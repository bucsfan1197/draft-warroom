"""
Player props — the sharpest public projection there is, and the one signal the tool can't backtest
because free historical prop lines don't exist. So this FORWARD-tests them instead: every refresh it
pulls current NFL player props, converts them to projected fantasy points, and archives them next to
the consensus projection. Once games are played it compares both to the truth and reports — honestly —
whether props actually beat consensus. Props never touch the app's projection until that verdict is
in and positive; until then they're context, exactly like Vegas and pace.

Setup (one time, free): create a free key at https://the-odds-api.com (500 req/mo, no card), then in
your repo add it as an Actions secret named ODDS_API_KEY. The refresh workflow passes it through; with
no key this whole module stays dormant and nothing changes.

CLI:  python tools_props.py --pull      (needs ODDS_API_KEY; pulls + archives this week's props)
      python tools_props.py --validate  (scores the archive against actuals -> props.json)
"""
import urllib.request, json, csv, io, re, os, time, statistics as st

HERE=os.path.dirname(os.path.abspath(__file__))
ARCHIVE=os.path.join(HERE,"props_archive.csv")
SPORT="americanfootball_nfl"
POS=("QB","RB","WR","TE")
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def gj(u,t=40):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r,timeout=t).read())

# PPR points per unit of each prop market. Yardage markets carry a line (point); the TD/anytime markets
# carry odds, converted to an implied probability and paid as expected TDs.
YARD={"player_pass_yds":0.04,"player_rush_yds":0.1,"player_reception_yds":0.1,"player_receptions":1.0}
TDLINE={"player_pass_tds":4.0}                       # line-based (e.g. 1.5 pass TDs)
PROB6={"player_anytime_td":6.0}                       # odds-based: P(scores) * 6
NEG={"player_pass_interceptions":-2.0}               # recognized if present, but not requested (budget)
# the-odds-api charges [markets x regions] PER EVENT. Budget math on the free 500 req/mo tier:
#   6 markets x 1 region x ~14 games = ~84 credits per pull; gated to ONE pull per NFL week (below) =
#   ~84 x at most 5 game-weeks/month = ~420/month worst case, well under 500. Interceptions dropped to
#   stay safe; validate() uses only free Sleeper/nflverse and costs nothing.
MARKETS=list(YARD)+list(TDLINE)+list(PROB6)          # 6 markets requested (INT omitted on purpose)

def american_to_prob(odds):
    try: o=float(odds)
    except Exception: return None
    return (100.0/(o+100.0)) if o>0 else ((-o)/((-o)+100.0))

def props_to_points(event_odds):
    """One event's odds JSON (the-odds-api shape) -> {player_norm: projected_ppr_points}."""
    acc={}   # name -> {market: value}
    for bk in event_odds.get("bookmakers",[]):
        for mk in bk.get("markets",[]):
            key=mk.get("key")
            if key not in MARKETS: continue
            for o in mk.get("outcomes",[]):
                who=norm(o.get("description") or o.get("name") or "")
                if not who: continue
                d=acc.setdefault(who,{})
                if key in PROB6:
                    if (o.get("name") or "").lower() in ("yes","over") or key=="player_anytime_td":
                        p=american_to_prob(o.get("price"))
                        if p is not None: d.setdefault(key,[]).append(p)
                else:
                    # yardage / TD-line / INT markets: take the OVER line (the median outcome)
                    if (o.get("name") or "").lower()=="over" and o.get("point") is not None:
                        d.setdefault(key,[]).append(float(o["point"]))
    pts={}
    for who,mk in acc.items():
        tot=0.0; used=0
        for k,w in YARD.items():
            if mk.get(k): tot+=st.median(mk[k])*w; used+=1
        for k,w in TDLINE.items():
            if mk.get(k): tot+=st.median(mk[k])*w; used+=1
        for k,w in NEG.items():
            if mk.get(k): tot+=st.median(mk[k])*w; used+=1
        for k,w in PROB6.items():
            if mk.get(k): tot+=st.mean(mk[k])*w; used+=1   # vig left in — conservative, and symmetric across players
        if used: pts[who]=round(tot,2)
    return pts

def pull_props(api_key):
    """Current NFL player props -> {player_norm: projected_ppr_points}. Empty dict if none posted."""
    ev=gj(f"https://api.the-odds-api.com/v4/sports/{SPORT}/events?apiKey={api_key}")
    out={}
    mstr=",".join(MARKETS)
    for e in (ev or [])[:16]:
        try:
            od=gj(f"https://api.the-odds-api.com/v4/sports/{SPORT}/events/{e['id']}/odds"
                  f"?apiKey={api_key}&regions=us&oddsFormat=american&markets={mstr}")
            for who,p in props_to_points(od).items():
                if who not in out: out[who]=p
        except Exception:
            continue
        time.sleep(0.3)
    return out

def _cur_season_week():
    # NFL week is derivable from the schedule, but for archiving we only need a monotonic (season,week)
    # stamp; the validator matches on it. Pull it from Sleeper's state so it's always right.
    try:
        s=gj("https://api.sleeper.app/v1/state/nfl")
        return int(s.get("season") or 0), int(s.get("week") or 0), (s.get("season_type") or "")
    except Exception:
        return 0,0,""

def archive_props(pts, season, week):
    new=not os.path.exists(ARCHIVE)
    with open(ARCHIVE,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(["season","week","player","prop_pts"])
        for who,p in sorted(pts.items()):
            w.writerow([season,week,who,p])

def _week_already_archived(season,week):
    if not os.path.exists(ARCHIVE): return False
    for r in csv.DictReader(open(ARCHIVE,encoding="utf-8")):
        try:
            if int(r["season"])==season and int(r["week"])==week: return True
        except Exception: continue
    return False

def pull_and_archive(api_key):
    season,week,stype=_cur_season_week()
    if stype!="regular" or not week:
        return {"status":"idle","note":"no regular-season week active"}
    # ONE pull per NFL week — the refresh runs 4x/day, but props for a week are stable and each pull
    # costs ~84 credits, so re-pulling every 6h would burn the free tier in a day. Snapshot once/week.
    if _week_already_archived(season,week):
        return {"status":"skip","note":f"week {week} already archived (1 pull/week to stay in free tier)"}
    pts=pull_props(api_key)
    if not pts: return {"status":"idle","note":"no props posted yet"}
    archive_props(pts,season,week)
    return {"status":"ok","players":len(pts),"season":season,"week":week}

def validate():
    """Score archived prop projections vs consensus against nflverse actuals -> props.json."""
    if not os.path.exists(ARCHIVE):
        out={"verdict":"accumulating","nWeeks":0,"note":"no props archived yet"}
        json.dump(out,open(os.path.join(HERE,"props.json"),"w"),indent=1); return out
    arch={}   # (season,week) -> {player: prop_pts}
    for r in csv.DictReader(open(ARCHIVE,encoding="utf-8")):
        try: s=int(r["season"]); w=int(r["week"]); p=float(r["prop_pts"])
        except Exception: continue
        arch.setdefault((s,w),{})[norm(r["player"])]=p
    rows=[]   # (season,week,prop,consensus,actual)
    seasons=sorted({s for s,_ in arch})
    for s in seasons:
        # actuals for the season
        try:
            raw=urllib.request.urlopen(urllib.request.Request(
                f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{s}.csv",
                headers={"User-Agent":"Mozilla/5.0"}),timeout=180).read().decode("utf8","replace")
        except Exception: continue
        act={}
        for r in csv.DictReader(io.StringIO(raw)):
            if (r.get("season_type") or "REG")!="REG": continue
            if r.get("position") not in POS: continue
            try: w=int(r["week"]); v=float(r.get("fantasy_points_ppr") or 0)
            except Exception: continue
            act[(w,norm(r.get("player_display_name") or ""))]=v
        for (as_,aw),players in arch.items():
            if as_!=s: continue
            posq="&".join(f"position[]={p}" for p in POS)
            try: d=gj(f"https://api.sleeper.com/projections/nfl/{s}/{aw}?season_type=regular&{posq}&order_by=pts_ppr")
            except Exception: d=[]
            cons={norm((it.get('player',{}).get('first_name','')+' '+it.get('player',{}).get('last_name','')).strip()):
                  (it.get('stats',{}) or {}).get('pts_ppr') for it in (d or [])}
            for who,pp in players.items():
                a=act.get((aw,who)); c=cons.get(who)
                if a is None or c is None: continue
                rows.append((s,aw,pp,float(c),a))
    nweeks=len({(s,w) for s,w,_,_,_ in rows})
    if len(rows)<100 or nweeks<3:
        out={"verdict":"accumulating","nWeeks":nweeks,"nPlayerWeeks":len(rows),
             "note":f"need more data — have {nweeks} week(s), {len(rows)} player-weeks (min 3 weeks / 100)"}
        json.dump(out,open(os.path.join(HERE,"props.json"),"w"),indent=1); return out
    mae=lambda f: sum(abs(f(r)-r[4]) for r in rows)/len(rows)
    maeP=mae(lambda r:r[2]); maeC=mae(lambda r:r[3])
    # per-week: do props beat consensus in EVERY week banked?
    weeks=sorted({(s,w) for s,w,_,_,_ in rows}); wins=0
    for (s,w) in weeks:
        sub=[r for r in rows if r[0]==s and r[1]==w]
        mp=sum(abs(r[2]-r[4]) for r in sub)/len(sub); mc=sum(abs(r[3]-r[4]) for r in sub)/len(sub)
        if mp<mc: wins+=1
    out={"verdict":"props beat consensus" if maeP<maeC and wins==len(weeks) else "consensus holds",
         "nWeeks":nweeks,"nPlayerWeeks":len(rows),
         "maeProps":round(maeP,3),"maeConsensus":round(maeC,3),
         "oosPct":round((maeP-maeC)/maeC*100,3),"weeksPropsWin":wins,"weeksTotal":len(weeks),
         "beatsEveryWeek":wins==len(weeks) and maeP<maeC}
    json.dump(out,open(os.path.join(HERE,"props.json"),"w"),indent=1); return out

if __name__=="__main__":
    import sys
    if "--pull" in sys.argv:
        k=os.environ.get("ODDS_API_KEY")
        print(json.dumps(pull_and_archive(k) if k else {"status":"dormant","note":"no ODDS_API_KEY"}))
    if "--validate" in sys.argv or "--pull" not in sys.argv:
        print(json.dumps(validate()))

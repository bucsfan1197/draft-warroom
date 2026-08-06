#!/usr/bin/env python3
"""
Draft War Room — data refresher.
Pulls fresh ADP + projections + injuries from every free source, rebuilds data.js,
and pushes it to your GitHub repo (which auto-updates your live site).

Run it and leave it: it refreshes every REFRESH_HOURS and pushes only when data changed.
    python refresh.py

First run downloads a bit; after that each cycle is ~30-60s.
Requires: pip install pandas numpy   (git must be installed and the repo already set up)
"""
import urllib.request, urllib.error, json, re, time, subprocess, os, sys, traceback, csv

# ---------- config ----------
HERE      = os.path.dirname(os.path.abspath(__file__))
SEASON    = "2026"
REFRESH_HOURS = 6            # how often to re-pull + push
GIT_PUSH  = True            # set False to just write data.js locally without pushing
# ----------------------------

def log(*a):
    msg=time.strftime("[%H:%M:%S] ")+" ".join(str(x) for x in a)
    try: print(msg, flush=True)
    except UnicodeEncodeError: print(msg.encode("ascii","replace").decode("ascii"), flush=True)
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def get(url, headers=None, timeout=40):
    req=urllib.request.Request(url, headers=headers or {"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()
def getj(url, headers=None, timeout=40): return json.loads(get(url,headers,timeout))

SCORING={"py":0.04,"ptd":4,"int":-2,"ry":0.1,"rtd":6,"rec":1,"recy":0.1,"rectd":6,"fl":-2}
EPOS={1:"QB",2:"RB",3:"WR",4:"TE",5:"K",16:"DST"}
NICK={"Cardinals":"ARI","Falcons":"ATL","Ravens":"BAL","Bills":"BUF","Panthers":"CAR","Bears":"CHI","Bengals":"CIN","Browns":"CLE","Cowboys":"DAL","Broncos":"DEN","Lions":"DET","Packers":"GB","Texans":"HOU","Colts":"IND","Jaguars":"JAX","Chiefs":"KC","Raiders":"LV","Chargers":"LAC","Rams":"LAR","Dolphins":"MIA","Vikings":"MIN","Patriots":"NE","Saints":"NO","Giants":"NYG","Jets":"NYJ","Eagles":"PHI","Steelers":"PIT","49ers":"SF","Seahawks":"SEA","Buccaneers":"TB","Titans":"TEN","Commanders":"WAS"}
ALIAS={"LA":"LAR","STL":"LAR","SD":"LAC","OAK":"LV","WSH":"WAS","JAC":"JAX"}
def std(t): return ALIAS.get(t,t)

# ---------- live pulls ----------
BASE=json.load(open(os.path.join(HERE,"base.json"),encoding="utf-8"))

def pull_ffc():
    out={}; drafts=0
    for fmt in ("ppr","2qb"):
        try:
            d=getj(f"https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams=12&year={SEASON}")
            if fmt=="ppr": drafts=(d.get("meta") or {}).get("total_drafts",0)
            for p in d.get("players",[]):
                pos={"DEF":"DST","PK":"K","D/ST":"DST"}.get(p["position"],p["position"])  # FFC uses DEF/PK; tool expects DST/K
                k=norm(p["name"]); e=out.setdefault(k,{"name":p["name"],"pos":pos,"team":std(p.get("team",""))})
                e["adp" if fmt=="ppr" else "adpSf"]=round(float(p["adp"]),1)
                # FFC publishes the MEASURED spread of where each player actually goes, plus how
                # many drafts it's based on. The app used to guess this (0.28 x adp); the real
                # figure is ~0.106 x adp and far tighter at the top — Bijan's is 0.7, not 5.
                if fmt=="ppr":
                    if p.get("stdev") is not None:
                        try: e["adpSd"]=round(float(p["stdev"]),2)
                        except Exception: pass
                    if p.get("times_drafted"): e["adpN"]=int(p["times_drafted"])
                    if p.get("high"): e["adpHi"]=int(p["high"])
                    if p.get("low"):  e["adpLo"]=int(p["low"])
        except Exception as ex: log("  FFC",fmt,"fail:",ex)
    for v in out.values(): v.setdefault("adp",v.get("adpSf",999)); v.setdefault("adpSf",v.get("adp",999))
    log(f"  FFC: {len(out)} players")
    return out, drafts

def pull_sleeper_players():
    out={}
    try:
        d=getj("https://api.sleeper.app/v1/players/nfl", timeout=90)
        for pid,p in d.items():
            if p.get("position") not in ("QB","RB","WR","TE","K"): continue
            nm=p.get("full_name") or ""
            if not nm: continue
            bp=p.get("injury_body_part")
            out[norm(nm)]={"name":nm,"pos":p.get("position"),"age":p.get("age"),"team":p.get("team"),
                           "inj":p.get("injury_status"),"depth":p.get("depth_chart_order"),"sid":str(pid),
                           # a real anatomical body part when Sleeper has one — the browser overlays
                           # ESPN's live status on top, but this is the baked baseline so the injury
                           # tab reads sensibly even before the live feed lands (or if it's offline).
                           "injPart":(bp if bp and bp!="Undisclosed" else None),
                           "injNews":p.get("news_updated")}
        log(f"  Sleeper players: {len(out)}")
    except Exception as ex: log("  Sleeper players fail:",ex)
    return out

def pull_sleeper_weekly():
    out={}
    posq="&".join(f"position[]={p}" for p in ["QB","RB","WR","TE","K","DEF"])
    for w in range(1,19):
        try:
            d=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}/{w}?season_type=regular&{posq}&order_by=pts_ppr")
            for it in d:
                pl=it.get("player",{}); pos=pl.get("position"); pos="DST" if pos=="DEF" else pos
                if pos not in ("QB","RB","WR","TE","K","DST"): continue
                key=(it.get("player_id") if pos=="DST" else norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()))
                if not key: continue
                pts=(it.get("stats") or {}).get("pts_ppr")
                e=out.setdefault((pos,key),{"wk":[0.0]*19})
                if pts is not None: e["wk"][w]=round(float(pts),2)
            time.sleep(0.2)
        except Exception as ex: log("  Sleeper wk",w,"fail:",ex)
    """Correct the measured bias before anything downstream sees it.

    tools_beat.py compared Sleeper's own archived weekly projections against nflverse actuals,
    fitted a per-position offset on 2021-23 and tested it on 2024 without ever fitting to it:
    mean absolute error 5.285 -> 5.210 and correlation 0.553 -> 0.560. Small, but real, free, and
    in the right direction. Quarterbacks carry nearly all of it — projected about four points a
    week too high, which an older independent calibration also found at -4.6 a season, so two
    separate measurements agree on the sign and rough size.

    Shrinking projections toward the positional mean was tested at the same time and made things
    WORSE out of sample (+0.57%), so it is deliberately not applied. Weekly consensus turns out
    not to be over-dispersed, which is the opposite of the textbook expectation and the reason
    this is applied rather than assumed.

    NOTE: the correction is now applied to the BLENDED consensus in cons() (build_data), not here.
    Correcting only Sleeper let roughly half of it wash out in the 50/50 ESPN average, so the shipped
    projection carried the validated offset at half strength. QB over-projection is a cross-provider
    phenomenon (two independent measurements agree on sign), so it belongs on the blend. Sleeper stays
    RAW at this stage so the blend is corrected exactly once."""
    for e in out.values(): e["season"]=round(sum(e["wk"][1:19]),1); e["wk"]=e["wk"][1:19]
    log(f"  Sleeper weekly: {len(out)}")
    return out

def _espn_call(filt):
    hdr={"X-Fantasy-Filter":json.dumps({"players":filt}),"User-Agent":"Mozilla/5.0","Accept":"application/json"}
    return getj(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leaguedefaults/3?view=kona_player_info", hdr)

def pull_espn():
    proj={}; adp={}
    calls=[{"limit":350,"sortDraftRanks":{"sortPriority":100,"sortAsc":True,"value":"PPR"}},
           {"limit":80,"filterSlotIds":{"value":[17,16]},"sortPercOwned":{"sortPriority":1,"sortAsc":False}}]
    for filt in calls:
        try:
            d=_espn_call(filt)
            for x in d.get("players",[]):
                p=x["player"]; pos=EPOS.get(p.get("defaultPositionId"))
                if not pos: continue
                key=NICK.get(p["fullName"].split(" D/ST")[0]) if pos=="DST" else norm(p["fullName"])
                if not key: continue
                st=[s for s in p.get("stats",[]) if s.get("seasonId")==int(SEASON) and s.get("statSourceId")==1]
                seas=next((s["appliedTotal"] for s in st if s.get("scoringPeriodId")==0),None)
                wk=[0.0]*19
                for s in st:
                    sp=s.get("scoringPeriodId")
                    if isinstance(sp,int) and 1<=sp<=18: wk[sp]=round(s.get("appliedTotal",0),2)
                if seas is not None: proj[(pos,key)]={"season":round(seas,1),"wk":wk[1:19]}
                o=(p.get("ownership") or {}).get("averageDraftPosition")
                sfr=(p.get("draftRanksByRankType") or {}).get("SUPERFLEX",{}).get("rank")
                adp[(pos,key)]={"s":round(o,1) if o and o>0 else None,"sf":sfr}
        except Exception as ex: log("  ESPN fail:",ex)
    log(f"  ESPN: {len(proj)} proj / {len(adp)} adp")
    return proj, adp

def pull_yahoo():
    out={}
    for start in range(0,340,25):
        try:
            d=getj(f"https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/game/nfl/players;start={start};count=25;sort=AR;out=draft_analysis?format=json")
            pl=d["fantasy_content"]["game"][1]["players"]; n=pl.get("count",0)
            for i in range(n):
                arr=pl[str(i)]["player"]; attrs=arr[0]
                name=next((a["name"]["full"] for a in attrs if isinstance(a,dict) and "name" in a),None)
                da=next((x["draft_analysis"] for x in arr[1:] if isinstance(x,dict) and "draft_analysis" in x),None)
                ap=None
                if da: ap=next((float(z["average_pick"]) for z in da if "average_pick" in z and z["average_pick"] not in ("-","0.0")),None)
                if name and ap: out[norm(name)]=round(ap,1)
            time.sleep(0.2)
            if n<25: break
        except Exception as ex: log("  Yahoo",start,"fail:",ex); break
    log(f"  Yahoo: {len(out)}")
    return out

def pull_fantasypros_ecr():
    # Expert Consensus Rank (aggregate of 100+ analysts), from FantasyPros' public rankings pages.
    # Personal use, one request per source per refresh cycle.
    out={}
    pages=[("ecr","ppr-cheatsheets"),("ecrSf","superflex-cheatsheets"),("ecrDyn","dynasty-overall")]
    for key,slug in pages:
        try:
            t=get(f"https://www.fantasypros.com/nfl/rankings/{slug}.php").decode("utf-8","replace")
            m=re.search(r'var\s+ecrData\s*=\s*(\{.*?\});',t,re.S)
            if not m: log(f"  FantasyPros {key}: no data block"); continue
            n=0
            for p in json.loads(m.group(1)).get("players",[]):
                nm=p.get("player_name"); rk=p.get("rank_ecr")
                if not nm or not rk: continue
                out.setdefault(norm(nm),{})[key]=int(rk); n+=1
            time.sleep(0.4)
        except Exception as ex: log(f"  FantasyPros {key} fail:",ex)
    log(f"  FantasyPros ECR: {len(out)}")
    return out

def pull_sleeper_adp():
    # Sleeper ADP lives in the PUBLIC season-projections payload (no auth).
    # stats.adp_ppr (1QB), adp_2qb (superflex), adp_dynasty_ppr (dynasty). 999 = undrafted.
    out={}
    try:
        d=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}?season_type=regular", timeout=60)
        for it in d:
            st=it.get("stats") or {}
            ppr=st.get("adp_ppr")
            if ppr is None or ppr>=900: continue
            pl=it.get("player") or {}
            nm=((pl.get("first_name") or "")+" "+(pl.get("last_name") or "")).strip()
            if not nm: continue
            e={"ppr":round(float(ppr),1)}
            for src,dst in (("adp_2qb","sf"),("adp_dynasty_ppr","dyn"),("adp_dynasty_2qb","dynSf")):
                v=st.get(src)
                if v is not None and v<900: e[dst]=round(float(v),1)
            out[norm(nm)]=e
        log(f"  Sleeper ADP: {len(out)}")
    except Exception as ex: log("  Sleeper ADP fail:",ex)
    return out

# ---------- build ----------
def build_stats(pos, C, templ):
    if pos in ("K","DST"): return {"p": round(C,1)}
    t=templ.get(pos,{}); intf=t.get("int_ppr",0.0)
    posP = C/(1+intf) if (1+intf)>0 else C
    stats={}
    for k,frac in t.items():
        if k=="int_ppr" or frac<=0: continue
        stats[k]=round(posP*frac/SCORING[k],1)
    if intf<0: stats["int"]=round((intf*posP)/SCORING["int"],1)
    stats["fl"]= 2 if pos=="QB" else 1
    return stats

def dist_for(pos, rank, FACT, BANDW):
    d=FACT.get(pos)
    if not d: return None
    b=max(1,int(-(-rank//BANDW)))  # ceil
    while b>0 and str(b) not in d: b-=1
    return d.get(str(b)) or d.get(min(d,key=lambda x:int(x)))

def byes_from_sched(SCHED):
    out={}
    for t,arr in SCHED.items():
        for w,opp in enumerate(arr):
            if opp is None: out[t]=w+1; break
    return out

# ---- IDP: individual defensive players ----
# Sleeper is the only free source that projects these, and it carries the exact stat lines
# fantasy scores off (solo/assist tackles, sacks, interceptions, forced fumbles) plus a real
# IDP ADP. Granular NFL positions are bucketed into the three slots leagues actually use.
IDP_BUCKET={"DE":"DL","DT":"DL","DL":"DL","NT":"DL",
            "LB":"LB","OLB":"LB","ILB":"LB","MLB":"LB",
            "DB":"DB","CB":"DB","S":"DB","SS":"DB","FS":"DB"}
IDP_STATS=["idp_tkl_solo","idp_tkl_ast","idp_sack","idp_int","idp_ff","idp_fum_rec",
           "idp_safe","idp_pass_def","idp_blk_kick","idp_def_td","idp_tkl_loss"]
IDP_TARGET=260          # deep enough for a 12-team league starting 6-7 defenders

def pull_idp():
    try:
        rows=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}?season_type=regular")
    except Exception as ex:
        log("  IDP season pull failed:",ex); return []
    out=[]
    for r in rows:
        pl=r.get("player") or {}
        b=IDP_BUCKET.get(pl.get("position"))
        if not b: continue
        st=r.get("stats") or {}
        line={k:round(float(st[k]),2) for k in IDP_STATS if st.get(k) not in (None,"")}
        if not line: continue
        name=((pl.get("first_name") or "")+" "+(pl.get("last_name") or "")).strip()
        if not name: continue
        adp=st.get("adp_idp") or st.get("adp_idp_1qb")
        out.append({"name":name,"pos":b,"posDetail":pl.get("position"),
                    "team":std(pl.get("team") or ""),"sid":str(r.get("player_id") or ""),
                    "idp":line,"gp":st.get("gp"),
                    "adp":float(adp) if adp not in (None,"",999) else None})
    # rank by a neutral yardstick so the pool is the players who actually matter
    def rough(x):
        l=x["idp"]
        return (l.get("idp_tkl_solo",0)*1.5+l.get("idp_tkl_ast",0)*0.75+l.get("idp_sack",0)*4
                +l.get("idp_int",0)*6+l.get("idp_ff",0)*4+l.get("idp_fum_rec",0)*2)
    out.sort(key=rough,reverse=True)
    out=out[:IDP_TARGET]
    log(f"  IDP: {len(out)} defenders ({sum(1 for x in out if x['adp'])} with IDP ADP)")
    return out

def pull_idp_weekly():
    """Week-by-week IDP projections, so the simulator and in-season tools work for defenders."""
    out={}
    posq="&".join(f"position[]={p}" for p in ["DL","LB","DB","DE","DT","CB","S","OLB","ILB","SS","FS","NT"])
    for w in range(1,19):
        try:
            d=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}/{w}?season_type=regular&{posq}")
            for it in d:
                pl=it.get("player") or {}
                if not IDP_BUCKET.get(pl.get("position")): continue
                st=it.get("stats") or {}
                line={k:float(st[k]) for k in IDP_STATS if st.get(k) not in (None,"")}
                if not line: continue
                key=str(it.get("player_id") or "")
                if not key: continue
                out.setdefault(key,{"wk":[None]*19})["wk"][w]=line
            time.sleep(0.2)
        except Exception as ex: log("  IDP wk",w,"fail:",ex)
    for e in out.values(): e["wk"]=e["wk"][1:19]
    log(f"  IDP weekly: {len(out)}")
    return out

def pull_market():
    """Blended free-market trade values (KeepTradeCut + FantasyCalc + DynastyProcess)."""
    try:
        import market_values
        m=market_values.build_market()
        if m: log(f"  market: {len(m['players'])} players, {len(m['picks'])} pick slots")
        return m
    except Exception as ex:
        log("  market pull failed:",ex); return None

def pull_kickoffs():
    """Kickoff date and time for every team, every week, from nflverse's schedule file.

    SCHED in base.json says WHO each team plays but not WHEN, which is why the matchup tab could
    never draw a win-probability line across the week: resolving games in order would have meant
    inventing a running order. This supplies the real one.

    Times are US Eastern, exactly as the NFL publishes them, and are stored as a naive
    "YYYY-MM-DDTHH:MM" string rather than an epoch. That is deliberate — the browser parses a
    string in that form as LOCAL time, so the weekday and the clock face come out identical for
    every viewer instead of a Thursday night game showing up as Friday for someone in Europe. The
    ordering we actually sort on is unaffected either way, since every kickoff shifts together.
    """
    url="https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
    try:
        import csv, io
        raw=get(url, timeout=60).decode("utf-8","replace")
        rows=[r for r in csv.DictReader(io.StringIO(raw))
              if r.get("season")==SEASON and r.get("game_type")=="REG"]
        if not rows:
            log(f"  kickoffs: nflverse has no REG rows for {SEASON} yet — drift chart stays off")
            return None
        KICK={}; VEGAS={}
        missing=0; vhit=0
        for r in rows:
            try: wk=int(r["week"])
            except (TypeError,ValueError): continue
            if not 1<=wk<=18: continue
            home,away=std((r.get("home_team") or "").strip()),std((r.get("away_team") or "").strip())
            day, tm = (r.get("gameday") or "").strip(), (r.get("gametime") or "").strip()
            if day and tm:
                for t in (home,away):
                    if t: KICK.setdefault(t,[None]*18)[wk-1]=f"{day}T{tm}"
            else:
                missing+=1
            # Vegas game environment: implied team total = total/2 +/- spread/2. spread_line > 0 == home
            # favored. Stored as raw market context for the UI — validated (tools_vegas.py) NOT to beat the
            # consensus projection out of sample, because consensus already prices the line, so it is shown
            # as game script, never folded into a player's points.
            # weather / dome, also straight from games.csv. roof is known when the schedule drops; wind and
            # temp are filled closer to kickoff. Dome/closed roof = no weather risk (a passing/kicking tailwind);
            # high wind suppresses the deep ball and field goals. Shown as context alongside the line.
            roof=(r.get("roof") or "").strip().lower()
            dome=1 if roof in ("dome","closed") else 0
            try: wind=int(float(r.get("wind"))) if (r.get("wind") or "").strip() not in ("","NA") else None
            except (TypeError,ValueError): wind=None
            wx={}
            if dome: wx["dome"]=1
            if wind is not None and wind>=1: wx["wind"]=wind
            try:
                tot=float(r["total_line"]); spr=float(r["spread_line"])
                he={"itt":round(tot/2+spr/2,1),"tot":tot,"line":round(-spr,1),"opp":away,"home":1}
                ae={"itt":round(tot/2-spr/2,1),"tot":tot,"line":round(spr,1),"opp":home,"home":0}
                he.update(wx); ae.update(wx)
                if home: VEGAS.setdefault(home,[None]*18)[wk-1]=he
                if away: VEGAS.setdefault(away,[None]*18)[wk-1]=ae
                vhit+=1
            except Exception: pass
        filled=sum(1 for arr in KICK.values() for v in arr if v)
        log(f"  kickoffs: {len(rows)} games, {len(KICK)} teams, {filled} team-weeks"
            +(f", {missing} games still without a time" if missing else "")
            +f" · vegas lines on {vhit} games")
        return (KICK if len(KICK)>=28 else None), (VEGAS if vhit>=10 else None)
    except Exception as ex:
        log("  kickoff pull failed:",ex); return None, None

def pull_usage_week():
    """Season-to-date OPPORTUNITY per player — targets, carries, receptions per game, plus a recent-form
    read — from nflverse weekly stats for the CURRENT season.

    tools_usage.py measured that recent usage adds a small, every-fold-positive improvement to the
    consensus WEEKLY projection out of sample — but a shrinking one (barely moves against 2024 consensus,
    which now prices usage itself). So this is deliberately NOT folded into a player's points, the same
    call as the betting line: it's surfaced as decision context, because opportunity is sticky and drives
    start/sit and waiver calls even where it no longer beats the point projection. In the pre-season there
    are no games, so this comes back empty and every usage panel stays dormant until Week 1 is on the board.
    """
    url=f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{SEASON}.csv"
    try:
        import csv, io
        raw=get(url, timeout=90).decode("utf-8","replace")
    except Exception as ex:
        log(f"  usage: no {SEASON} weekly stats yet ({ex.__class__.__name__}) — usage panels stay dormant")
        return {}
    perp={}   # norm name -> list[(week, pos, pts, tgt, car, rec)]
    try:
        for r in csv.DictReader(io.StringIO(raw)):
            if (r.get("season_type") or "REG")!="REG": continue
            pos=r.get("position")
            if pos not in ("QB","RB","WR","TE"): continue
            try: w=int(r["week"])
            except Exception: continue
            fn=lambda k:(float(r.get(k) or 0))
            perp.setdefault(norm(r.get("player_display_name") or ""),[]).append(
                (w,pos,fn("fantasy_points_ppr"),fn("targets"),fn("carries"),fn("receptions")))
    except Exception as ex:
        log("  usage parse failed:",ex); return {}
    out={}
    for nm,lst in perp.items():
        if not lst: continue
        lst.sort(); g=len(lst)
        tgt=sum(x[3] for x in lst)/g; car=sum(x[4] for x in lst)/g; rec=sum(x[5] for x in lst)/g
        ppg=sum(x[2] for x in lst)/g
        last3=lst[-3:]; r3=sum(x[2] for x in last3)/len(last3)
        out[nm]={"g":g,"tgt":round(tgt,1),"car":round(car,1),"rec":round(rec,1),
                 "ppg":round(ppg,1),"r3":round(r3,1),"pos":lst[-1][1]}
    weeks=max((x[0] for lst in perp.values() for x in lst), default=0)
    log(f"  usage: {len(out)} players through week {weeks} of {SEASON}")
    return out

def _stats_week_url(yr): return f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv"
def _ff(v):
    try: return float(v or 0)
    except (TypeError,ValueError): return 0.0

def pull_dvp():
    """Defense-vs-position, computed from real results instead of a static snapshot.

    DvP is the single most important matchup input in the app — it drives the win-probability sim, the
    schedule grade, the weekly matchup grid and strength-of-schedule everywhere. It was a fixed table baked
    into base.json (compressed to ~0.90-1.05, no provenance), which meant every matchup number was only as
    good as one old snapshot. This recomputes it every cycle from nflverse: for each defense and position,
    the fantasy points it actually allowed per game versus the league average. Recency-weighted across the
    current season (weighted up as it accrues) and the two prior years, then regressed 30% toward league
    average because a single season of DvP is noisy. Standard-industry DvP, kept live.
    """
    import io as _io
    from collections import defaultdict
    POS=("QB","RB","WR","TE")
    def year_dvp(yr):
        try: raw=get(_stats_week_url(yr), timeout=120).decode("utf-8","replace")
        except Exception: return None,0
        sumTP=defaultdict(float); gamesTeam=defaultdict(set); n=0
        for r in csv.DictReader(_io.StringIO(raw)):
            if (r.get("season_type") or "REG")!="REG": continue
            if r.get("position") not in POS: continue
            opp=std(r.get("opponent_team")); gid=r.get("game_id")
            if not opp or not gid: continue
            sumTP[(opp,r["position"])]+=_ff(r.get("fantasy_points_ppr")); gamesTeam[opp].add(gid); n+=1
        if n<200: return None,0
        posV=defaultdict(list); perGame={}
        for (team,pos),tot in sumTP.items():
            g=len(gamesTeam[team]) or 1; pg=tot/g; perGame[(team,pos)]=pg; posV[pos].append(pg)
        la={pos:(sum(v)/len(v) if v else 1) for pos,v in posV.items()}
        weeks=max((len(s) for s in gamesTeam.values()),default=0)
        return {tp:pg/(la[tp[1]] or 1) for tp,pg in perGame.items()}, weeks
    cy=int(SEASON)
    plan=[(str(cy),1.5),(str(cy-1),0.9),(str(cy-2),0.45)]
    blend=defaultdict(float); wsum=defaultdict(float); used=[]
    for yr,w in plan:
        d,weeks=year_dvp(yr)
        if not d: continue
        ww=w*(min(weeks,17)/17.0) if yr==str(cy) else w   # current season counts more as it plays out
        if ww<=0: continue
        used.append(f"{yr}×{round(ww,2)}")
        for tp,v in d.items(): blend[tp]+=ww*v; wsum[tp]+=ww
    teams=set(t for t,_ in blend);
    if len(teams)<28:
        log("  dvp: not enough nflverse data — keeping static DVP from base.json"); return None
    # SHRINK regresses each DvP toward league average. Was 0.70 (a guess); tools_validate_dvp.py backtested
    # it two ways and 0.70 was too aggressive — it made a naive projection WORSE (+0.2% in 2023, +1.2% in 2024),
    # because DvP is only weakly persistent and consensus already prices most of the matchup. The optimal
    # out-of-sample shrink was ~0.30 (2023) / ~0.00 (2024), so 0.35 is the honest setting: near-neutral for
    # prediction while keeping a modest, real spread for the schedule grade and matchup grid. DvP is a small
    # directional signal, and it's now sized like one.
    SHRINK=0.35
    DVP=defaultdict(dict)
    for tp,v in blend.items():
        raw=v/(wsum[tp] or 1); DVP[tp[0]][tp[1]]=round(1+(raw-1)*SHRINK,3)
    wr=sorted((v.get("WR",1),t) for t,v in DVP.items())
    log(f"  dvp: computed from {', '.join(used)} — {len(DVP)} defenses "
        f"(toughest WR {wr[0][1]} {wr[0][0]}, softest {wr[-1][1]} {wr[-1][0]})")
    return dict(DVP)

def pull_advanced():
    """Real opportunity per player — the stuff that actually predicts fantasy scoring: target share, air-yards
    share, WOPR, snap share, and targets/carries per game. The Grades 'Opportunity' axis was a crude proxy
    (projected yards ÷ a yards-per-touch constant); this replaces it with measured usage. In-season it reads
    the current year; in the pre-season it falls back to the last complete season as a role prior — a receiver
    who ran a 27% target share last year is a known high-volume asset before a single 2026 snap is played.
    """
    import io as _io
    from collections import defaultdict
    POS=("QB","RB","WR","TE")
    def load_stats(yr):
        try: return list(csv.DictReader(_io.StringIO(get(_stats_week_url(yr),timeout=120).decode("utf-8","replace"))))
        except Exception: return []
    def load_snaps(yr):
        snaps=defaultdict(lambda:[0.0,0])
        try:
            for r in csv.DictReader(_io.StringIO(get(f"https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{yr}.csv",timeout=120).decode("utf-8","replace"))):
                if (r.get("game_type") or "REG")!="REG": continue
                if r.get("position") not in POS: continue
                s=snaps[norm(r.get("player") or "")]; s[0]+=_ff(r.get("offense_pct")); s[1]+=1
        except Exception: pass
        return snaps
    cy=int(SEASON); cur=load_stats(str(cy))
    curReg=[r for r in cur if (r.get("season_type") or "REG")=="REG"]
    use=str(cy) if len(curReg)>=150 else str(cy-1)
    rows=curReg if use==str(cy) else load_stats(use)
    snaps=load_snaps(use)
    agg=defaultdict(lambda:{"g":0,"tgt":0.0,"car":0.0,"ts":0.0,"ays":0.0,"wopr":0.0,"ay":0.0})
    for r in rows:
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        a=agg[norm(r.get("player_display_name") or "")]; a["g"]+=1
        a["tgt"]+=_ff(r.get("targets")); a["car"]+=_ff(r.get("carries")); a["ts"]+=_ff(r.get("target_share"))
        a["ays"]+=_ff(r.get("air_yards_share")); a["wopr"]+=_ff(r.get("wopr")); a["ay"]+=_ff(r.get("receiving_air_yards"))
    out={}
    for k,a in agg.items():
        if a["g"]<1: continue
        g=a["g"]; s=snaps.get(k)
        out[k]={"g":a["g"],"tgt":round(a["tgt"]/g,1),"car":round(a["car"]/g,1),"ts":round(a["ts"]/g,3),
                "ays":round(a["ays"]/g,3),"wopr":round(a["wopr"]/g,3),"aypg":round(a["ay"]/g,1),
                "snap":(round(s[0]/s[1],3) if s and s[1] else None)}
    log(f"  advanced: {len(out)} players' real usage from {use} ({'current season' if use==str(cy) else 'last season as pre-season prior'})")
    return {"year":use,"current":use==str(cy),"players":out}

def pull_injury_reports():
    """Official NFL injury reports — practice participation (DNP / Limited / Full) plus the game-status
    designation — from nflverse, straight off the league's Wednesday-Friday reports.

    Practice participation is the read beat writers act on: a starter who Did Not Practice Wed and Thu is far
    more likely to sit than one who was a Full go, and you know it days before the Sunday inactive list. The
    live ESPN feed already carries the STATUS; this adds the practice detail behind it. In-season only — empty
    in the pre-season, like the other weekly feeds — and it overlays onto the Injuries tab and every
    injury-aware tool the moment Week 1 reports drop.
    """
    url=f"https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{SEASON}.csv"
    try: raw=get(url,timeout=90).decode("utf-8","replace")
    except Exception as ex:
        log(f"  injury reports: no {SEASON} data yet ({ex.__class__.__name__}) — dormant until in-season"); return {}
    import io as _io
    PMAP={"did not participate in practice":"DNP","limited participation in practice":"LP","full participation in practice":"FP"}
    latest={}
    try:
        for r in csv.DictReader(_io.StringIO(raw)):
            if (r.get("season_type") or "REG")!="REG": continue
            nm=norm(r.get("full_name") or "")
            if not nm: continue
            try: w=int(r.get("week") or 0)
            except (TypeError,ValueError): w=0
            if nm in latest and latest[nm][0]>=w: continue
            pr=(r.get("practice_status") or "").strip().lower()
            latest[nm]=(w,{"status":(r.get("report_status") or "").strip() or None,
                           "inj":(r.get("report_primary_injury") or r.get("practice_primary_injury") or "").strip() or None,
                           "practice":PMAP.get(pr),"week":w})
    except Exception as ex:
        log("  injury reports parse failed:",ex); return {}
    out={nm:rec for nm,(w,rec) in latest.items() if rec.get("status") or rec.get("practice")}
    wk=max((w for w,_ in latest.values()),default=0)
    log(f"  injury reports: {len(out)} players through week {wk} of {SEASON}")
    return out

def pull_accuracy():
    """In-season self-monitoring — how accurate have the projections ACTUALLY been this year?

    Everything else here is a forecast; this is the receipt. It scores the consensus weekly projection
    against real results as the season plays out (MAE per position) and re-measures the bias per position,
    which is an early warning if the shipped bias correction drifts. Honest accountability, shown in the app.
    Dormant in the pre-season — no games to score — like the other weekly feeds.
    """
    import io as _io, statistics as _st
    POS=("QB","RB","WR","TE")
    try: raw=get(_stats_week_url(SEASON),timeout=90).decode("utf-8","replace")
    except Exception:
        log("  accuracy: no current-season actuals yet — dormant"); return None
    act={}
    for r in csv.DictReader(_io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        try: w=int(r["week"])
        except Exception: continue
        act[(norm(r.get("player_display_name") or ""),r["position"],w)]=_ff(r.get("fantasy_points_ppr"))
    weeks=sorted({w for (_,_,w) in act})
    if not weeks: log("  accuracy: no played weeks yet — dormant"); return None
    posq="&".join(f"position[]={p}" for p in POS)
    rows=[]
    for w in weeks:
        try: d=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}/{w}?season_type=regular&{posq}&order_by=pts_ppr",timeout=60)
        except Exception: continue
        for it in d or []:
            pl=it.get("player") or {}; p=pl.get("position")
            if p not in POS: continue
            pr=(it.get("stats") or {}).get("pts_ppr")
            if pr is None or pr<3: continue
            key=(norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()),p,w)
            if key in act: rows.append((p,float(pr),act[key]))
    if len(rows)<100:
        log(f"  accuracy: only {len(rows)} scored player-weeks — too few, dormant"); return None
    mae=sum(abs(pr-a) for _,pr,a in rows)/len(rows)
    corr=None
    try:
        pj=[pr for _,pr,_ in rows]; ac=[a for _,_,a in rows]
        mp,ma=_st.mean(pj),_st.mean(ac)
        cov=sum((x-mp)*(y-ma) for x,y in zip(pj,ac)); sp=(sum((x-mp)**2 for x in pj))**.5; sa=(sum((y-ma)**2 for y in ac))**.5
        corr=round(cov/(sp*sa),3) if sp and sa else None
    except Exception: pass
    bias={p:round(_st.mean([a-pr for pp,pr,a in rows if pp==p]),2) for p in POS if any(pp==p for pp,_,_ in rows)}
    maeByPos={p:round(sum(abs(pr-a) for pp,pr,a in rows if pp==p)/max(1,sum(1 for pp,_,_ in rows if pp==p)),2) for p in POS}
    log(f"  accuracy: scored {len(rows)} player-weeks through wk {max(weeks)} — consensus MAE {mae:.2f}, corr {corr}")
    return {"weeks":max(weeks),"n":len(rows),"mae":round(mae,2),"corr":corr,"bias":bias,"maeByPos":maeByPos}

def archive_projections(players):
    """Snapshot what we projected, before the games are played.

    The whole reason "can we beat consensus" went unanswered for so long is that nobody keeps the
    forecast — you can always find out what happened, never what was predicted. Sleeper happens to
    serve its own history, which is how tools_beat.py got a test at all, but that is luck and it is
    one provider. This keeps our own, weekly, so that in a season's time there is a real archive to
    fit and test against instead of borrowing someone else's.

    Append-only, one row per player per snapshot. Small: a few hundred KB a season.
    """
    try:
        path=os.path.join(HERE,"proj_archive.csv")
        new=not os.path.exists(path)
        stamp=time.strftime("%Y-%m-%d")
        with open(path,"a",newline="",encoding="utf-8") as f:
            w=csv.writer(f)
            if new: w.writerow(["date","season","name","pos","team","proj","espn","sleeper","adp"])
            for p in players:
                c=p.get("cons") or {}
                w.writerow([stamp,SEASON,p["name"],p["pos"],p.get("team",""),
                            round(sum(p.get("wk") or [0]),1) if p.get("wk") else "",
                            c.get("e",""),c.get("k",""),p.get("adp","")])
        log(f"  archived {len(players)} projections -> proj_archive.csv")
    except Exception as ex:
        log("  archive failed:",ex)

def load_prev():
    """The last data.js we shipped, so a source that dies this cycle can fall back to its last-good
    value instead of blanking out. One dead feed should degrade one panel, not the whole board."""
    try:
        raw=open(os.path.join(HERE,"data.js"),encoding="utf-8").read()
        raw=raw.split("=",1)[1].rstrip().rstrip(";")
        return json.loads(raw)
    except Exception:
        return {}

def build_data():
    base=json.load(open(os.path.join(HERE,"base.json"),encoding="utf-8"))
    FACT=base["DIST_FACTORS"]; BANDW=base.get("BANDW",8); TEMPL=base["STAT_TEMPLATE"]
    USAGE=base.get("USAGE",{})
    BYE=byes_from_sched(base["SCHED"])
    PREV=load_prev()          # last-good values for failover
    HEALTH={}                 # per-source status surfaced in the app: ok | stale | down
    def failover(name, val, prev_key, seasonal=False):
        # seasonal feeds (usage, injuries, in-season monitor) are legitimately empty before the season
        # starts — mark those "dormant" (neutral), not "down", so the health chip never cries wolf.
        empty = val is None or (isinstance(val,(list,dict)) and len(val)==0)
        if empty and PREV.get(prev_key) not in (None,{},[]):
            HEALTH[name]="stale"; log(f"  [health] {name} down this cycle — using last-good from data.js")
            return PREV.get(prev_key)
        HEALTH[name]="ok" if not empty else ("dormant" if seasonal else "down")
        return val
    log("Pulling live sources…")
    ffc,ffc_drafts=pull_ffc(); slp=pull_sleeper_players(); slw=pull_sleeper_weekly()
    espn_proj,espn_adp=pull_espn(); yah=pull_yahoo(); sadp=pull_sleeper_adp(); fpe=pull_fantasypros_ecr()
    kick,vegas=pull_kickoffs()
    usage_wk=pull_usage_week()
    dvp=pull_dvp()
    adv=pull_advanced()
    inj_reports=pull_injury_reports()
    accuracy=pull_accuracy()
    if not ffc: raise RuntimeError("FFC returned nothing — aborting this cycle")

    # consensus per (pos,key)
    PF_BIAS=(BASE.get("PROJFIX") or {}).get("bias") or {}
    # Accuracy-weighted ensemble: once a season plays out, tools_ensemble.py fits per-position
    # ESPN/Sleeper weights on proj_archive.csv and writes ensemble.json ONLY if they beat the equal
    # blend out of sample. Consume them here so the validated weights actually reach the projection;
    # absent the file (pre-season, or weighting didn't beat equal) this is empty and the blend stays
    # the plain average — byte-for-byte the previous behaviour.
    ENS_W={}
    try:
        _ep=os.path.join(HERE,"ensemble.json")
        if os.path.exists(_ep): ENS_W=(json.load(open(_ep,encoding="utf-8")) or {}).get("weights") or {}
    except Exception as ex: log("  ensemble.json unreadable, using equal blend:",ex)
    if ENS_W: log(f"  ensemble: accuracy-weighted blend active {ENS_W}")
    def cons(pos,key):
        e=espn_proj.get((pos,key)); s=slw.get((pos,key))
        seas=[v["season"] for v in (e,s) if v and v.get("season")]
        if not seas: return None
        # weights only apply when BOTH sources are present (the weighting was fit on ESPN-vs-Sleeper);
        # with one source, or no fitted weights, fall back to the equal average.
        ew=ENS_W.get(pos) if (ENS_W and e and s and e.get("season") is not None and s.get("season") is not None) else None
        wk=[]
        for i in range(18):
            ve=e["wk"][i] if (e and e.get("wk") and e["wk"][i]>0) else None
            vs=s["wk"][i] if (s and s.get("wk") and s["wk"][i]>0) else None
            if ew and ve is not None and vs is not None: wk.append(round(ew[0]*ve+ew[1]*vs,2))
            else:
                got=[x for x in (ve,vs) if x is not None]
                wk.append(round(sum(got)/len(got),2) if got else 0.0)
        base_s=(ew[0]*e["season"]+ew[1]*s["season"]) if ew else sum(seas)/len(seas)
        # Apply the measured, cross-validated bias correction to the SHIPPED consensus blend at FULL
        # strength (see pull_sleeper_weekly for the rationale). Faded in above 3 pts so a backup
        # projection is never driven to zero. The season total is shifted by exactly the sum of the
        # weekly corrections, so `s` and `wk` stay consistent and an uncorrected position (b falsy) is
        # left byte-for-byte unchanged.
        b=PF_BIAS.get(pos)
        if b:
            adj=[round(v+b*min(1.0,max(0.0,(v-3.0)/3.0)),2) if v>3 else v for v in wk]
            base_s+=sum(adj)-sum(wk); wk=adj
        return {"s":round(base_s,1),"e":(e["season"] if e else None),"k":(s["season"] if s else None),"wk":wk}

    # positional ranks by FFC ppr adp
    bypos={}
    for k,v in ffc.items(): bypos.setdefault(v["pos"],[]).append((v["adp"],k))
    rank={}
    for pos,lst in bypos.items():
        for i,(_,k) in enumerate(sorted(lst)): rank[(pos,k)]=i+1

    players=[]; pid=0
    for k,v in sorted(ffc.items(), key=lambda kv: kv[1]["adp"]):
        pos=v["pos"]; team=std(v.get("team") or (slp.get(k,{}) or {}).get("team") or "FA")
        c = cons(pos,k) if pos in ("QB","RB","WR","TE") else cons(pos, team if pos=="DST" else k)
        p={"id":pid,"name":v["name"],"pos":pos,"team":team,"bye":BYE.get(team,0),
           "adp":v["adp"],"adpSf":v["adpSf"],"override":None,
           "age":(slp.get(k,{}) or {}).get("age") or 27}
        # measured draft-position spread straight from FFC (thousands of real drafts)
        for fld in ("adpSd","adpN","adpHi","adpLo"):
            if v.get(fld) is not None: p[fld]=v[fld]
        if pos in ("QB","RB","WR","TE"):
            season = c["s"] if c else None
            if season: p["stats"]=build_stats(pos,season,TEMPL); p["cons"]=({"s":c["s"],"e":c["e"],"k":c["k"]}); p["wk"]=c["wk"]
            else: p["stats"]={"p":80}   # fallback for a skill player with no consensus
            fa=dist_for(pos, rank.get((pos,k),99), FACT, BANDW)
            if fa: p["dist"]={"f":fa["f"],"c":fa["c"],"bust":fa["bust"],"boom":fa["boom"]}
        else:  # K / DST
            season = c["s"] if c else None
            p["stats"]={"p": round(season,1) if season else (140 if pos=="DST" else 130)}
            if c: p["cons"]={"s":c["s"],"e":c["e"],"k":c["k"]}; p["wk"]=c["wk"]
        # injuries / depth
        io=slp.get(k)
        if io and io.get("sid"): p["sid"]=io["sid"]
        if pos=="DST": p["sid"]=team
        if io:
            if io.get("inj"): p["inj"]=io["inj"]
            if io.get("depth") is not None: p["depth"]=io["depth"]
            if io.get("injPart"): p["injPart"]=io["injPart"]
            if io.get("injNews"): p["injNews"]=io["injNews"]
        # extra ADP sources
        ea=espn_adp.get((pos, team if pos=="DST" else k))
        if ea:
            if ea.get("s"): p["adpE"]=ea["s"]
            if ea.get("sf"): p["adpEsf"]=ea["sf"]
        ya=yah.get(k)
        if ya is not None: p["adpY"]=ya
        sa=sadp.get(k)
        if sa:
            p["adpS"]=sa["ppr"]
            if sa.get("sf"): p["adpSsf"]=sa["sf"]
            if sa.get("dyn"): p["adpSdyn"]=sa["dyn"]
            if sa.get("dynSf"): p["adpSdynSf"]=sa["dynSf"]
        fe=fpe.get(k)
        if fe:
            for kk in ("ecr","ecrSf","ecrDyn"):
                if fe.get(kk): p[kk]=fe[kk]
        if USAGE.get(k) is not None: p["usage"]=USAGE[k]
        players.append(p); pid+=1

    # ---- expand the pool so NO league size/format can ever run out of players ----
    # FFC only lists ~230. Deep formats need far more: 12x18 best ball = 216 picks,
    # dynasty startups (12-14 teams x 25-30 spots) can exceed 400. We include every player
    # carrying ANY signal from ANY site: an ADP (FFC/Sleeper/ESPN/Yahoo) or a real projection.
    POOL_TARGET=900
    posCount={}
    for p in players: posCount[p["pos"]]=posCount.get(p["pos"],0)+1
    cand=[]
    for k,info in slp.items():
        if k in ffc: continue
        pos=info.get("pos")
        if pos not in ("QB","RB","WR","TE","K"): continue
        c=cons(pos,k)
        seasonPts=(c or {}).get("s")
        sa=sadp.get(k); ea=espn_adp.get((pos,k)); ya=yah.get(k)
        hasAdp=bool(sa) or bool(ea and ea.get("s")) or (ya is not None)
        if seasonPts is None and not hasAdp: continue          # no signal anywhere -> skip
        if not hasAdp and (seasonPts or 0)<5: continue          # projected ~nothing and undrafted
        cand.append(((seasonPts or 0)+(500 if hasAdp else 0), seasonPts, k, info, pos, c, sa, ea, ya))
    cand.sort(key=lambda x:-x[0])
    for _,seasonPts,k,info,pos,c,sa,ea,ya in cand:
        if len(players)>=POOL_TARGET: break
        team=std(info.get("team") or "FA")
        posCount[pos]=posCount.get(pos,0)+1
        adp=sa["ppr"] if sa else (ea.get("s") if (ea and ea.get("s")) else (ya if ya is not None else round(300+len(players)*0.2,1)))
        p={"id":pid,"name":info["name"],"pos":pos,"team":team,"bye":BYE.get(team,0),
           "adp":adp,"adpSf":(sa.get("sf") if sa else adp),"override":None,"age":info.get("age") or 25,
           "stats":build_stats(pos,(seasonPts if seasonPts else 20),TEMPL)}
        if c: p["cons"]={"s":c["s"],"e":c["e"],"k":c["k"]}; p["wk"]=c["wk"]
        fa=dist_for(pos,posCount[pos],FACT,BANDW)
        if fa: p["dist"]={"f":fa["f"],"c":fa["c"],"bust":fa["bust"],"boom":fa["boom"]}
        # NOT `k` — that is the player's name key, still needed below for the ECR and usage
        # lookups. Rebinding it here left every Sleeper-extra player matching against the literal
        # string "adpLo", which silently halved ecr (415 -> 210) and usage (368 -> 173) coverage.
        for fld in ("adpSd","adpN","adpHi","adpLo"):
            if info.get(fld) is not None: p[fld]=info[fld]
        if info.get("sid"): p["sid"]=info["sid"]
        if info.get("inj"): p["inj"]=info["inj"]
        if info.get("depth") is not None: p["depth"]=info["depth"]
        if info.get("injPart"): p["injPart"]=info["injPart"]
        if info.get("injNews"): p["injNews"]=info["injNews"]
        if ea:
            if ea.get("s"): p["adpE"]=ea["s"]
            if ea.get("sf"): p["adpEsf"]=ea["sf"]
        if ya is not None: p["adpY"]=ya
        if sa:
            p["adpS"]=sa["ppr"]
            if sa.get("sf"): p["adpSsf"]=sa["sf"]
            if sa.get("dyn"): p["adpSdyn"]=sa["dyn"]
            if sa.get("dynSf"): p["adpSdynSf"]=sa["dynSf"]
        fe=fpe.get(k)
        if fe:
            for kk in ("ecr","ecrSf","ecrDyn"):
                if fe.get(kk): p[kk]=fe[kk]
        if USAGE.get(k) is not None: p["usage"]=USAGE[k]
        players.append(p); pid+=1
    # every NFL defense (FFC lists only ~22 of 32)
    haveDST={p["team"] for p in players if p["pos"]=="DST"}
    for team in sorted(base["SCHED"].keys()):
        if team in haveDST: continue
        c=cons("DST",team)
        seasonPts=(c or {}).get("s") or 120
        posCount["DST"]=posCount.get("DST",0)+1
        p={"id":pid,"name":team+" Defense","pos":"DST","team":team,"bye":BYE.get(team,0),
           "adp":round(300+len(players)*0.2,1),"adpSf":round(300+len(players)*0.2,1),
           "override":None,"age":27,"sid":team,"stats":{"p":round(seasonPts,1)}}
        if c: p["cons"]={"s":c["s"],"e":c["e"],"k":c["k"]}; p["wk"]=c["wk"]
        players.append(p); pid+=1
    log(f"  pool expanded to {len(players)} players")

    # superflex QB-shift insight: mean overall ADP of top-12 QBs, 1QB (adp) vs SF (adpSf)
    qbs=sorted([p for p in players if p["pos"]=="QB"], key=lambda x:x["adp"])[:12]
    sfShift=[round(sum(q["adp"] for q in qbs)/len(qbs),1),
             round(sum(q.get("adpSf",q["adp"]) for q in qbs)/len(qbs),1)] if qbs else [65.0,20.0]

    # ---- IDP defenders, appended after the offensive pool ----
    idp=pull_idp(); idpw=pull_idp_weekly()
    idpCount={}
    for d in idp:
        idpCount[d["pos"]]=idpCount.get(d["pos"],0)+1
        team=d["team"] or "FA"
        p={"id":pid,"name":d["name"],"pos":d["pos"],"posDetail":d.get("posDetail"),
           "team":team,"bye":BYE.get(team,0),
           "adp":d["adp"] if d["adp"] else round(300+len(players)*0.2,1),
           "adpSf":d["adp"] if d["adp"] else round(300+len(players)*0.2,1),
           "override":None,"age":25,"idp":d["idp"],"stats":{}}
        if d["adp"]: p["adpIdp"]=d["adp"]
        if d.get("sid"):
            p["sid"]=d["sid"]
            w=idpw.get(d["sid"])
            if w: p["idpWk"]=w["wk"]
        fa=dist_for("IDP_"+d["pos"],idpCount[d["pos"]],FACT,BANDW) or dist_for("WR",idpCount[d["pos"]],FACT,BANDW)
        if fa: p["dist"]={"f":fa["f"],"c":fa["c"],"bust":fa["bust"],"boom":fa["boom"]}
        players.append(p); pid+=1
    log(f"  pool with IDP: {len(players)} ({sum(idpCount.values())} defenders: "
        + " ".join(f"{k}{v}" for k,v in sorted(idpCount.items())) + ")")

    market=pull_market()
    if market:
        # attach per-player market values by sleeper id, else normalised name
        bysid={str(p["sid"]):p for p in players if p.get("sid")}
        byname={norm(p["name"]):p for p in players}
        hit=0
        for k,r in market["players"].items():
            tgt=bysid.get(str(r.get("sid") or "")) or byname.get(k)
            if tgt is not None:
                tgt["mkt"]=r["v"]
                if r.get("dis"): tgt["mktDis"]=r["dis"]
                if r.get("n"): tgt["mktN"]=r["n"]          # votes behind the value
                if r.get("srcN"): tgt["mktSrcN"]=r["srcN"] # how many sources price him
                if r.get("src"): tgt["mktSrc"]=r["src"]
                hit+=1
        log(f"  market matched to pool: {hit}/{len(players)}")

    # One name per defense, always. FFC supplies a handful under their city ("Atlanta Defense")
    # and we fill the rest in by abbreviation ("ATL Defense"), so which name a team gets flips
    # whenever FFC's list changes. The app keys saved players by name, so a flip reads as a brand
    # new player and quietly leaves a second Tennessee in everyone's pool. Abbreviation wins
    # because it matches the team code we already store.
    for p in players:
        if p["pos"]=="DST" and p.get("team"): p["name"]=f'{p["team"]} Defense'

    # attach measured opportunity (target/air-yards share, WOPR, snap %) to every player by name
    if adv and adv.get("players"):
        amap=adv["players"]; hit=0
        for p in players:
            a=amap.get(norm(p["name"]))
            if a: p["adv"]=a; hit+=1
        log(f"  advanced usage matched to pool: {hit}/{len(players)}")

    archive_projections(players)

    # Per-source health: projection feeds degrade the blend gracefully (record ok/down for the chip);
    # the standalone context blobs fall back to last-good so one dead feed doesn't blank a panel.
    HEALTH["ffc"]="ok"
    HEALTH["espn"]="ok" if espn_proj else "down"
    HEALTH["yahoo"]="ok" if yah else "down"
    HEALTH["sleeperAdp"]="ok" if sadp else "down"
    HEALTH["ecr"]="ok" if fpe else "down"
    dvp_fo    = failover("dvp",        dvp,         "DVP")
    kick_fo   = failover("kickoffs",   kick,        "KICK")
    vegas_fo  = failover("vegas",      vegas,       "VEGAS")
    usage_fo  = failover("usage",      usage_wk,    "USAGE_WK",    seasonal=True)
    inj_fo    = failover("injuries",   inj_reports, "INJ_REPORTS", seasonal=True)
    mon_fo    = failover("monitor",    accuracy,    "MONITOR",     seasonal=True)
    out={"PLAYERS":players,"BACKTEST":base["BACKTEST"],"SLOTVAL":base["SLOTVAL"],"OPENING":base["OPENING"],
         "DVP":(dvp_fo or base["DVP"]),"DVP_LIVE":bool(dvp_fo),"ADV_META":({"year":adv["year"],"current":adv["current"]} if adv else None),
         "SCHED":base["SCHED"],"CALIB":base["CALIB"],"KICK":kick_fo,"VEGAS":vegas_fo,"USAGE_WK":usage_fo,"INJ_REPORTS":inj_fo,"MONITOR":mon_fo,
         "MISSRATE":base.get("MISSRATE"),"WEEKCV":base.get("WEEKCV"),"PROJFIX":base.get("PROJFIX"),"DRAWCV":base.get("DRAWCV"),
         "META":{"updated":time.strftime("%Y-%m-%d %H:%M"),"sources":"FFC+ESPN+Sleeper+Yahoo (live) · nflverse (historical)",
                 "drafts":ffc_drafts,"hist":"11 seasons (2014-24)","sfShift":sfShift,"health":HEALTH,
                 "usageEval":base.get("USAGE_EVAL"),
                 "market":({"picks":market["picks"],
                            "sources":"KeepTradeCut + FantasyCalc + DynastyProcess",
                            "players":len(market["players"]),
                            "updated":time.strftime("%Y-%m-%d %H:%M")} if market else None)}}
    log(f"Built {len(players)} players "
        f"({sum(1 for p in players if p.get('cons'))} w/ consensus, "
        f"{sum(1 for p in players if p.get('inj'))} injuries)")
    return out

def write_data_js(data):
    path=os.path.join(HERE,"data.js")
    open(path,"w",encoding="utf-8").write("window.__FFDATA__="+json.dumps(data,separators=(",",":"))+";")
    return path

def git_push():
    if not GIT_PUSH: return
    try:
        # data.js drives the site; proj_archive.csv is the season-long forecast record. The archive
        # is the whole point of the accuracy work — next year's walk-forward test is fit against it —
        # so it has to leave this machine. It was accumulating locally and never pushed, one disk
        # failure from gone. Both are backed up now. Trigger on either changing.
        # Only stage files that actually exist — props_archive.csv/props.json are absent until the
        # optional odds key is set, and `git add` on a missing path aborts the WHOLE commit (exit 128),
        # which silently stalled every data push. Filtering keeps a dormant feature from blocking data.
        tracked=[f for f in ["data.js","proj_archive.csv","props_archive.csv","props.json"]
                 if os.path.exists(os.path.join(HERE,f))]
        st=subprocess.run(["git","-C",HERE,"status","--porcelain",*tracked],capture_output=True,text=True)
        if not st.stdout.strip():
            log("git: no change, nothing to push"); return
        subprocess.run(["git","-C",HERE,"add",*tracked],check=True)
        subprocess.run(["git","-C",HERE,"commit","-m",f"data update {time.strftime('%Y-%m-%d %H:%M')}"],check=True)
        subprocess.run(["git","-C",HERE,"push"],check=True)
        log("git: pushed OK - your live site will update in ~1 min")
        return True
    except Exception as ex:
        log("git push failed (is the repo set up? see README):",ex)
    return False

def keepalive():
    """GitHub disables a SCHEDULED workflow after 60 days with no repo commits. In-season the data
    commits on its own; this only fires in a dead offseason where nothing changed for a week, adding
    one trivial heartbeat commit so the schedule never auto-pauses and you never have to touch it.
    The timestamp lives in the committed file (not the filesystem mtime, which resets on each CI
    checkout), so the weekly gate survives fresh runners."""
    if not GIT_PUSH: return
    hb=os.path.join(HERE,".heartbeat")
    try:
        last=0
        if os.path.exists(hb):
            try: last=int(json.load(open(hb)).get("ts",0))
            except Exception: last=0
        if time.time()-last < 6*86400: return         # at most one heartbeat a week
        json.dump({"ts":int(time.time()),"utc":time.strftime("%Y-%m-%d %H:%M")},open(hb,"w"))
        subprocess.run(["git","-C",HERE,"add",".heartbeat"],check=True)
        subprocess.run(["git","-C",HERE,"commit","-m",f"keepalive {time.strftime('%Y-%m-%d')}"],check=True)
        subprocess.run(["git","-C",HERE,"push"],check=True)
        log("keepalive: heartbeat committed — schedule stays active")
    except Exception as ex:
        log("keepalive skipped:",ex)

def props_step():
    # Player props forward-experiment. Dormant unless ODDS_API_KEY is set (a free the-odds-api key,
    # added as an Actions secret). Pulls + archives this week's props and re-scores the archive against
    # actuals into props.json. Props never touch the projection until that verdict proves positive.
    key=os.environ.get("ODDS_API_KEY")
    try:
        import tools_props
        if key:
            log("  props:", tools_props.pull_and_archive(key))
        log("  props verdict:", tools_props.validate().get("verdict"))
    except Exception as ex:
        log("  props step skipped:",ex)

def cycle():
    try:
        data=build_data(); write_data_js(data)
        props_step()
        pushed=git_push()
        if not pushed: keepalive()   # only when real data didn't change this cycle
    except Exception:
        log("cycle error:\n"+traceback.format_exc())

if __name__=="__main__":
    once = "--once" in sys.argv
    log(f"Draft War Room refresher starting (every {REFRESH_HOURS}h). Ctrl+C to stop.")
    while True:
        cycle()
        if once: break
        log(f"Sleeping {REFRESH_HOURS}h…")
        try: time.sleep(REFRESH_HOURS*3600)
        except KeyboardInterrupt: log("stopped."); break

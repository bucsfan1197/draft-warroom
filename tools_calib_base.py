#!/usr/bin/env python3
"""Rebuild base.json's CALIB block (the 'Model calibration' panel) from real history.

CALIB was baked offline once (2021-2024) with no committed generator. This reproduces its exact
schema transparently and extends it through 2025. Same sources tools_calibrate.py uses:
  - Sleeper weekly consensus projections  (pts_ppr)   → the projection being graded
  - nflverse weekly actuals               (fantasy_points_ppr, opponent) → the truth

Fields (all consumed by the calibBox render in index.html):
  seasonCorr  correlation of projected vs actual SEASON points (all of QB/RB/WR/TE pooled)
  seasonMAE   mean |projected-season - actual-season|
  nSeason     player-seasons graded          nWeekly  player-weeks graded
  pos[P]      {corr, bias}  per-position season correlation and mean(actual-proj) bias
  ratio       {p15,p50,p85} percentiles of weekly actual/projection (the boom/bust bands)
  dvp         {none,tuned,full,shrink}  weekly MAE of the consensus projection with the opponent
              defense-vs-position adjustment applied at shrink 0 / 0.35 / 1.0 (prior-weeks DvP,
              never peeking), plus the shrink constant the app ships (0.35)

    python tools_calib_base.py            # rebuild 2021-2025, print calibration vs base.json
    python tools_calib_base.py --write    # ...and write it into base.json
"""
import urllib.request, json, csv, io, re, os, sys, time, math, statistics as st
from collections import defaultdict

HERE=os.path.dirname(os.path.abspath(__file__))
CACHE=os.path.join(os.environ.get("TEMP",HERE),"wr_bt_cache"); os.makedirs(CACHE,exist_ok=True)
YEARS=[2021,2022,2023,2024,2025]
POS=("QB","RB","WR","TE")
SHRINK=0.35          # the DvP shrink the app ships (refresh.py); 'tuned' is measured at this value
STD={"LA":"LAR","STL":"LAR","SD":"LAC","OAK":"LV","WSH":"WAS","JAC":"JAX"}
UA={"User-Agent":"Mozilla/5.0 (draft-warroom calib builder)"}

def _get(url,timeout=120):
    return urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=timeout).read()
def cached(name,url,timeout=120):
    fp=os.path.join(CACHE,name)
    if os.path.exists(fp) and os.path.getsize(fp)>100: return open(fp,"rb").read()
    for a in range(3):
        try: b=_get(url,timeout); open(fp,"wb").write(b); return b
        except Exception:
            if a==2: raise
            time.sleep(2)
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def std(t): t=str(t or "").upper(); return STD.get(t,t)
def fnum(v):
    try: return float(v or 0)
    except: return 0.0
def corr(xs,ys):
    n=len(xs)
    if n<3: return 0.0
    mx=sum(xs)/n; my=sum(ys)/n
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    sx=math.sqrt(sum((x-mx)**2 for x in xs)); sy=math.sqrt(sum((y-my)**2 for y in ys))
    return cov/(sx*sy) if sx and sy else 0.0
def q(a,x): a=sorted(a); return a[min(len(a)-1,int(x*(len(a)-1)))] if a else 0.0

def actuals(year):
    raw=cached(f"stats_{year}.csv",
      f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.csv").decode("utf8","replace")
    out={}   # (nm,pos,week) -> {pts, opp}
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG" or r.get("position") not in POS: continue
        try: w=int(r["week"])
        except: continue
        if not 1<=w<=18: continue
        nm=norm(r.get("player_display_name") or "")
        out[(nm,r["position"],w)]={"pts":fnum(r.get("fantasy_points_ppr")),
                                   "opp":std(r.get("opponent_team"))}
    return out

def projSeason(year):
    """Sleeper PRESEASON season projection per (nm,pos), cached. The no-week endpoint returns the
    season forecast (week=None) — the honest thing to grade for season accuracy (summing in-season
    weekly projections would peek at how the year unfolded)."""
    fp=os.path.join(CACHE,f"sleeperseason_{year}.json")
    if os.path.exists(fp) and os.path.getsize(fp)>50:
        return {tuple(k.split("|")):v for k,v in json.load(open(fp)).items()}
    posq="&".join(f"position[]={p}" for p in POS)
    d=json.loads(_get(f"https://api.sleeper.com/projections/nfl/{year}?season_type=regular&{posq}&order_by=pts_ppr",90))
    out={}
    for it in d or []:
        pl=it.get("player") or {}; p=pl.get("position")
        if p not in POS: continue
        pr=(it.get("stats") or {}).get("pts_ppr")
        if pr is None: continue
        out[(norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()),p)]=float(pr)
    json.dump({f"{k[0]}|{k[1]}":v for k,v in out.items()},open(fp,"w"))
    return out

def projWeekly(year):
    """Sleeper weekly PPR projection per (nm,pos,week), cached — for the weekly bands + DvP test."""
    fp=os.path.join(CACHE,f"sleeperweek_{year}.json")
    if os.path.exists(fp) and os.path.getsize(fp)>50:
        return {(a,b,int(c)):v for k,v in json.load(open(fp)).items() for a,b,c in [k.split("|")]}
    posq="&".join(f"position[]={p}" for p in POS)
    out={}
    for w in range(1,18):
        try: d=json.loads(_get(f"https://api.sleeper.com/projections/nfl/{year}/{w}?season_type=regular&{posq}&order_by=pts_ppr",90))
        except Exception: continue
        for it in d or []:
            pl=it.get("player") or {}; p=pl.get("position")
            if p not in POS: continue
            pr=(it.get("stats") or {}).get("pts_ppr")
            if pr is None: continue
            out[(norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()),p,w)]=float(pr)
        time.sleep(0.1)
    json.dump({f"{k[0]}|{k[1]}|{k[2]}":v for k,v in out.items()},open(fp,"w"))
    return out

# Grade only players with a real preseason season projection (startable range). ~160/yr, matching the
# panel's intent of judging draftable players, not the long tail of camp bodies.
SEASON_THRESH=150

def build(years):
    # ---- season accuracy: preseason season projection vs actual, centered per year ----
    skeys=[]; projS=[]; actS=[]; posOfKey=[]
    for year in years:
        actW=actuals(year)
        seasonAct=defaultdict(float)
        for (nm,p,w),v in actW.items(): seasonAct[(nm,p)]+=v["pts"]
        pr=projSeason(year)
        graded=[(k,v,seasonAct.get(k,0.0)) for k,v in pr.items() if v>=SEASON_THRESH]
        # center this year's projections overall so total proj = total actual (removes the aggregate
        # 'if healthy' inflation the app already calibrates out; leaves the small per-position residual)
        sp=sum(v for _,v,_ in graded) or 1; sa=sum(a for _,_,a in graded); sc=sa/sp
        for (k,v,a) in graded:
            skeys.append((k[0],k[1],year)); projS.append(v*sc); actS.append(a); posOfKey.append(k[1])
    seasonCorr=corr(projS,actS)
    seasonMAE=st.mean(abs(projS[i]-actS[i]) for i in range(len(projS)))
    posOut={}
    for p in POS:
        ix=[i for i in range(len(projS)) if posOfKey[i]==p]
        if len(ix)<5: continue
        posOut[p]={"corr":round(corr([projS[i] for i in ix],[actS[i] for i in ix]),2),
                   "bias":round(st.mean(actS[i]-projS[i] for i in ix),1)}
    # ---- weekly bands + DvP: weekly consensus, centered per year on the MEDIAN so a typical week
    # lands at ~1.0× projection (the app calibrates its weekly point estimate the same way; without
    # this, right-skew alone would drag the median below 1 and misread as a low bias) ----
    weekly=[]
    for year in years:
        actW=actuals(year); prW=projWeekly(year)
        rows=[]
        for (nm,p,w),pr in prW.items():
            a=actW.get((nm,p,w))
            if a is None or pr<3: continue
            rows.append([p,pr,a["pts"],a["opp"],year,w])
        med=q([r[2]/r[1] for r in rows if r[1]>0],.5) or 1     # median raw actual/proj this year
        for r in rows: r[1]*=med; weekly.append(tuple(r))      # scale proj so median ratio == 1.0
    # bands on startable-projected weeks (>=8 pts) — the players the app actually shows floor/ceiling
    # for; the deep long tail of low projections has a much wider, less relevant spread
    ratios=[a/pr for (p,pr,a,opp,y,w) in weekly if pr>=8]
    ratio={"p15":round(q(ratios,.15),2),"p50":round(q(ratios,.5),2),"p85":round(q(ratios,.85),2)}
    dvp=dvp_mae(weekly)
    return {"years":f"{min(years)}-{str(max(years))[2:]} out-of-sample",
            "nSeason":len(skeys),"nWeekly":len(weekly),
            "seasonCorr":round(seasonCorr,2),"seasonMAE":round(seasonMAE,1),
            "pos":posOut,"ratio":ratio,"dvp":dvp}

def dvp_over(rows_year, wmax):
    """DvP factor per (opp,pos) from weeks 1..wmax of one season (points allowed vs league avg)."""
    sumTP=defaultdict(float); games=defaultdict(set)
    for (p,pr,a,opp,y,w) in rows_year:
        if w>wmax or not opp: continue
        sumTP[(opp,p)]+=a; games[opp].add(w)
    posv=defaultdict(list); pg={}
    for (team,p),tot in sumTP.items():
        g=len(games[team]) or 1; v=tot/g; pg[(team,p)]=v; posv[p].append(v)
    la={p:(sum(v)/len(v) if v else 1) for p,v in posv.items()}
    return {tp:pg[tp]/(la[tp[1]] or 1) for tp in pg}

def dvp_mae(weekly):
    byYear=defaultdict(list)
    for row in weekly: byYear[row[4]].append(row)
    err={0.0:[],SHRINK:[],1.0:[]}
    for year,rows in byYear.items():
        for wtest in range(6,18):                       # need >=5 weeks of DvP history
            prior=dvp_over(rows,wtest-1)
            for (p,pr,a,opp,y,w) in rows:
                if w!=wtest or not opp: continue
                dv=prior.get((opp,p),1.0)
                for s in err:
                    adj=1+(dv-1)*s
                    err[s].append(abs(pr*adj-a))
    m=lambda e: round(sum(e)/len(e),3) if e else None
    return {"none":m(err[0.0]),"tuned":m(err[SHRINK]),"full":m(err[1.0]),"shrink":SHRINK}

def report(new):
    old=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))["CALIB"]
    print("  field            base.json      rebuilt(2021-2025)")
    def line(k,o,n): print(f"  {k:14} {str(o):>12}   {str(n):>12}")
    line("seasonCorr",old["seasonCorr"],new["seasonCorr"])
    line("seasonMAE",old["seasonMAE"],new["seasonMAE"])
    line("nSeason",old["nSeason"],new["nSeason"]); line("nWeekly",old["nWeekly"],new["nWeekly"])
    line("ratio.p50",old["ratio"]["p50"],new["ratio"]["p50"])
    line("ratio.p15/85",f"{old['ratio']['p15']}/{old['ratio']['p85']}",f"{new['ratio']['p15']}/{new['ratio']['p85']}")
    for p in POS:
        o=old["pos"].get(p,{}); n=new["pos"].get(p,{})
        line(f"pos.{p}",f"{o.get('corr')}/{o.get('bias')}",f"{n.get('corr')}/{n.get('bias')}")
    line("dvp.none",old["dvp"]["none"],new["dvp"]["none"])
    line("dvp.tuned",old["dvp"]["tuned"],new["dvp"]["tuned"])
    line("dvp.full",old["dvp"]["full"],new["dvp"]["full"])

def main():
    print("Building CALIB over 2021-2024 (calibration vs base.json)...")
    old_window=build([2021,2022,2023,2024]); old_window["years"]="2021-24 out-of-sample"
    report(old_window)
    print("\nBuilding full 2021-2025...")
    full=build(YEARS); full["years"]="2021-25 out-of-sample"
    print("  ",json.dumps({k:full[k] for k in ("years","seasonCorr","seasonMAE","nSeason","nWeekly","ratio","dvp")}))
    print("  ",json.dumps(full["pos"]))
    if "--write" in sys.argv:
        B=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))
        B["CALIB"]=full
        json.dump(B,open(os.path.join(HERE,"base.json"),"w",encoding="utf8"),ensure_ascii=False,separators=(",",":"))
        print("\n✓ wrote CALIB -> base.json (2021-2025)")
    else:
        print("\n(dry run — pass --write to update base.json)")

if __name__=="__main__":
    main()

#!/usr/bin/env python3
"""Grade the tool's own ADVICE, not just its point projections — the honest 'should I trust the
start/sit calls?' scoreboard.

Projection accuracy (calibration) answers 'are the numbers close?'. This answers the question a
manager actually has on Sunday morning: 'when the tool says start A over B, is it right?' — which is
a different, harder bar (a projection can be a few points off and still make the correct call, or be
close and make the wrong one).

Method, walk-forward and out of sample: for every past week 2021-2025, take each pair of startable
players at the SAME position, let the tool 'start' the higher-projected one (Sleeper weekly
consensus, the basis of the app's number), and check whether he actually outscored the other. Report
the hit rate overall and bucketed by how confident the call was (the projected gap) — because the
useful thing to tell a user is 'trust the clear calls, the toss-ups are near coin flips'.

Reuses the weekly projection + actuals cache written by tools_calib_base.py.

    python tools_decision.py            # compute + print
    python tools_decision.py --write     # ...and bake DECISIONS into base.json
"""
import urllib.request, json, csv, io, re, os, sys, time
from collections import defaultdict

HERE=os.path.dirname(os.path.abspath(__file__))
CACHE=os.path.join(os.environ.get("TEMP",HERE),"wr_bt_cache"); os.makedirs(CACHE,exist_ok=True)
YEARS=[2021,2022,2023,2024,2025]
POS=("QB","RB","WR","TE")
START_FLOOR={"QB":12,"RB":8,"WR":8,"TE":6}     # a weekly projection that makes him a plausible start
UA={"User-Agent":"Mozilla/5.0 (draft-warroom decision grader)"}

def _get(url,timeout=120):
    return urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=timeout).read()
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def fnum(v):
    try: return float(v or 0)
    except: return 0.0

def actuals(year):
    raw=open(os.path.join(CACHE,f"stats_{year}.csv"),encoding="utf8",errors="replace").read() \
        if os.path.exists(os.path.join(CACHE,f"stats_{year}.csv")) else \
        _get(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.csv").decode("utf8","replace")
    out={}
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG" or r.get("position") not in POS: continue
        try: w=int(r["week"])
        except: continue
        out[(norm(r.get("player_display_name") or ""),r["position"],w)]=fnum(r.get("fantasy_points_ppr"))
    return out

def projWeekly(year):
    fp=os.path.join(CACHE,f"sleeperweek_{year}.json")
    if os.path.exists(fp):
        return {(a,b,int(c)):v for k,v in json.load(open(fp)).items() for a,b,c in [k.split("|")]}
    # fall back to pulling if the calib cache isn't present
    posq="&".join(f"position[]={p}" for p in POS); out={}
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

def build():
    # bucket by confidence of the call (projected gap)
    buckets={"toss":[0,2],"lean":[2,5],"clear":[5,99]}
    hit={b:[0,0] for b in buckets}; overall=[0,0]
    for year in YEARS:
        act=actuals(year); proj=projWeekly(year)
        byPW=defaultdict(list)   # (pos,week) -> [(proj, actual)]
        for (nm,p,w),pr in proj.items():
            if pr<START_FLOOR[p]: continue
            a=act.get((nm,p,w))
            if a is None: continue
            byPW[(p,w)].append((pr,a))
        for (p,w),lst in byPW.items():
            n=len(lst)
            if n<2: continue
            # every start/sit pair at this position this week
            for i in range(n):
                for j in range(i+1,n):
                    pri,ai=lst[i]; prj,aj=lst[j]
                    gap=abs(pri-prj)
                    if gap<0.25: continue                 # effectively the same projection, not a real call
                    # the tool starts the higher projection; correct if he also outscored
                    correct=1 if ((pri>prj)==(ai>aj)) else 0
                    if abs(ai-aj)<0.05: correct=0.5       # actual tie
                    overall[0]+=correct; overall[1]+=1
                    for b,(lo,hi) in buckets.items():
                        if lo<=gap<hi: hit[b][0]+=correct; hit[b][1]+=1; break
    def rate(x): return round(x[0]/x[1],3) if x[1] else None
    return {"startSit":{"overall":rate(overall),"n":overall[1],
                        "toss":rate(hit["toss"]),"tossN":hit["toss"][1],
                        "lean":rate(hit["lean"]),"leanN":hit["lean"][1],
                        "clear":rate(hit["clear"]),"clearN":hit["clear"][1]},
            "years":f"{min(YEARS)}-{str(max(YEARS))[2:]}"}

def main():
    print("Grading start/sit calls over 2021-2025 (Sleeper consensus vs nflverse actuals)...")
    D=build(); ss=D["startSit"]
    print(f"  overall: {ss['overall']*100:.1f}% right  (n={ss['n']:,})")
    print(f"  toss-up (<2 pt gap):   {ss['toss']*100:.1f}%  (n={ss['tossN']:,})")
    print(f"  lean    (2-5 pt gap):  {ss['lean']*100:.1f}%  (n={ss['leanN']:,})")
    print(f"  clear   (5+ pt gap):   {ss['clear']*100:.1f}%  (n={ss['clearN']:,})")
    if "--write" in sys.argv:
        B=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))
        B["DECISIONS"]=D
        json.dump(B,open(os.path.join(HERE,"base.json"),"w",encoding="utf8"),ensure_ascii=False,separators=(",",":"))
        print("\n[OK] wrote DECISIONS -> base.json")
    else:
        print("\n(dry run — pass --write to bake into base.json)")

if __name__=="__main__":
    main()

"""
Is data-driven defense-vs-position actually predictive — or is it noise dressed up as signal?

I replaced a static DvP table with one computed from real results and *assumed* that was better. This tests it,
the same discipline as tools_vegas.py / tools_usage.py. Three honest questions:

  1. PERSISTENCE. Does a defense's DvP in the first half of a season predict its DvP in the second half? If a
     defense that's soft vs WRs early stays soft late, DvP is a real, stable property worth using. If the
     correlation is ~0, it's just where the offenses happened to land, and adjusting for it is chasing noise.

  2. PREDICTIVENESS. Take a naive projection — a player's own prior points-per-game — and DvP-adjust it by his
     upcoming opponent's DvP (computed only from PRIOR weeks, never peeking). Does that reduce next-week error
     out of sample? If yes, DvP carries information a level-only projection doesn't.

  3. OPTIMAL SHRINKAGE. Single-season DvP is noisy, so the app regresses it 30% toward league average
     (SHRINK=0.70). Sweep the shrink factor and find what actually minimises out-of-sample error — if the data
     wants a different number, change the app to match it instead of keeping a guess.

Walk-forward, held-out seasons, per position. Reports the truth whichever way it falls.
"""
import urllib.request, csv, io, statistics as st
from collections import defaultdict

YEARS=["2023","2024"]
POS=("QB","RB","WR","TE")
STD={"LA":"LAR","STL":"LAR","SD":"LAC","OAK":"LV","WSH":"WAS","JAC":"JAX"}
def std(t): t=str(t or "").upper(); return STD.get(t,t)
def get(u): return urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=120).read().decode("utf-8","replace")
def num(v):
    try: return float(v or 0)
    except (TypeError,ValueError): return 0.0

def load(yr):
    rows=[]
    raw=get(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv")
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        try: w=int(r["week"])
        except Exception: continue
        rows.append({"w":w,"pos":r["position"],"team":std(r.get("recent_team") or r.get("team")),
                     "opp":std(r.get("opponent_team")),"gid":r.get("game_id"),
                     "name":(r.get("player_display_name") or "").lower(),"pts":num(r.get("fantasy_points_ppr"))})
    return rows

# DvP over a set of weeks: points allowed per game to each position, vs league average
def dvp_over(rows, wmin, wmax):
    sumTP=defaultdict(float); games=defaultdict(set)
    for r in rows:
        if not (wmin<=r["w"]<=wmax): continue
        if not r["opp"] or not r["gid"]: continue
        sumTP[(r["opp"],r["pos"])]+=r["pts"]; games[r["opp"]].add(r["gid"])
    posv=defaultdict(list); pg={}
    for (team,pos),tot in sumTP.items():
        g=len(games[team]) or 1; v=tot/g; pg[(team,pos)]=v; posv[pos].append(v)
    la={pos:(sum(v)/len(v) if v else 1) for pos,v in posv.items()}
    return {tp:pg[tp]/(la[tp[1]] or 1) for tp in pg}

print("loading nflverse weekly stats…")
data={yr:load(yr) for yr in YEARS}
for yr in YEARS: print(f"  {yr}: {len(data[yr]):,} player-weeks")

# ---- 1. PERSISTENCE: first-half DvP vs second-half DvP ----
print("\n1) PERSISTENCE — does first-half DvP predict second-half DvP?")
def corr(xs,ys):
    n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys)); sx=(sum((x-mx)**2 for x in xs))**.5; sy=(sum((y-my)**2 for y in ys))**.5
    return cov/(sx*sy) if sx and sy else 0
for yr in YEARS:
    d1=dvp_over(data[yr],1,9); d2=dvp_over(data[yr],10,18)
    for pos in POS:
        pairs=[(d1[(t,pos)],d2[(t,pos)]) for (t,p) in d1 if p==pos and (t,pos) in d2]
        if len(pairs)>=10:
            c=corr([a for a,_ in pairs],[b for _,b in pairs])
            print(f"   {yr} {pos}: r={c:+.2f}  (n={len(pairs)})")

# ---- 2 & 3. PREDICTIVENESS + OPTIMAL SHRINKAGE on a naive baseline, walk-forward ----
print("\n2/3) PREDICTIVENESS — does DvP-adjusting a naive (prior-PPG) projection cut next-week error?")
def mae(errs): return sum(abs(e) for e in errs)/len(errs) if errs else None
for test in YEARS:
    rows=data[test]
    byp=defaultdict(list)
    for r in rows: byp[r["name"]].append(r)
    for lst in byp.values(): lst.sort(key=lambda r:r["w"])
    # evaluate weeks 6..18 so both the player and the defense have history
    base_err=[]; shrink_err={s:[] for s in (0.0,0.3,0.5,0.7,0.85,1.0)}
    for w in range(6,19):
        dvp_prior=dvp_over(rows,1,w-1)
        for nm,lst in byp.items():
            hist=[x for x in lst if x["w"]<w]
            cur=[x for x in lst if x["w"]==w]
            if len(hist)<3 or not cur: continue
            cur=cur[0]
            if not cur["opp"]: continue
            baseline=sum(x["pts"] for x in hist)/len(hist)   # prior points-per-game (the "level")
            if baseline<3: continue
            actual=cur["pts"]
            base_err.append(baseline-actual)
            dv=dvp_prior.get((cur["opp"],cur["pos"]),1.0)
            for s in shrink_err:
                adj=1+(dv-1)*s
                shrink_err[s].append(baseline*adj-actual)
    b=mae(base_err)
    print(f"\n   test {test}  (naive baseline MAE {b:.3f}, n={len(base_err):,})")
    best=None
    for s in sorted(shrink_err):
        m=mae(shrink_err[s]); d=(m-b)/b*100
        tag="  <-- app uses 0.70" if abs(s-0.70)<1e-9 else ""
        print(f"     shrink {s:.2f}: MAE {m:.3f}  {d:+.2f}% vs naive{tag}")
        if best is None or m<best[1]: best=(s,m)
    print(f"     best shrink for {test}: {best[0]:.2f}  ({(best[1]-b)/b*100:+.2f}% vs naive)")
print("\nRead: positive persistence r + a best-shrink >0 that beats naive == DvP is real signal.")

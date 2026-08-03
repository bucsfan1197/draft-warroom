"""
Does the betting market's implied team total add accuracy the consensus projection doesn't already have?

The honest worry with a Vegas adjustment is double-counting: ESPN/Sleeper projections are made by people
who have already seen the line, so the game environment is mostly priced in. If that's true, a Vegas term
adds nothing out of sample and belongs on screen as context, not folded into the number. This measures which.

Method — identical discipline to tools_beat.py:
  * Sleeper's weekly PPR projection is the consensus baseline.
  * nflverse weekly actuals are the truth (and carry each player's team that week).
  * nflverse games.csv carries the closing spread + total for every historical game, so the implied team
    total (total/2 +/- spread/2) is known before kickoff.
  * The Vegas coefficient is FIT ON EARLIER SEASONS AND TESTED ON A LATER ONE. If the market truly adds
    signal beyond consensus, out-of-sample MAE falls; if it's already priced in, it won't move (or worsens).

The candidate: residual = actual - consensus, regressed on the centred implied team total, per position.
proj_adj = proj + beta_pos * (implied_total - league_mean). Tested against consensus and against the
bias-only correction already shipped, so the only thing that survives is what beats BOTH out of sample.
"""
import urllib.request, json, csv, io, re, time, os, statistics as st

HERE=os.path.dirname(os.path.abspath(__file__))
YEARS=[2021,2022,2023,2024]
POS=("QB","RB","WR","TE")
STD={"LA":"LAR","STL":"LAR","SD":"LAC","OAK":"LV","WSH":"WAS","JAC":"JAX"}
def std(t): t=str(t or "").upper(); return STD.get(t,t)
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def gj(u,t=90):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r,timeout=t).read())
def gt(u,t=240):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(r,timeout=t).read().decode("utf8","replace")

# ---- implied team totals from games.csv (all years at once) ----
itt={}   # (year, week, team) -> implied points total
graw=gt("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv")
for r in csv.DictReader(io.StringIO(graw)):
    if r.get("game_type")!="REG": continue
    try:
        yr=int(r["season"]); wk=int(r["week"])
        tot=float(r["total_line"]); spr=float(r["spread_line"])   # spread_line > 0 == home favored
    except Exception: continue
    home,away=std(r.get("home_team")),std(r.get("away_team"))
    itt[(yr,wk,home)]=tot/2.0+spr/2.0
    itt[(yr,wk,away)]=tot/2.0-spr/2.0
print(f"implied totals: {len(itt):,} team-weeks loaded")

# ---- actuals (with team) + consensus projections, matched ----
rows=[]   # (year, week, pos, proj, actual, itt)
posq="&".join(f"position[]={p}" for p in POS)
for yr in YEARS:
    act={}
    raw=gt(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv")
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        try: w=int(r["week"]); v=float(r.get("fantasy_points_ppr") or 0)
        except Exception: continue
        team=std(r.get("recent_team") or r.get("team"))
        act[(norm(r.get("player_display_name") or ""),r["position"],w)]=(v,team)
    hit=0
    for w in range(1,18):
        try: d=gj(f"https://api.sleeper.com/projections/nfl/{yr}/{w}?season_type=regular&{posq}&order_by=pts_ppr")
        except Exception: continue
        for it in d or []:
            pl=it.get("player") or {}; p=pl.get("position")
            if p not in POS: continue
            pr=(it.get("stats") or {}).get("pts_ppr")
            if pr is None or pr<3: continue
            key=(norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()),p,w)
            if key not in act: continue
            v,team=act[key]
            tt=itt.get((yr,w,team))
            if tt is None: continue
            rows.append((yr,w,p,float(pr),v,tt)); hit+=1
        time.sleep(0.12)
    print(f"  {yr}: {hit:,} matched player-weeks with a line")

TEST=YEARS[-1]
tr=[r for r in rows if r[0]!=TEST]; te=[r for r in rows if r[0]==TEST]
meanITT=st.mean([r[5] for r in tr])
print(f"\nfit on {YEARS[:-1]} ({len(tr):,})  test on {TEST} ({len(te):,})   league mean implied total {meanITT:.1f}")

# ---- fit bias (per pos) and Vegas beta (per pos) on TRAINING ONLY ----
bias={p: st.mean([r[4]-r[3] for r in tr if r[2]==p]) for p in POS}
beta={}
for p in POS:
    sub=[r for r in tr if r[2]==p]
    x=[r[5]-meanITT for r in sub]; y=[r[4]-r[3] for r in sub]  # residual after consensus
    den=sum(xi*xi for xi in x)
    beta[p]=(sum(xi*yi for xi,yi in zip(x,y))/den) if den else 0.0
print("\nfitted on training years only (residual pts per +1 implied point):")
for p in POS:
    print(f"  {p}: bias {bias[p]:+.2f}   vegas beta {beta[p]:+.4f}")

def score(rs,fn):
    e=[fn(r)-r[4] for r in rs]; return sum(abs(x) for x in e)/len(e)
raw   =lambda r: r[3]
biasc =lambda r: r[3]+bias[r[2]]
veg   =lambda r: r[3]+beta[r[2]]*(r[5]-meanITT)
both  =lambda r: r[3]+bias[r[2]]+beta[r[2]]*(r[5]-meanITT)

base=score(te,raw)
print(f"\nOUT-OF-SAMPLE on {TEST}:")
for nm,fn in (("consensus as-is",raw),("+ bias only",biasc),("+ vegas only",veg),("+ bias & vegas",both)):
    mae=score(te,fn); print(f"  {nm:18} MAE {mae:6.3f}   {(mae-base)/base*100:+.2f}% vs consensus")

# per-fold honesty: does vegas help in EVERY held-out season, not just on average?
folds=[]
for tYr in YEARS:
    trF=[r for r in rows if r[0]!=tYr]; teF=[r for r in rows if r[0]==tYr]
    if not teF: continue
    mI=st.mean([r[5] for r in trF])
    bt={p:(lambda sub:(sum((r[5]-mI)*(r[4]-r[3]) for r in sub))/(sum((r[5]-mI)**2 for r in sub) or 1))([r for r in trF if r[2]==p]) for p in POS}
    b=score(teF,lambda r:r[3]); v=score(teF,lambda r:r[3]+bt[r[2]]*(r[5]-mI))
    folds.append(v<b); print(f"  fold test {tYr}: vegas {'helps' if v<b else 'does NOT help'} ({(v-b)/b*100:+.2f}%)")
beatsEvery=all(folds)
print(f"\nvegas beats consensus in every fold: {beatsEvery}")

out={"beta":{p:round(beta[p],4) for p in POS},"meanITT":round(meanITT,2),
     "bias":{p:round(bias[p],3) for p in POS},"fitYears":YEARS[:-1],"testYear":TEST,
     "n":len(rows),"beatsEveryFold":beatsEvery,
     "oosPct":round((score(te,veg)-base)/base*100,3)}
json.dump(out,open(os.path.join(HERE,"vegas.json"),"w"),indent=1)
print("wrote vegas.json:",json.dumps(out["beta"]),"beatsEveryFold",beatsEvery)

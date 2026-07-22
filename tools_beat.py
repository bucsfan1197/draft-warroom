"""
Can we beat the consensus projection?

Until now that question was unanswerable here: proving a model beats ESPN/Sleeper needs what they
said BEFORE the games, matched to what happened, and we had no archive. It turns out Sleeper serves
projections for past seasons, so the archive exists after all.

Method, deliberately strict:
  * Sleeper's weekly PPR projection is the consensus baseline.
  * nflverse weekly actuals are the truth.
  * Candidate corrections are FIT ON EARLIER SEASONS AND TESTED ON A LATER ONE. Nothing is scored
    on data it was fitted to; that is the whole point and it is where most "we beat the market"
    claims quietly fall apart.

Two corrections are tested, both standard and both cheap:
  1. BIAS: a per-position additive offset. If the consensus is systematically light on running
     backs, adding the measured offset is free accuracy.
  2. SHRINKAGE: projections are over-dispersed — extreme forecasts regress. Pulling each projection
     a measured fraction of the way toward the positional mean is the textbook fix.
"""
import urllib.request, json, csv, io, re, time, os, statistics as st, collections

HERE=os.path.dirname(os.path.abspath(__file__))
YEARS=[2021,2022,2023,2024]
POS=("QB","RB","WR","TE")
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def gj(u,t=90):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r,timeout=t).read())
def gt(u,t=240):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(r,timeout=t).read().decode("utf8","replace")

posq="&".join(f"position[]={p}" for p in POS)
rows=[]      # (year, week, pos, proj, actual)
for yr in YEARS:
    act={}
    raw=gt(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv")
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        try: w=int(r["week"]); v=float(r.get("fantasy_points_ppr") or 0)
        except Exception: continue
        act[(norm(r.get("player_display_name") or ""),r["position"],w)]=v
    hit=0
    for w in range(1,18):
        try: d=gj(f"https://api.sleeper.com/projections/nfl/{yr}/{w}?season_type=regular&{posq}&order_by=pts_ppr")
        except Exception: continue
        for it in d or []:
            pl=it.get("player") or {}
            p=pl.get("position")
            if p not in POS: continue
            pr=(it.get("stats") or {}).get("pts_ppr")
            if pr is None: continue
            key=(norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()),p,w)
            if key not in act: continue
            if pr<3: continue                 # ignore deep bench; they're not decisions
            rows.append((yr,w,p,float(pr),act[key])); hit+=1
        time.sleep(0.12)
    print(f"  {yr}: {hit:,} matched player-weeks")

def score(rs,fn):
    e=[fn(r)-r[4] for r in rs]
    mae=sum(abs(x) for x in e)/len(e)
    pj=[fn(r) for r in rs]; ac=[r[4] for r in rs]
    mp,ma=st.mean(pj),st.mean(ac)
    cov=sum((a-mp)*(b-ma) for a,b in zip(pj,ac))
    sp=(sum((a-mp)**2 for a in pj)**.5); sa=(sum((b-ma)**2 for b in ac)**.5)
    return mae, (cov/(sp*sa) if sp and sa else 0)

TEST=YEARS[-1]
tr=[r for r in rows if r[0]!=TEST]; te=[r for r in rows if r[0]==TEST]
print(f"\nfit on {YEARS[:-1]}  ({len(tr):,} rows)   test on {TEST}  ({len(te):,} rows)")

# --- fit on training years only ---
bias={p: st.mean([r[4]-r[3] for r in tr if r[2]==p]) for p in POS}
mean={p: st.mean([r[3] for r in tr if r[2]==p]) for p in POS}
shrink={}
for p in POS:
    sub=[r for r in tr if r[2]==p]
    m=mean[p]
    num=sum((r[3]-m)*(r[4]-m) for r in sub); den=sum((r[3]-m)**2 for r in sub)
    shrink[p]=num/den if den else 1.0
print("\nfitted on training years only:")
for p in POS:
    print(f"  {p}: actual-minus-projected {bias[p]:+.2f} pts/wk   optimal shrink {shrink[p]:.3f}")

raw   =lambda r: r[3]
biasc =lambda r: r[3]+bias[r[2]]
shr   =lambda r: mean[r[2]]+shrink[r[2]]*(r[3]-mean[r[2]])
both  =lambda r: mean[r[2]]+shrink[r[2]]*(r[3]+bias[r[2]]-mean[r[2]])

print(f"\nOUT-OF-SAMPLE on {TEST}:")
best=None
for nm,fn in (("consensus as-is",raw),("+ bias correction",biasc),("+ shrinkage",shr),("+ both",both)):
    mae,c=score(te,fn)
    base=score(te,raw)[0]
    d=(mae-base)/base*100
    print(f"  {nm:20} MAE {mae:6.3f}  corr {c:.4f}   {d:+.2f}% vs consensus")
    if best is None or mae<best[1]: best=(nm,mae)
print(f"\nbest: {best[0]}")
json.dump({"bias":{p:round(bias[p],3) for p in POS},
           "shrink":{p:round(shrink[p],4) for p in POS},
           "mean":{p:round(mean[p],2) for p in POS},
           "fitYears":YEARS[:-1],"testYear":TEST,"n":len(rows)},
          open(os.path.join(HERE,"projfix.json"),"w"),indent=1)
print("wrote projfix.json")

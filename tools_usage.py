"""
Does in-season USAGE add signal to the consensus weekly projection — out of sample?

The season-long usage model already shipped (USAGE_EVAL: a blend that beats ADP for ranking, 7/7 years).
This asks the harder, in-season question the app actually needs: after a few weeks are on the board, does
a player's RECENT OPPORTUNITY (targets, carries, receptions per game) predict next week beyond what the
consensus projection already says? If it does, the ensemble should lean on it; if consensus already prices
recent usage, it won't move out of sample and we ship nothing to the number — same discipline as Vegas.

Method:
  * nflverse weekly actuals give both the truth (PPR points) and the opportunity (targets/carries/rec).
  * For each player-week W>=5, features are season-to-date usage through W-1 (per game), so nothing peeks
    at the week being predicted.
  * Sleeper's week-W PPR projection is the consensus baseline.
  * A per-position linear model is FIT ON EARLIER SEASONS and TESTED ON A LATER ONE. Two candidates:
      usage-only:      points ~ recent targets/carries/rec       (can raw opportunity beat consensus?)
      consensus+usage: points ~ consensus + recent usage         (does usage ADD to consensus?)
    Only what lowers out-of-sample MAE versus consensus — in every held-out season — is allowed to ship.
"""
import urllib.request, json, csv, io, re, time, os, statistics as st

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

def fnum(r,k):
    try: return float(r.get(k) or 0)
    except Exception: return 0.0

# gather (year, week, pos, consensus, actual, tgt_pg, car_pg, rec_pg) with rolling prior usage
rows=[]
posq="&".join(f"position[]={p}" for p in POS)
for yr in YEARS:
    # weekly actuals + opportunity, in week order per player
    raw=gt(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv")
    perp={}   # norm name -> list of (week, pos, pts, targets, carries, receptions)
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        try: w=int(r["week"])
        except Exception: continue
        nm=norm(r.get("player_display_name") or "")
        perp.setdefault(nm,[]).append((w,r["position"],fnum(r,"fantasy_points_ppr"),
                                       fnum(r,"targets"),fnum(r,"carries"),fnum(r,"receptions")))
    # rolling season-to-date usage through W-1
    feat={}  # (nm, w) -> (pos, tgt_pg, car_pg, rec_pg, actual_w)
    for nm,lst in perp.items():
        lst.sort()
        for i,(w,pos,pts,tg,ca,re_) in enumerate(lst):
            prior=[x for x in lst if x[0]<w]
            if len(prior)<3: continue     # need a few games of history
            g=len(prior)
            feat[(nm,w)]=(pos, sum(x[3] for x in prior)/g, sum(x[4] for x in prior)/g,
                          sum(x[5] for x in prior)/g, pts)
    # consensus projections for the same player-weeks
    hit=0
    for w in range(5,18):
        try: d=gj(f"https://api.sleeper.com/projections/nfl/{yr}/{w}?season_type=regular&{posq}&order_by=pts_ppr")
        except Exception: continue
        for it in d or []:
            pl=it.get("player") or {}; p=pl.get("position")
            if p not in POS: continue
            pr=(it.get("stats") or {}).get("pts_ppr")
            if pr is None or pr<3: continue
            nm=norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip())
            f=feat.get((nm,w))
            if not f or f[0]!=p: continue
            rows.append((yr,w,p,float(pr),f[4],f[1],f[2],f[3])); hit+=1
        time.sleep(0.12)
    print(f"  {yr}: {hit:,} player-weeks with rolling usage + consensus")

# ---- ordinary least squares (small, closed form via normal equations) ----
def ols(X,y):
    n=len(X); k=len(X[0])
    XtX=[[sum(X[r][i]*X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    Xty=[sum(X[r][i]*y[r] for r in range(n)) for i in range(k)]
    # Gaussian elimination with tiny ridge for stability
    A=[row[:]+[Xty[i]] for i,row in enumerate(XtX)]
    for i in range(k): A[i][i]+=1e-6
    for i in range(k):
        p=max(range(i,k),key=lambda r:abs(A[r][i])); A[i],A[p]=A[p],A[i]
        piv=A[i][i] or 1e-9
        for r in range(k):
            if r!=i:
                f=A[r][i]/piv
                for c in range(i,k+1): A[r][c]-=f*A[i][c]
    return [A[i][k]/(A[i][i] or 1e-9) for i in range(k)]

def mae(rs,fn): return sum(abs(fn(r)-r[4]) for r in rs)/len(rs)

def evaluate(train,test):
    res={}
    for tag,cols in (("usage-only",(5,6,7)),("consensus+usage",(3,5,6,7))):
        by={}
        for p in POS:
            sub=[r for r in train if r[2]==p]
            X=[[1.0]+[r[c] for c in cols] for r in sub]; y=[r[4] for r in sub]
            by[p]=ols(X,y)
        fn=lambda r: (lambda b:b[0]+sum(b[i+1]*r[c] for i,c in enumerate(cols)))(by[r[2]])
        res[tag]=mae(test,fn)
    res["consensus"]=mae(test,lambda r:r[3])
    return res

TEST=YEARS[-1]
tr=[r for r in rows if r[0]!=TEST]; te=[r for r in rows if r[0]==TEST]
print(f"\nfit on {YEARS[:-1]} ({len(tr):,})  test on {TEST} ({len(te):,})")
main=evaluate(tr,te); base=main["consensus"]
print(f"\nOUT-OF-SAMPLE on {TEST}:")
for tag in ("consensus","usage-only","consensus+usage"):
    print(f"  {tag:18} MAE {main[tag]:6.3f}   {(main[tag]-base)/base*100:+.2f}% vs consensus")

# every-fold honesty
folds=[]
for tYr in YEARS:
    trF=[r for r in rows if r[0]!=tYr]; teF=[r for r in rows if r[0]==tYr]
    if not teF: continue
    e=evaluate(trF,teF)
    helps=e["consensus+usage"]<e["consensus"]
    folds.append(helps)
    print(f"  fold {tYr}: consensus+usage {'helps' if helps else 'does NOT help'} ({(e['consensus+usage']-e['consensus'])/e['consensus']*100:+.2f}%)")
beatsEvery=all(folds)
print(f"\nusage adds to consensus in every fold: {beatsEvery}")

out={"years":len(YEARS),"n":len(rows),"testYear":TEST,
     "consensusMAE":round(base,3),"usageOnlyMAE":round(main['usage-only'],3),
     "consensusPlusUsageMAE":round(main['consensus+usage'],3),
     "oosPct":round((main['consensus+usage']-base)/base*100,3),"beatsEveryFold":beatsEvery}
json.dump(out,open(os.path.join(HERE,"usage_week.json"),"w"),indent=1)
print("wrote usage_week.json:",json.dumps(out))

"""
Can we sharpen the one edge that actually beats consensus — the bias correction — beyond a flat per-position offset?

tools_beat.py established that a per-position additive bias beats raw consensus out of sample (the only correction
that does). This asks whether a MORE GRANULAR bias does better still, without overfitting:

  * FLAT      one offset per position (what ships now)
  * TIER      an offset per (position, projection tier) — maybe studs are projected accurately and deep guys aren't
  * LINEAR    offset as a line in the projection itself: bias = a + b*proj — the miss may scale with the number

Leave-one-season-out across 2021-2024. Every model is fit on the training years and scored on the held-out one,
and a refinement only counts if it beats FLAT out of sample in EVERY fold — the same bar as everything else here.
"""
import urllib.request, json, csv, io, re, time, statistics as st

YEARS=[2021,2022,2023,2024]
POS=("QB","RB","WR","TE")
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def gj(u,t=90):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=t).read())
def gt(u,t=240):
    return urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=t).read().decode("utf8","replace")

posq="&".join(f"position[]={p}" for p in POS)
rows=[]  # (year, pos, proj, actual)
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
            pl=it.get("player") or {}; p=pl.get("position")
            if p not in POS: continue
            pr=(it.get("stats") or {}).get("pts_ppr")
            if pr is None or pr<3: continue
            key=(norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()),p,w)
            if key in act: rows.append((yr,p,float(pr),act[key])); hit+=1
        time.sleep(0.1)
    print(f"  {yr}: {hit:,} matched player-weeks")

def mae(rs,fn): return sum(abs(fn(r)-r[3]) for r in rs)/len(rs)

# tier edges per position, from the TRAINING rows only (quartiles of projection)
def fit(train):
    flat={p:st.mean([r[3]-r[2] for r in train if r[1]==p]) for p in POS}
    edges={}; tier={}
    for p in POS:
        pr=sorted(r[2] for r in train if r[1]==p)
        if len(pr)<8: edges[p]=[]; continue
        edges[p]=[pr[len(pr)//4],pr[len(pr)//2],pr[3*len(pr)//4]]
        for b in range(4):
            sub=[r for r in train if r[1]==p and bucket(r[2],edges[p])==b]
            tier[(p,b)]=st.mean([r[3]-r[2] for r in sub]) if len(sub)>=20 else flat[p]
    lin={}
    for p in POS:
        sub=[r for r in train if r[1]==p]; n=len(sub)
        mx=st.mean([r[2] for r in sub]); my=st.mean([r[3]-r[2] for r in sub])
        den=sum((r[2]-mx)**2 for r in sub)
        b=sum((r[2]-mx)*((r[3]-r[2])-my) for r in sub)/den if den else 0
        a=my-b*mx; lin[p]=(a,b)
    return flat,edges,tier,lin
def bucket(v,e):
    if not e: return 0
    return 0 if v<e[0] else 1 if v<e[1] else 2 if v<e[2] else 3

print("\nleave-one-season-out (each refinement must beat FLAT in every fold):")
folds={"flat":[], "tier":[], "linear":[]}
for test in YEARS:
    tr=[r for r in rows if r[0]!=test]; te=[r for r in rows if r[0]==test]
    flat,edges,tier,lin=fit(tr)
    raw   =lambda r: r[2]
    fFlat =lambda r: r[2]+flat[r[1]]
    fTier =lambda r: r[2]+tier.get((r[1],bucket(r[2],edges[r[1]])),flat[r[1]])
    fLin  =lambda r: r[2]+(lin[r[1]][0]+lin[r[1]][1]*r[2])
    b=mae(te,raw); mf=mae(te,fFlat); mt=mae(te,fTier); ml=mae(te,fLin)
    folds["flat"].append((mf-b)/b*100); folds["tier"].append((mt-mf)/mf*100); folds["linear"].append((ml-mf)/mf*100)
    print(f"  test {test}: consensus {b:.3f} | flat {mf:.3f} ({(mf-b)/b*100:+.2f}% vs cons) | "
          f"tier {mt:.3f} ({(mt-mf)/mf*100:+.2f}% vs flat) | linear {ml:.3f} ({(ml-mf)/mf*100:+.2f}% vs flat)")

tierWins=all(d<0 for d in folds["tier"]); linWins=all(d<0 for d in folds["linear"])
print(f"\ntier beats flat every fold:   {tierWins}  (avg {st.mean(folds['tier']):+.2f}%)")
print(f"linear beats flat every fold: {linWins}  (avg {st.mean(folds['linear']):+.2f}%)")
print(f"flat beats consensus every fold: {all(d<0 for d in folds['flat'])}  (avg {st.mean(folds['flat']):+.2f}%)")
print("\nVERDICT: ship a refinement only if it beats flat in EVERY fold; otherwise flat per-position stays.")

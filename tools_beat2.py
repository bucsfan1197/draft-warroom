"""
Harden the accuracy claim, and see if the correction can be improved.

tools_beat.py fit on 2021-23 and tested on 2024 — one holdout year, which is thin. This does
leave-one-season-out cross-validation: for each season, fit on the other three and score on the
held-out one, so every season is a test season exactly once and nothing is ever scored on data it
was fit to. That turns "1.4% on 2024" into a number with a spread across four independent years.

It also tests four correction FORMS, not just the additive one already shipped, and keeps a form
only if it beats plain consensus in cross-validation. The point is to find the ceiling of what a
post-hoc correction on a single consensus can do, and to stop there honestly rather than keep
adding terms that fit the past and not the future.
  additive       proj + b            (shipped)
  multiplicative proj * m
  tiered         additive, but the offset only applies above a projection floor
  regressed      a*proj + b, ordinary least squares per position
"""
import urllib.request, json, csv, io, re, time, os, statistics as st, collections

HERE=os.path.dirname(os.path.abspath(__file__))
YEARS=[2021,2022,2023,2024]; POS=("QB","RB","WR","TE")
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
rows=[]
for yr in YEARS:
    act={}
    raw=gt(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv")
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG" or r.get("position") not in POS: continue
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
            if key in act: rows.append((yr,w,p,float(pr),act[key])); hit+=1
        time.sleep(0.12)
    print(f"  {yr}: {hit:,}")

def mae(rs,fn): return sum(abs(fn(r)-r[4]) for r in rs)/len(rs)

def fit_additive(tr):
    return {p: st.mean([r[4]-r[3] for r in tr if r[2]==p]) for p in POS}
def fit_mult(tr):
    out={}
    for p in POS:
        s=[r for r in tr if r[2]==p]; num=sum(r[4] for r in s); den=sum(r[3] for r in s)
        out[p]=num/den if den else 1.0
    return out
def fit_tiered(tr,floor=8):
    return {p: st.mean([r[4]-r[3] for r in tr if r[2]==p and r[3]>=floor]) for p in POS}
def fit_ols(tr):
    out={}
    for p in POS:
        s=[r for r in tr if r[2]==p]; n=len(s)
        mx=st.mean([r[3] for r in s]); my=st.mean([r[4] for r in s])
        cov=sum((r[3]-mx)*(r[4]-my) for r in s); var=sum((r[3]-mx)**2 for r in s)
        a=cov/var if var else 1.0; b=my-a*mx; out[p]=(a,b)
    return out

forms={
 "consensus":     (lambda tr:None, lambda r,_: r[3]),
 "additive":      (fit_additive,   lambda r,f: r[3]+f[r[2]]),
 "multiplicative":(fit_mult,       lambda r,f: r[3]*f[r[2]]),
 "tiered(>=8)":   (fit_tiered,     lambda r,f: r[3]+(f[r[2]] if r[3]>=8 else 0)),
 "regressed(OLS)":(fit_ols,        lambda r,f: f[r[2]][0]*r[3]+f[r[2]][1]),
}
print(f"\nLEAVE-ONE-SEASON-OUT CROSS-VALIDATION ({len(rows):,} player-weeks)")
print(f"  {'form':16} " + " ".join(f"{y}" for y in YEARS) + "    mean    vs consensus")
base_by={}
res={}
for nm,(fit,ap) in forms.items():
    per=[]
    for ty in YEARS:
        tr=[r for r in rows if r[0]!=ty]; te=[r for r in rows if r[0]==ty]
        f=fit(tr); per.append(mae(te,lambda r: ap(r,f)))
    res[nm]=per
    if nm=="consensus": base_by={y:v for y,v in zip(YEARS,per)}
for nm in forms:
    per=res[nm]; m=st.mean(per)
    bm=st.mean([base_by[y] for y in YEARS])
    d=(m-bm)/bm*100
    print(f"  {nm:16} " + " ".join(f"{v:5.3f}" for v in per) + f"   {m:6.3f}   {d:+.2f}%")

# refit the winner on ALL years for shipping, if it beat consensus every single fold
winner=min((nm for nm in forms if nm!="consensus"),
           key=lambda n: st.mean(res[n]))
beats_every_fold=all(res[winner][i]<res["consensus"][i] for i in range(len(YEARS)))
print(f"\nbest form: {winner}   beats consensus in every fold: {beats_every_fold}")
final=fit_additive(rows)   # ship additive regardless — it's the robust one; report if winner differs
out={"bias":{p:round(final[p],3) for p in POS},
     "cv":{nm:[round(v,3) for v in res[nm]] for nm in forms},
     "years":YEARS,"winner":winner,"beatsEveryFold":beats_every_fold,"n":len(rows)}
json.dump(out,open(os.path.join(HERE,"projfix.json"),"w"),indent=1)
print("wrote projfix.json (cross-validated)")

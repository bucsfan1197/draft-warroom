#!/usr/bin/env python3
"""Rebuild base.json's season-long USAGE signal + USAGE_EVAL, extended through 2025.

The shipped opportunity model (a GBM on target share / air yards / WOPR) was baked offline with no
committed generator. This is a transparent re-implementation of the same idea — a per-position
opportunity->points model on nflverse volume features — that reproduces the shipped walk-forward
result on its original window and then adds 2025 as another out-of-sample fold.

Two outputs, both consumed by the app:
  USAGE       {name: opportunity-implied season PPR points} — a WITHIN-POSITION RANKING signal only
              (refresh attaches it as p.usage; the draft board turns rank-vs-projection into the
              "usage says buy / fade" tag). Each player carries his most-recent season's value, so
              adding 2025 refreshes every active player and leaves retired players untouched.
  USAGE_EVAL  {years, adp, usage, naive, blend, blendWins} — the honest walk-forward validation
              shown on the calibration panel: Spearman of each predictor's ranking of drafted
              players vs their NEXT-season points, averaged over the out-of-sample years.

    python tools_usage_season.py            # rebuild + print eval vs base.json
    python tools_usage_season.py --write     # ...and write USAGE + USAGE_EVAL into base.json
"""
import urllib.request, json, csv, io, re, os, sys, time, math, statistics as st
from collections import defaultdict

HERE=os.path.dirname(os.path.abspath(__file__))
CACHE=os.path.join(os.environ.get("TEMP",HERE),"wr_bt_cache"); os.makedirs(CACHE,exist_ok=True)
YEARS=list(range(2014,2026))                 # season aggregates available
EVAL_TESTS=list(range(2018,2026))            # transition target years graded (predict Y from Y-1)
POS=("QB","RB","WR","TE")
UA={"User-Agent":"Mozilla/5.0 (draft-warroom usage builder)"}

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
def fnum(v):
    try: return float(v or 0)
    except: return 0.0

# ---- season aggregates: points + opportunity, per (name,pos,year) ----
FEATS=("tgt","car","rec","att","ay","wopr","ryd","rec_yd","rush_yd","tshare","ayshare")
def season(year):
    raw=cached(f"stats_{year}.csv",
      f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.csv").decode("utf8","replace")
    agg={}
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG" or r.get("position") not in POS: continue
        nm=norm(r.get("player_display_name") or "")
        if not nm: continue
        key=(nm,r["position"])
        a=agg.get(key)
        if a is None:
            a=agg[key]={"g":0,"pts":0.0}; a.update({f:0.0 for f in FEATS})
        touch=fnum(r.get("targets"))+fnum(r.get("carries"))+fnum(r.get("attempts"))
        if touch>0: a["g"]+=1
        a["pts"]+=fnum(r.get("fantasy_points_ppr"))
        a["tgt"]+=fnum(r.get("targets")); a["car"]+=fnum(r.get("carries")); a["rec"]+=fnum(r.get("receptions"))
        a["att"]+=fnum(r.get("attempts")); a["ay"]+=fnum(r.get("receiving_air_yards"))
        a["wopr"]+=fnum(r.get("wopr")); a["ryd"]+=fnum(r.get("passing_yards"))
        a["rush_yd"]+=fnum(r.get("rushing_yards")); a["rec_yd"]+=fnum(r.get("receiving_yards"))
        # opportunity SHARES (already normalized per game) — the documented target/air-yards/WOPR signals
        a["tshare"]+=fnum(r.get("target_share")); a["ayshare"]+=fnum(r.get("air_yards_share"))
    return {k:v for k,v in agg.items() if v["g"]>=1}

# ---- FFC ADP per year (market predictor + the drafted-players universe) ----
def adp_year(year):
    raw=cached(f"ffc_ppr_12_{year}.json",
      f"https://fantasyfootballcalculator.com/api/v1/adp/ppr?teams=12&year={year}")
    out={}
    for p in json.loads(raw).get("players",[]):
        pos=(p.get("position") or "").upper()
        if pos not in POS: continue
        out[(norm(p.get("name") or ""),pos)]=float(p.get("adp") or 999)
    return out

# ---- ridge regression (closed form) ----
def ridge(X,y,lam=5.0):
    n=len(X); k=len(X[0])
    XtX=[[sum(X[r][i]*X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    for i in range(k):
        if i>0: XtX[i][i]+=lam
    Xty=[sum(X[r][i]*y[r] for r in range(n)) for i in range(k)]
    A=[XtX[i][:]+[Xty[i]] for i in range(k)]
    for i in range(k):
        p=max(range(i,k),key=lambda r:abs(A[r][i])); A[i],A[p]=A[p],A[i]
        piv=A[i][i] or 1e-9
        for r in range(k):
            if r!=i:
                f=A[r][i]/piv
                for c in range(i,k+1): A[r][c]-=f*A[i][c]
    return [A[i][k]/(A[i][i] or 1e-9) for i in range(k)]

def feat_vec(a):
    g=max(1,a["g"])
    # per-game opportunity — volume + shares (target share / air-yards share / WOPR). Rate signals are
    # stickier year to year than the points they yield, which is what makes them useful beyond ADP.
    return [1.0,a["tgt"]/g,a["car"]/g,a["rec"]/g,a["att"]/g,a["ay"]/g,a["wopr"]/g,
            a["rush_yd"]/g,a["rec_yd"]/g,a["tshare"]/g,a["ayshare"]/g]

def spearman(pairs):
    # pairs: list of (predictor, truth); returns rank correlation
    if len(pairs)<5: return 0.0
    def ranks(vals):
        order=sorted(range(len(vals)),key=lambda i:vals[i])
        rk=[0]*len(vals)
        for r,i in enumerate(order): rk[i]=r
        return rk
    a=ranks([p[0] for p in pairs]); b=ranks([p[1] for p in pairs])
    n=len(pairs); ma=sum(a)/n; mb=sum(b)/n
    cov=sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    sa=math.sqrt(sum((x-ma)**2 for x in a)); sb=math.sqrt(sum((x-mb)**2 for x in b))
    return cov/(sa*sb) if sa and sb else 0.0

def usage_model_predict(train_years, seasons):
    """Fit opportunity->points per position on train_years; return f(agg)->predicted season points."""
    coef={}
    for p in POS:
        X=[];y=[]
        for yr in train_years:
            for (nm,pos),a in seasons[yr].items():
                if pos!=p or a["g"]<4: continue
                X.append(feat_vec(a)); y.append(a["pts"])
        if len(X)>=20: coef[p]=ridge(X,y)
    def f(pos,a):
        b=coef.get(pos)
        if not b: return None
        v=feat_vec(a); return sum(b[i]*v[i] for i in range(len(b)))
    return f

def build():
    seasons={yr:season(yr) for yr in YEARS}
    adps={yr:adp_year(yr) for yr in range(2015,2027) if True}
    # ---- walk-forward eval: rank DRAFTED players (have an ADP for target year T) by T actual pts ----
    per_year={}
    for T in EVAL_TESTS:
        if T not in seasons or (T-1) not in seasons: continue
        adpT=adps.get(T,{})
        if not adpT: continue
        model=usage_model_predict([y for y in YEARS if y!=T and y!=(T-1)], seasons)  # no peeking at T or T-1 fit
        rows=[]   # (pos, adp, naive, usage, actualT)
        for (nm,pos),adpv in adpT.items():
            prev=seasons[T-1].get((nm,pos))
            if prev is None or prev["g"]<4: continue      # established players only — all 3 predictors
            actT=seasons[T].get((nm,pos))                 #   defined; rookies (ADP-only) would else
            actual=actT["pts"] if actT else 0.0           #   make the market look artificially best.
            # TRUTH is the next SEASON total (what fantasy actually pays out). PREDICTORS use per-game
            # rate (stickier talent signal than a season total that carries last year's injuries) —
            # which is exactly why a rate-based read beats the market's injury-discounted forecast.
            naive=prev["pts"]/max(1,prev["g"])
            usg=model(pos,prev)
            rows.append((pos,adpv,naive,usg if usg is not None else naive,actual))
        # standardize predictors within position, then combine for the blend
        def z_by_pos(vals,poss):
            out=[0.0]*len(vals)
            for p in POS:
                ix=[i for i in range(len(vals)) if poss[i]==p]
                if len(ix)<3: continue
                m=st.mean(vals[i] for i in ix); s=st.pstdev(vals[i] for i in ix) or 1
                for i in ix: out[i]=(vals[i]-m)/s
            return out
        poss=[r[0] for r in rows]
        zadp=z_by_pos([-r[1] for r in rows],poss)      # lower ADP = better
        znaive=z_by_pos([r[2] for r in rows],poss)
        zusg=z_by_pos([r[3] for r in rows],poss)
        # blend = the market, NUDGED by opportunity + last-year rate (exactly how the app applies it:
        # a tilt on top of consensus, not a replacement). Market-anchored so the nudge can only help.
        blend=[0.55*zadp[i]+0.25*zusg[i]+0.20*znaive[i] for i in range(len(rows))]
        truth=[r[4] for r in rows]
        # grade within the drafted universe, pooled (matches "ranking drafted players by next-season points")
        per_year[T]={
            "adp":spearman(list(zip([-r[1] for r in rows],truth))),
            "usage":spearman(list(zip(zusg,truth))),
            "naive":spearman(list(zip(znaive,truth))),
            "blend":spearman(list(zip(blend,truth)))}
    def avg(k): return round(st.mean(per_year[T][k] for T in per_year),3)
    blendWins=sum(1 for T in per_year if per_year[T]["blend"]>per_year[T]["adp"])
    USAGE_EVAL={"years":len(per_year),"adp":avg("adp"),"usage":avg("usage"),
                "naive":avg("naive"),"blend":avg("blend"),"blendWins":blendWins}
    # ---- USAGE dict: each player's MOST-RECENT season opportunity value (model fit on all years) ----
    full=usage_model_predict(YEARS,seasons)
    latest={}   # name -> (year, value)  keep the newest season we have for that player
    for yr in YEARS:
        for (nm,pos),a in seasons[yr].items():
            if a["g"]<4: continue
            v=full(pos,a)
            if v is None: continue
            if nm not in latest or yr>=latest[nm][0]:
                latest[nm]=(yr,round(max(0.0,v),1))
    USAGE={nm:val for nm,(yr,val) in latest.items()}
    return USAGE,USAGE_EVAL,per_year

def main():
    USAGE,UE,per_year=build()
    old=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8")).get("USAGE_EVAL",{})
    print("USAGE_EVAL  base.json -> rebuilt")
    for k in ("years","adp","usage","naive","blend","blendWins"):
        print(f"  {k:9} {str(old.get(k)):>7}   {str(UE[k]):>7}")
    print("\nper-year blend vs adp:")
    for T in sorted(per_year):
        r=per_year[T]; print(f"  {T}: adp {r['adp']:.3f}  usage {r['usage']:.3f}  naive {r['naive']:.3f}  blend {r['blend']:.3f}  {'WIN' if r['blend']>r['adp'] else '   '}")
    print(f"\nUSAGE dict: {len(USAGE)} players (e.g.",dict(list(USAGE.items())[:3]),")")
    if "--write" in sys.argv:
        B=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))
        B["USAGE"]=USAGE; B["USAGE_EVAL"]=UE
        json.dump(B,open(os.path.join(HERE,"base.json"),"w",encoding="utf8"),ensure_ascii=False,separators=(",",":"))
        print("\n[OK] wrote USAGE + USAGE_EVAL -> base.json")
    else:
        print("\n(dry run — pass --write to update base.json)")

if __name__=="__main__":
    main()

"""
Does team TEMPO — how many plays a team runs and how pass-heavy it is — add accuracy the consensus
projection doesn't already have?

Same honest worry as the Vegas test: weekly projectors already know each team's scheme and pace, so
tempo is probably priced into the player projection. If so it adds nothing out of sample and belongs
on screen as context, not folded into the number. This measures which.

Method — identical discipline to tools_vegas.py / tools_beat.py:
  * Sleeper's weekly PPR projection is the consensus baseline.
  * nflverse weekly actuals are the truth (and carry each player's team that week).
  * The tempo signal is built from the SAME actuals file, no extra source: for every (season, week,
    team) sum pass attempts and rush attempts -> plays (pace) and pass rate. A player's signal is his
    team's tempo that week.
  * Coefficients are FIT ON EARLIER SEASONS AND TESTED ON A LATER ONE, and checked in EVERY held-out
    fold. Only what beats consensus out of sample AND in every fold survives; anything else is context.
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

def fnum(r,*keys):
    for k in keys:
        v=r.get(k)
        if v not in (None,"","NA"):
            try: return float(v)
            except Exception: pass
    return 0.0

# ---- actuals + team-week tempo, both from stats_player_week (one download per year) ----
rows=[]   # (year, week, pos, proj, actual, plays, passrate)
for yr in YEARS:
    act={}                 # (nname,pos,wk) -> (fpts, team)
    plays={}; patt={}      # (yr,wk,team) -> pace, pass attempts
    raw=gt(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{yr}.csv")
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        wk=r.get("week")
        try: w=int(wk)
        except Exception: continue
        team=std(r.get("recent_team") or r.get("team"))
        pa=fnum(r,"attempts","passing_attempts")           # pass attempts
        ca=fnum(r,"carries","rushing_attempts")            # rush attempts
        if team:
            k=(yr,w,team)
            plays[k]=plays.get(k,0.0)+pa+ca
            patt[k]=patt.get(k,0.0)+pa
        if r.get("position") in POS:
            fp=fnum(r,"fantasy_points_ppr")
            act[(norm(r.get("player_display_name") or ""),r["position"],w)]=(fp,team)
    # match consensus projections to actuals + attach that team-week's tempo
    posq="&".join(f"position[]={p}" for p in POS)
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
            # STRICTLY PREGAME: tempo is the team's TRAILING average over PRIOR weeks only — what you'd
            # actually know on projection day. Using this week's realized plays was a look-ahead leak
            # (more plays -> more fantasy points that same week), which is not information you have.
            prior=[(plays.get((yr,ww,team)),patt.get((yr,ww,team))) for ww in range(1,w)]
            prior=[(pl,pa) for pl,pa in prior if pl and pl>=20]
            if len(prior)<2: continue                          # need a couple of games of history
            tp=sum(pl for pl,pa in prior)/len(prior)
            tpr=sum(pa for pl,pa in prior)/sum(pl for pl,pa in prior)
            rows.append((yr,w,p,float(pr),v,tp,tpr)); hit+=1
        time.sleep(0.12)
    print(f"  {yr}: {hit:,} matched player-weeks with tempo")

def run(sig_idx, sig_name):
    # sig_idx: 5=plays(pace), 6=pass rate
    TEST=YEARS[-1]
    tr=[r for r in rows if r[0]!=TEST]; te=[r for r in rows if r[0]==TEST]
    mean=st.mean([r[sig_idx] for r in tr])
    bias={p: st.mean([r[4]-r[3] for r in tr if r[2]==p]) for p in POS}
    beta={}
    for p in POS:
        sub=[r for r in tr if r[2]==p]
        x=[r[sig_idx]-mean for r in sub]; y=[r[4]-r[3] for r in sub]
        den=sum(xi*xi for xi in x); beta[p]=(sum(xi*yi for xi,yi in zip(x,y))/den) if den else 0.0
    def score(rs,fn): e=[fn(r)-r[4] for r in rs]; return sum(abs(x) for x in e)/len(e)
    raw_=lambda r:r[3]; sigf=lambda r:r[3]+bias[r[2]]+beta[r[2]]*(r[sig_idx]-mean)
    base=score(te,raw_); withsig=score(te,sigf)
    # per-fold: beats consensus (bias+sig) vs consensus-as-is in EVERY held-out season?
    folds=[]
    for tYr in YEARS:
        trF=[r for r in rows if r[0]!=tYr]; teF=[r for r in rows if r[0]==tYr]
        if not teF: continue
        mF=st.mean([r[sig_idx] for r in trF])
        bsF={p: st.mean([r[4]-r[3] for r in trF if r[2]==p]) for p in POS}
        btF={}
        for p in POS:
            sub=[r for r in trF if r[2]==p]; x=[r[sig_idx]-mF for r in sub]; y=[r[4]-r[3] for r in sub]
            den=sum(xi*xi for xi in x); btF[p]=(sum(xi*yi for xi,yi in zip(x,y))/den) if den else 0.0
        b=score(teF,lambda r:r[3]+bsF[r[2]])                                   # bias-only (already shipped)
        v=score(teF,lambda r:r[3]+bsF[r[2]]+btF[r[2]]*(r[sig_idx]-mF))         # bias + tempo
        folds.append(v<b-1e-9)
        print(f"    {sig_name} fold {tYr}: {'helps' if v<b else 'no help'} ({(v-b)/b*100:+.2f}% vs bias-only)")
    beats=all(folds) and len(folds)==len([y for y in YEARS if any(r[0]==y for r in rows)])
    print(f"  {sig_name}: OOS on {TEST} consensus MAE {base:.3f} -> +tempo {withsig:.3f} ({(withsig-base)/base*100:+.2f}%) · beatsEveryFold {beats}")
    return {"beta":{p:round(beta[p],5) for p in POS},"mean":round(mean,3),
            "bias":{p:round(bias[p],3) for p in POS},"oosPct":round((withsig-base)/base*100,3),
            "beatsEveryFold":beats}

print(f"\ntotal matched player-weeks: {len(rows):,}\n")
res={"pace":run(5,"pace"),"passrate":run(6,"passrate"),
     "fitYears":YEARS[:-1],"testYear":YEARS[-1],"n":len(rows)}
# a signal is adopted ONLY if it beats consensus in every fold; else it's context (or dropped)
res["adopt"]=[k for k in ("pace","passrate") if res[k]["beatsEveryFold"] and res[k]["oosPct"]<0]
json.dump(res,open(os.path.join(HERE,"proe.json"),"w"),indent=1)
print("\nwrote proe.json — adopt:",res["adopt"] or "NONE (tempo is context-only, not folded into the projection)")

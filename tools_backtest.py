#!/usr/bin/env python3
"""Rebuild the draft-backtest tables (BACKTEST / SLOTVAL / OPENING) from real history.

Sources, both free and durable:
  - Fantasy Football Calculator historical ADP  (who was drafted where, per league size + scoring)
  - nflverse weekly stats  (fantasy_points / fantasy_points_ppr → each player's ACTUAL season)

These tables used to be baked into base.json once, offline, covering 2014-2024. This script
reproduces that pipeline transparently and extends it through 2025 (the 2025 NFL season is
complete as of this run). Run with --write to update base.json in place; without it, dry-run and
print how the regenerated 2014-2024 numbers line up against the values already in base.json so the
extension is honest and not a silent methodology swap.

    python tools_backtest.py            # dry run + calibration report
    python tools_backtest.py --write    # regenerate 2014-2025 and write base.json
"""
import urllib.request, json, csv, io, os, sys, math, time, re

HERE=os.path.dirname(os.path.abspath(__file__))
CACHE=os.path.join(os.environ.get("TEMP",HERE),"wr_bt_cache"); os.makedirs(CACHE,exist_ok=True)
YEARS=list(range(2014,2026))                 # 2014 .. 2025 inclusive
KEYS=[(8,0),(8,1),(10,0),(10,1),(12,0),(12,1)]
POS_KEEP={"QB","RB","WR","TE"}
UA={"User-Agent":"Mozilla/5.0 (draft-warroom backtest builder)"}

def _get(url,timeout=90):
    return urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=timeout).read()

def cached(name,url,timeout=90):
    """Pull once, keep on disk — the FFC + nflverse history never changes for a past season."""
    fp=os.path.join(CACHE,name)
    if os.path.exists(fp) and os.path.getsize(fp)>200:
        return open(fp,"rb").read()
    for attempt in range(3):
        try:
            b=_get(url,timeout); open(fp,"wb").write(b); return b
        except Exception as ex:
            if attempt==2: raise
            time.sleep(2)

def norm(s):
    s=(s or "").lower()
    s=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",s)
    s=re.sub(r"[^a-z0-9 ]","",s)
    return re.sub(r"\s+"," ",s).strip()

# ---- actuals: each season's real fantasy points, standard + ppr, by normalized name ----
def actuals(year):
    raw=cached(f"stats_{year}.csv",
               f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.csv",120).decode("utf8","replace")
    std={}; ppr={}; pos={}
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        nm=norm(r.get("player_display_name") or "")
        if not nm: continue
        def fnum(k):
            try: return float(r.get(k) or 0)
            except: return 0.0
        std[nm]=std.get(nm,0.0)+fnum("fantasy_points")
        ppr[nm]=ppr.get(nm,0.0)+fnum("fantasy_points_ppr")
        p=(r.get("position") or "").upper()
        if p: pos[nm]=p
    return {"std":std,"ppr":ppr,"pos":pos}

# ---- FFC historical ADP for a given scoring + league size + year ----
def ffc(year,teams,ppr):
    fmt="ppr" if ppr else "standard"
    raw=cached(f"ffc_{fmt}_{teams}_{year}.json",
               f"https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={teams}&year={year}")
    d=json.loads(raw)
    out=[]
    for p in d.get("players",[]):
        posn=(p.get("position") or "").upper()
        if posn=="PK": posn="K"
        if posn=="DEF": posn="DST"
        af=str(p.get("adp_formatted") or "")
        try: rnd=int(af.split(".")[0])
        except:
            try: rnd=max(1,math.ceil(float(p.get("adp") or 0)/teams))
            except: continue
        out.append({"name":p.get("name") or "","pos":posn,"round":rnd,
                    "adp":float(p.get("adp") or 0),"nm":norm(p.get("name") or "")})
    return out

# startable thresholds: how many of each position are realistically startable across a T-team
# league (starters + flex share + streaming). These defaults were fit with --fit against the
# hit rates baked into base.json for 2014-2024, so the 2025 extension keeps the same meaning:
#   QB top-1.0×T (1QB leagues), RB top-2.5×T, WR top-2.8×T, TE top-1.25×T.
START_MULT={"QB":1.0,"RB":2.5,"WR":2.8,"TE":1.25}
def startable_n(pos,teams):
    return max(1,round(teams*START_MULT.get(pos,2.0)))

def build_backtest(years,mult=None):
    """BACKTEST[key][round][pos] = {n, avg (PPR pts, matching the tab's label), hit (frac startable)}.

    `hit` is scoring-specific (a startable RB in a standard league is a different bar than in PPR).
    `avg` is reported in PPR points regardless of key — that's what the tab shows and what the
    original baked tables used, so the numbers stay comparable to the values they replace."""
    mult=mult or START_MULT
    acc={}  # key -> round -> pos -> [pts...] and [is_startable...]
    for (teams,ppr) in KEYS:
        key=f"{teams}_{ppr}"; acc[key]={}
    for year in years:
        act=actuals(year); pprmap=act["ppr"]
        for (teams,ppr) in KEYS:
            key=f"{teams}_{ppr}"
            ptsmap=act["ppr"] if ppr else act["std"]     # startable test uses the key's own scoring
            ranked={}
            for nm,p in act["pos"].items():
                ranked.setdefault(p,[]).append((ptsmap.get(nm,0.0),nm))
            startset={}
            for p,lst in ranked.items():
                lst.sort(reverse=True)
                cut=max(1,round(teams*mult.get(p,2.0)))
                startset[p]=set(nm for _,nm in lst[:cut])
            for pl in ffc(year,teams,ppr):
                pos=pl["pos"]
                if pos not in POS_KEEP: continue
                nm=pl["nm"]
                if nm not in ptsmap: continue            # unmatched → drop (avoids name-mismatch false zeros)
                r=str(pl["round"])
                node=acc[key].setdefault(r,{}).setdefault(pos,{"pts":[],"hit":[]})
                node["pts"].append(pprmap.get(nm,0.0))   # avg reported in PPR points
                node["hit"].append(1 if nm in startset.get(pos,()) else 0)
    out={}
    for key,rounds in acc.items():
        out[key]={}
        for r,poss in rounds.items():
            for pos,d in poss.items():
                n=len(d["pts"])
                if n<4: continue                          # too thin to report
                out[key].setdefault(r,{})[pos]={
                    "n":n,"avg":round(sum(d["pts"])/n,1),
                    "hit":round(sum(d["hit"])/n,2)}
        # prune empty rounds
        out[key]={r:v for r,v in out[key].items() if v}
    return out

# ---- SLOTVAL + OPENING: snake-draft the field by ADP, value each team's starters ----
STARTERS={"QB":1,"RB":2,"WR":2,"TE":1,"FLEX":1}   # a plain, standard starting lineup
FLEX_ELIG={"RB","WR","TE"}
def team_value(roster_pts):
    """roster_pts: dict pos -> sorted-desc list of season pts. Value = best legal starting lineup."""
    used={p:0 for p in roster_pts}; total=0.0
    for pos,cnt in STARTERS.items():
        if pos=="FLEX": continue
        lst=roster_pts.get(pos,[])
        for i in range(cnt):
            if i<len(lst): total+=lst[i]; used[pos]=i+1
    # flex: best remaining among RB/WR/TE
    best=0.0
    for pos in FLEX_ELIG:
        lst=roster_pts.get(pos,[]); i=used.get(pos,0)
        if i<len(lst): best=max(best,lst[i])
    total+=best
    return total

def build_slot(years):
    slot_acc={f"{t}_{p}":[[] for _ in range(t)] for (t,p) in KEYS}
    open_acc={f"{t}_{p}":{s:{q:[] for q in POS_KEEP} for s in range(t)} for (t,p) in KEYS}
    for year in years:
        act=actuals(year)
        for (teams,ppr) in KEYS:
            key=f"{teams}_{ppr}"
            ptsmap=act["ppr"] if ppr else act["std"]
            board=[pl for pl in ffc(year,teams,ppr) if pl["pos"] in POS_KEEP and pl["nm"] in ptsmap]
            board.sort(key=lambda x:x["adp"])
            rounds=15
            rosters=[{p:[] for p in POS_KEEP} for _ in range(teams)]
            firstpos=[None]*teams
            bi=0
            for rd in range(rounds):
                order=range(teams) if rd%2==0 else range(teams-1,-1,-1)
                for slot in order:
                    if bi>=len(board): break
                    pl=board[bi]; bi+=1
                    rosters[slot][pl["pos"]].append(ptsmap[pl["nm"]])
                    if firstpos[slot] is None: firstpos[slot]=pl["pos"]
            for slot in range(teams):
                rp={p:sorted(v,reverse=True) for p,v in rosters[slot].items()}
                val=team_value(rp)
                slot_acc[key][slot].append(val)
                fp=firstpos[slot]
                if fp in open_acc[key][slot]: open_acc[key][slot][fp].append(val)
    SLOTVAL={}; OPENING={}
    for key in slot_acc:
        SLOTVAL[key]=[round(sum(v)/len(v),1) if v else 0.0 for v in slot_acc[key]]
        OPENING[key]={}
        for slot,posd in open_acc[key].items():
            OPENING[key][str(slot)]={q:round(sum(vv)/len(vv),1) for q,vv in posd.items() if vv}
    return SLOTVAL,OPENING

def calibration_report(new_bt):
    """Compare regenerated 2014-2024 hit/avg against what's baked in base.json today."""
    base=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))
    old=base["BACKTEST"]
    dh=[]; da=[]
    for key in old:
        for r in old[key]:
            for pos in old[key][r]:
                o=old[key][r][pos]; n=new_bt.get(key,{}).get(r,{}).get(pos)
                if not n: continue
                dh.append(abs(o["hit"]-n["hit"])); da.append(abs(o["avg"]-n["avg"]))
    if dh:
        dh.sort(); da.sort()
        print(f"  hit  Δ: mean {sum(dh)/len(dh):.3f}  median {dh[len(dh)//2]:.3f}  max {max(dh):.3f}  (n={len(dh)})")
        print(f"  avg  Δ: mean {sum(da)/len(da):.1f}   median {da[len(da)//2]:.1f}   max {max(da):.1f}")

def _fit_multipliers(calib_years):
    """Grid-search the startable multipliers so regenerated 2014-2024 hit rates best match the
    values already baked into base.json (n-weighted). Keeps 2025 an honest extension, not a reset."""
    base=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))["BACKTEST"]
    grids={"QB":[1.0,1.1,1.15,1.2,1.3],"RB":[2.3,2.5,2.7,2.9,3.1],
           "WR":[2.8,3.0,3.2,3.4,3.6],"TE":[1.1,1.25,1.4,1.6,1.8]}
    # cache per-year actuals+ffc once by building a big accumulator per candidate is costly; instead
    # evaluate each position independently since hit for a pos depends only on its own multiplier.
    best={}
    for pos,cands in grids.items():
        scored=[]
        for m in cands:
            trial=dict(START_MULT); trial[pos]=m
            bt=build_backtest(calib_years,trial)
            num=den=0.0
            for key in base:
                for r in base[key]:
                    o=base[key][r].get(pos); n=bt.get(key,{}).get(r,{}).get(pos)
                    if not o or not n: continue
                    w=o["n"]; num+=w*abs(o["hit"]-n["hit"]); den+=w
            scored.append((num/den if den else 9,m))
        scored.sort(); best[pos]=scored[0][1]
        print(f"  fit {pos}: {scored[0][1]}  (wErr {scored[0][0]:.3f})")
    return best

def main():
    write="--write" in sys.argv
    calib_years=list(range(2014,2025))          # 2014-2024, to check we match the old baked tables
    if "--fit" in sys.argv:
        print("Fitting startable multipliers against base.json (2014-2024)...")
        fit=_fit_multipliers(calib_years)
        print("  BEST:",fit); START_MULT.update(fit)
    print("Building BACKTEST for 2014-2024 (calibration vs base.json)...")
    bt_old=build_backtest(calib_years)
    calibration_report(bt_old)
    print("\nBuilding full 2014-2025 tables...")
    BACKTEST=build_backtest(YEARS)
    SLOTVAL,OPENING=build_slot(YEARS)
    # sanity print
    for key in ("12_1","8_0"):
        r1=BACKTEST[key].get("1",{})
        print(f"  {key} round1:", {p:(r1[p]["n"],r1[p]["avg"],r1[p]["hit"]) for p in r1})
    print("  SLOTVAL 12_1:", SLOTVAL["12_1"])
    if write:
        base=json.load(open(os.path.join(HERE,"base.json"),encoding="utf8"))
        base["BACKTEST"]=BACKTEST; base["SLOTVAL"]=SLOTVAL; base["OPENING"]=OPENING
        json.dump(base,open(os.path.join(HERE,"base.json"),"w",encoding="utf8"),ensure_ascii=False,separators=(",",":"))
        print("\n✓ wrote base.json (BACKTEST/SLOTVAL/OPENING → 2014-2025)")
    else:
        print("\n(dry run — pass --write to update base.json)")

if __name__=="__main__":
    main()

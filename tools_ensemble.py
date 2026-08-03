"""
Turn the projection archive into an accuracy-weighted ensemble.

The app's consensus is a blend of ESPN and Sleeper. An equal blend is only optimal if both sources are
equally accurate; they usually aren't, and which is sharper varies by position. The principled fix is to
weight each source by how accurate it has actually been — but that needs a kept record of what each source
predicted BEFORE the games, which is exactly what refresh.py's proj_archive.csv now accumulates (one row
per player per snapshot, carrying the ESPN number, the Sleeper number, the blend, and ADP).

This script:
  * reads proj_archive.csv,
  * for any season that is COMPLETE, pulls nflverse season-total actuals as truth,
  * scores each source (ESPN, Sleeper, current blend) by mean absolute error per position,
  * fits inverse-error weights on earlier data and TESTS them out of sample against the equal blend,
  * writes ensemble.json ONLY with weights that beat the equal blend out of sample — same discipline as
    every other correction here.

By design it self-activates: with no completed season in the archive yet, it reports what it's waiting for
and writes nothing, so it can be run on a cron and will start improving the blend the moment it can.
"""
import urllib.request, csv, io, os, json, re, statistics as st, collections

HERE=os.path.dirname(os.path.abspath(__file__))
POS=("QB","RB","WR","TE")
ARCH=os.path.join(HERE,"proj_archive.csv")
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def gt(u,t=180):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(r,timeout=t).read().decode("utf8","replace")

if not os.path.exists(ARCH):
    print("no proj_archive.csv yet — nothing to fit. It accumulates as refresh.py runs.")
    raise SystemExit

# earliest snapshot per (season,name) — the preseason forecast is the fair test of a season-long projection
rows=[]
with open(ARCH,encoding="utf-8") as f:
    for r in csv.DictReader(f):
        rows.append(r)
seasons=sorted({r["season"] for r in rows})
print(f"archive: {len(rows):,} rows across seasons {seasons}")

# season-total actuals for any season we can get them for
def actuals(season):
    try:
        raw=gt(f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv")
    except Exception as ex:
        return None
    tot=collections.defaultdict(float); wk=collections.defaultdict(set)
    for r in csv.DictReader(io.StringIO(raw)):
        if (r.get("season_type") or "REG")!="REG": continue
        if r.get("position") not in POS: continue
        try: v=float(r.get("fantasy_points_ppr") or 0); w=int(r["week"])
        except Exception: continue
        k=(norm(r.get("player_display_name") or ""),r["position"])
        tot[k]+=v; wk[k].add(w)
    weeks=max((w for s in wk.values() for w in s), default=0)
    return tot, weeks

# earliest forecast per (season,name,pos)
first={}
for r in rows:
    key=(r["season"],norm(r["name"]),r["pos"])
    if key not in first or r["date"]<first[key]["date"]:
        first[key]=r

data=[]   # (season, pos, espn, sleeper, blend, actual)
complete=[]
for s in seasons:
    a=actuals(s)
    if not a: print(f"  {s}: no actuals available — skipping"); continue
    tot,weeks=a
    if weeks<17: print(f"  {s}: only {weeks} weeks played — not a complete season yet, skipping"); continue
    complete.append(s)
    n=0
    for (ss,nm,pos),r in first.items():
        if ss!=s: continue
        k=(nm,pos)
        if k not in tot: continue
        def fv(x):
            try: return float(x)
            except Exception: return None
        e,sl,bl=fv(r.get("espn")),fv(r.get("sleeper")),fv(r.get("proj"))
        if bl is None: continue
        data.append((s,pos,e,sl,bl,tot[k])); n+=1
    print(f"  {s}: {n} players matched to season totals")

if len(complete)<1:
    print("\nno COMPLETE season in the archive yet — the ensemble needs one played-out season to fit and test.")
    print("This is expected pre-season; refresh.py keeps archiving, and this self-activates once a season finishes.")
    raise SystemExit

def mae(rs,fn):
    xs=[abs(fn(r)-r[5]) for r in rs if fn(r) is not None]
    return sum(xs)/len(xs) if xs else None

# leave-one-season-out if we have >=2 complete seasons, else report in-sample honestly labelled
if len(complete)>=2:
    print(f"\nleave-one-season-out across complete seasons {complete}:")
    wins=[]
    for test in complete:
        tr=[d for d in data if d[0]!=test]; te=[d for d in data if d[0]==test]
        w={}
        for p in POS:
            sub=[d for d in tr if d[1]==p and d[2] is not None and d[3] is not None]
            if not sub: w[p]=(0.5,0.5); continue
            eE=st.mean([abs(d[2]-d[5]) for d in sub]); eS=st.mean([abs(d[3]-d[5]) for d in sub])
            iE,iS=1/max(eE,1e-6),1/max(eS,1e-6)
            w[p]=(iE/(iE+iS),iS/(iE+iS))
        wblend=lambda d:(w[d[1]][0]*d[2]+w[d[1]][1]*d[3]) if (d[2] is not None and d[3] is not None) else d[4]
        eq=lambda d:((d[2]+d[3])/2) if (d[2] is not None and d[3] is not None) else d[4]
        mw,me=mae(te,wblend),mae(te,eq)
        better=mw is not None and me is not None and mw<me
        wins.append(better)
        print(f"  test {test}: weighted MAE {mw:.3f} vs equal {me:.3f}  -> {'better' if better else 'not better'}")
    if all(wins):
        # final weights fit on ALL complete seasons
        w={}
        for p in POS:
            sub=[d for d in data if d[1]==p and d[2] is not None and d[3] is not None]
            eE=st.mean([abs(d[2]-d[5]) for d in sub]); eS=st.mean([abs(d[3]-d[5]) for d in sub])
            iE,iS=1/max(eE,1e-6),1/max(eS,1e-6); w[p]=[round(iE/(iE+iS),3),round(iS/(iE+iS),3)]
        json.dump({"weights":w,"seasons":complete,"beatsEqualEveryFold":True},
                  open(os.path.join(HERE,"ensemble.json"),"w"),indent=1)
        print("\nwrote ensemble.json:",json.dumps(w))
    else:
        print("\naccuracy weighting did NOT beat the equal blend out of sample in every fold — shipping nothing.")
else:
    print(f"\nonly one complete season ({complete}) — can measure per-source accuracy but not test out of sample yet.")
    for p in POS:
        sub=[d for d in data if d[1]==p and d[2] is not None and d[3] is not None]
        if not sub: continue
        eE=st.mean([abs(d[2]-d[5]) for d in sub]); eS=st.mean([abs(d[3]-d[5]) for d in sub])
        print(f"  {p}: ESPN season MAE {eE:.1f} · Sleeper {eS:.1f}  (sharper: {'ESPN' if eE<eS else 'Sleeper'})")
    print("waiting for a second complete season before shipping weights.")

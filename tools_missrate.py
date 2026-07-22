"""
How often does a healthy fantasy starter miss a game?

The in-season tab can already say what a backup is worth IF the man ahead of him is out. It
deliberately stops short of multiplying by the odds of that happening, because nothing in the app
measured them — the injury data we carry only describes players who ALREADY have a designation.
This measures the missing term.

Source: nflverse weekly rosters, which carry a per-player per-week roster status (ACT / INA /
RES / etc). That is better than inferring from snap counts, where a healthy backup who simply
didn't play looks identical to an injured one.

What is measured, precisely: a HAZARD RATE. Given a player was active in a given week, how often
is he unavailable the following week. That is the form the app needs — from it, the chance of
missing at least one of the next N games is 1-(1-h)^N — and it avoids the survivorship trap of
counting season totals, where players who get hurt in week 2 and never return drag the average in
a way that doesn't describe the risk facing a player who is fit today.

Only established contributors count: a player must have been active at least four times that
season before he's eligible, so camp bodies and practice-squad churn don't masquerade as injuries.
"""
import urllib.request, csv, io, json, os, collections, statistics

HERE=os.path.dirname(os.path.abspath(__file__))
SEASONS=range(2016,2025)          # 2025+ roster files may be incomplete mid-season
POS=("QB","RB","WR","TE")
URL="https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{}.csv"

def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(r,timeout=180).read().decode("utf8","replace")

# player-season -> {week: status}
hazard=collections.defaultdict(lambda:[0,0])      # pos -> [misses, opportunities]
byAge =collections.defaultdict(lambda:[0,0])      # (pos,ageBucket) -> [...]
statuses=collections.Counter()

for yr in SEASONS:
    try: raw=get(URL.format(yr))
    except Exception as ex:
        print(f"  {yr}: fetch failed {ex}"); continue
    rows=list(csv.DictReader(io.StringIO(raw)))
    seen=collections.defaultdict(dict)
    ages={}
    for r in rows:
        if r.get("position") not in POS: continue
        if (r.get("season_type") or "REG")!="REG": continue
        try: w=int(r["week"])
        except Exception: continue
        if not 1<=w<=18: continue
        pid=r.get("gsis_id") or r.get("full_name")
        if not pid: continue
        st=(r.get("status") or "").strip().upper()
        statuses[st]+=1
        seen[(pid,r["position"])][w]=st
        try:
            by=int((r.get("birth_date") or "")[:4]); ages[(pid,r["position"])]=yr-by
        except Exception: pass
    for key,wk in seen.items():
        act=[w for w,s in wk.items() if s=="ACT"]
        if len(act)<4: continue                     # established contributors only
        pos=key[1]; age=ages.get(key)
        lo,hi=min(wk),max(wk)
        for w in range(lo,hi):
            if wk.get(w)!="ACT": continue           # only ask the question of an active player
            nxt=wk.get(w+1)
            if nxt is None: continue
            miss = 1 if nxt!="ACT" else 0
            hazard[pos][0]+=miss; hazard[pos][1]+=1
            if age:
                b = "u25" if age<25 else ("25-28" if age<29 else "29+")
                byAge[(pos,b)][0]+=miss; byAge[(pos,b)][1]+=1
    print(f"  {yr}: {len(rows)} rows, {len(seen)} player-seasons")

print("\nstatus values seen:", dict(statuses.most_common(8)))
out={"weeklyMiss":{}, "byAge":{}, "n":{}, "years":f"{min(SEASONS)}-{max(SEASONS)}"}
print("\nPER-GAME chance an ACTIVE starter is unavailable the following week:")
for pos in POS:
    m,n=hazard[pos]
    if not n: continue
    h=m/n
    out["weeklyMiss"][pos]=round(h,4); out["n"][pos]=n
    print(f"  {pos}: {h*100:.2f}%   (n={n:,})   -> misses >=1 of next 6: {(1-(1-h)**6)*100:.0f}%")
print("\nby age:")
for (pos,b),(m,n) in sorted(byAge.items()):
    if n<200: continue
    out["byAge"][f"{pos}|{b}"]=round(m/n,4)
    print(f"  {pos:3} {b:5} {m/n*100:5.2f}%  (n={n:,})")

json.dump(out,open(os.path.join(HERE,"missrate.json"),"w"),indent=1)
print("\nwrote missrate.json")

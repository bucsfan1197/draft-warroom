"""
Turn the raw retention measurements into a smooth table the app can carry.

Raw per-(position, age, k) means are noisy at the tails (a dozen 36-year-old QBs), so:
  1. weighted rolling mean across neighbouring ages (sample-size weighted)
  2. clamp to a sane range
  3. enforce non-increasing in k AFTER the peak — real decline is monotone; wobble is noise,
     but genuinely young players do improve for a year or two, so growth before the peak stays.
  4. extend flat to the age range the app needs, and force a hard zero at a career-end age.
"""
import pandas as pd, numpy as np, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = r"C:/Users/ethan/AppData/Local/Temp/claude/C--Users-ethan--claude/c9ad71b1-6df2-4070-8953-ff0adbded93d/scratchpad/data"
YEARS = list(range(2014, 2026))
POS = ["QB", "RB", "WR", "TE"]
K = 7                                  # horizon in years beyond the current one
AGES = {"QB": (22, 40), "RB": (21, 33), "WR": (21, 35), "TE": (22, 35)}
STARTABLE = {"QB": 24, "RB": 36, "WR": 48, "TE": 18}

rows = []
for y in YEARS:
    f = os.path.join(DATA, f"ps_{y}.csv")
    if not os.path.exists(f): continue
    d = pd.read_csv(f, low_memory=False)
    if "season_type" in d: d = d[d["season_type"] == "REG"]
    g = (d.groupby(["player_id", "position"], as_index=False)
           .agg(pts=("fantasy_points_ppr", "sum")))
    g["season"] = y
    rows.append(g)
season = pd.concat(rows, ignore_index=True)
season = season[season["position"].isin(POS)]

births = {}
for y in YEARS:
    f = os.path.join(HERE, f"roster_{y}.csv")
    if not os.path.exists(f): continue
    r = pd.read_csv(f, low_memory=False)
    for pid, bd in zip(r.get("gsis_id", []), r.get("birth_date", [])):
        if isinstance(pid, str) and isinstance(bd, str) and pid not in births:
            births[pid] = bd
season["birth"] = season["player_id"].map(births)
season = season.dropna(subset=["birth"])
season["age"] = ((pd.to_datetime(season["season"].astype(str) + "-09-01")
                  - pd.to_datetime(season["birth"], errors="coerce")).dt.days / 365.25)
season = season.dropna(subset=["age"])
season["age"] = season["age"].round().astype(int)
season["rank"] = season.groupby(["season", "position"])["pts"].rank(ascending=False, method="first")
startable = season[season["rank"] <= season["position"].map(STARTABLE)]
lookup = {(r.player_id, r.season): r.pts for r in season.itertuples()}

raw = {}
for _, r in startable.iterrows():
    if r.pts <= 0: continue
    for k in range(0, K + 1):
        fs = r.season + k
        if fs > max(YEARS): continue          # censored, not a zero
        raw.setdefault(r.position, {}).setdefault(int(r.age), {}).setdefault(k, []) \
           .append(lookup.get((r.player_id, fs), 0.0) / r.pts)

table = {}
for pos in POS:
    lo, hi = AGES[pos]
    ages = list(range(lo, hi + 1))
    grid = np.zeros((len(ages), K + 1))
    for i, a in enumerate(ages):
        for k in range(K + 1):
            num = den = 0.0
            for d in range(-2, 3):                       # sample-size weighted neighbourhood
                v = raw.get(pos, {}).get(a + d, {}).get(k)
                if not v: continue
                w = len(v) / (1 + abs(d))
                num += np.mean(v) * w; den += w
            grid[i, k] = num / den if den else np.nan
    # fill gaps by carrying the nearest estimate
    for k in range(K + 1):
        col = pd.Series(grid[:, k]).ffill().bfill()
        grid[:, k] = col.values
    grid[:, 0] = 1.0
    grid = np.clip(grid, 0, 1.4)
    # monotone after the peak
    for i in range(len(ages)):
        peak = int(np.argmax(grid[i]))
        for k in range(peak + 1, K + 1):
            grid[i, k] = min(grid[i, k], grid[i, k - 1])
    # a player is done at some point: taper the oldest ages to zero
    for i, a in enumerate(ages):
        if a >= hi - 1:
            for k in range(K + 1):
                grid[i, k] *= max(0.0, 1 - 0.25 * (k - 1)) if k >= 1 else 1
    table[pos] = {a: [round(float(x), 3) for x in grid[i]] for i, a in enumerate(ages)}

# ---- report ----
print("SMOOTHED RETENTION (k = years ahead)")
for pos in POS:
    print(f"\n{pos}  " + "".join(f"  k{k}  " for k in range(K + 1)))
    for a in sorted(table[pos]):
        if a % 2: continue
        print(f"  {a:>3} " + "".join(f"{v:6.2f}" for v in table[pos][a]))

WINDOW = {"contend":[1,.55,.30,.15,.08,.04,.02,.01],
          "balanced":[1,.80,.62,.45,.32,.22,.15,.10],
          "rebuild":[.65,.85,1,1,.92,.80,.65,.50]}
print("\n\nWhat this does to dynasty value (same base points, different age):")
for win, w in WINDOW.items():
    print(f"\n  {win}")
    for pos in ["QB", "WR", "RB"]:
        lo, hi = AGES[pos]
        vals = {}
        for a in [lo + 1, 24, 27, 30, 33]:
            if a > hi: continue
            r = table[pos][min(max(a, lo), hi)]
            vals[a] = sum(w[t] * r[t] for t in range(len(w)))
        young = vals[lo + 1]
        s = "  ".join(f"{a}:{v:5.2f} ({v/young:.2f}x)" for a, v in vals.items())
        print(f"    {pos}  {s}")

json.dump(table, open(os.path.join(HERE, "retention_table.json"), "w"), indent=0)
js = "const RETENTION=" + json.dumps({p: {str(a): v for a, v in table[p].items()} for p in table},
                                     separators=(",", ":")) + ";"
open(os.path.join(HERE, "retention.js"), "w").write(js)
print(f"\nwrote retention_table.json and retention.js ({len(js)} bytes)")

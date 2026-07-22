"""
Derive dynasty age curves EMPIRICALLY instead of asserting them.

The shipped model values a dynasty asset as: sum over a 4-year window of
    base_points * ageMult(age+t) / ageMult(age)
where ageMult is a hand-authored production curve. That curve describes how much a player
scores *if he is still playing*. It therefore misses the thing dynasty actually prices:
attrition. A 31-year-old QB and a 23-year-old QB have nearly identical ageMult over four
years, but wildly different odds of still being a starter in year 4.

So measure the real quantity: for a player who is a startable fantasy asset at age A,
what fraction of his current production does he actually deliver in year A+k, counting
players who are out of the league as ZERO?

That single number (retention[pos][A][k]) is what dynasty value needs, and it folds
production decline and washout risk into one empirically grounded curve.
"""
import pandas as pd, numpy as np, json, os, sys

DATA = r"C:/Users/ethan/AppData/Local/Temp/claude/C--Users-ethan--claude/c9ad71b1-6df2-4070-8953-ff0adbded93d/scratchpad/data"
HERE = os.path.dirname(os.path.abspath(__file__))
YEARS = list(range(2014, 2026))
POS = ["QB", "RB", "WR", "TE"]

# ---- 1. season totals per player ----
rows = []
for y in YEARS:
    f = os.path.join(DATA, f"ps_{y}.csv")
    if not os.path.exists(f):
        continue
    d = pd.read_csv(f, low_memory=False)
    d = d[d["season_type"] == "REG"] if "season_type" in d else d
    g = (d.groupby(["player_id", "player_display_name", "position"], as_index=False)
           .agg(pts=("fantasy_points_ppr", "sum"), games=("week", "nunique")))
    g["season"] = y
    rows.append(g)
season = pd.concat(rows, ignore_index=True)
season = season[season["position"].isin(POS)]

# ---- 2. ages from rosters (birth_date joined on gsis_id) ----
births = {}
for y in YEARS:
    f = os.path.join(HERE, f"roster_{y}.csv")
    if not os.path.exists(f):
        os.system(f'curl -sL -o "{f}" "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{y}.csv"')
    if not os.path.exists(f):
        continue
    try:
        r = pd.read_csv(f, low_memory=False)
    except Exception:
        continue
    for pid, bd in zip(r.get("gsis_id", []), r.get("birth_date", [])):
        if isinstance(pid, str) and isinstance(bd, str) and pid not in births:
            births[pid] = bd

season["birth"] = season["player_id"].map(births)
season = season.dropna(subset=["birth"])
season["age"] = (pd.to_datetime(season["season"].astype(str) + "-09-01")
                 - pd.to_datetime(season["birth"], errors="coerce")).dt.days / 365.25
season = season.dropna(subset=["age"])
season["age"] = season["age"].round().astype(int)

print(f"player-seasons with age: {len(season)}  ({season.season.min()}-{season.season.max()})")

# ---- 3. "startable at age A": top-N at the position that season ----
# A dynasty asset is a player you'd actually roster, not every practice-squad body.
STARTABLE = {"QB": 24, "RB": 36, "WR": 48, "TE": 18}
season["rank"] = season.groupby(["season", "position"])["pts"].rank(ascending=False, method="first")
startable = season[season["rank"] <= season["position"].map(STARTABLE)]

pts_lookup = {(r.player_id, r.season): r.pts for r in season.itertuples()}

# ---- 4. retention: points in year A+k as a share of year-A points, zeros included ----
K = 6
out = {}
for pos in POS:
    sub = startable[startable["position"] == pos]
    band = {}
    for _, r in sub.iterrows():
        if r.pts <= 0:
            continue
        a = int(r.age)
        for k in range(0, K + 1):
            fut_season = r.season + k
            if fut_season > max(YEARS):          # censored — no data yet, must not count as a zero
                continue
            fut = pts_lookup.get((r.player_id, fut_season), 0.0)
            band.setdefault(a, {}).setdefault(k, []).append(fut / r.pts)
    out[pos] = {a: {k: (float(np.mean(v)), len(v)) for k, v in kk.items()} for a, kk in band.items()}

# ---- 5. report ----
print("\nRETENTION — share of current production delivered k years later (0 if out of league)")
for pos in POS:
    print(f"\n{pos}")
    print("  age   n     k=1    k=2    k=3    k=4    k=5")
    for a in sorted(out[pos]):
        d = out[pos][a]
        n = d.get(1, (0, 0))[1]
        if n < 12:
            continue
        cells = "".join(f"  {d[k][0]:.2f} " if k in d else "   --  " for k in range(1, 6))
        print(f"  {a:>3} {n:>4}  {cells}")

# ---- 6. what the SHIPPED curve claims, for comparison ----
AGE_CURVE = {
 "RB": {21:.85,22:.90,23:.96,24:1,25:1,26:.93,27:.84,28:.72,29:.60,30:.48,31:.38,32:.30,33:.24,34:.20},
 "WR": {21:.78,22:.84,23:.90,24:.95,25:.99,26:1,27:1,28:.96,29:.90,30:.82,31:.73,32:.63,33:.53,34:.44,35:.37,36:.30},
 "TE": {22:.62,23:.72,24:.82,25:.90,26:.96,27:1,28:1,29:.97,30:.92,31:.85,32:.77,33:.68,34:.59,35:.50,36:.42,37:.35},
 "QB": {22:.82,23:.87,24:.91,25:.95,26:.98,27:1,28:1,29:1,30:1,31:1,32:.99,33:.97,34:.94,35:.90,36:.85,37:.79,38:.72,39:.65,40:.58},
}
def agemult(pos, age):
    c = AGE_CURVE[pos]; ks = list(c)
    a = max(min(ks), min(max(ks), int(round(age))))
    return c.get(a, 1)

print("\n\nMODEL vs REALITY — 4-year total (the window dynValue actually sums)")
print("pos  age   model   actual   ratio")
for pos in POS:
    for a in sorted(out[pos]):
        d = out[pos][a]
        if d.get(1, (0, 0))[1] < 20 or not all(k in d for k in (1, 2, 3)):
            continue
        model = sum(agemult(pos, a + t) / agemult(pos, a) for t in range(0, 4))
        actual = 1.0 + sum(d[k][0] for k in (1, 2, 3))
        print(f"{pos:>3} {a:>4}   {model:5.2f}   {actual:5.2f}   {actual/model:5.2f}")

json.dump({p: {str(a): {str(k): v[0] for k, v in kk.items()} for a, kk in out[p].items()} for p in out},
          open(os.path.join(HERE, "retention.json"), "w"), indent=1)
print("\nwrote retention.json")

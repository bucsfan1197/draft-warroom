"""
What does an injury designation ACTUALLY cost?

The app discounts a designated player by hand-picked multipliers — Questionable .88, Doubtful .4,
Out 0 — and long-term statuses by another set for rest-of-season. Those were guesses. nflverse
publishes the weekly injury report going back years, and the weekly scoring alongside it, so the
real number is measurable:

    multiplier(status) = mean points in weeks with that status
                       / mean points that same player scores in his healthy weeks

Comparing each player against HIMSELF matters: designated players are not a random sample, they
skew toward the kind of player who plays through knocks, so a league-wide average would be
biased. Every comparison here is within-player.
"""
import pandas as pd, numpy as np, os, json, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = r"C:/Users/ethan/AppData/Local/Temp/claude/C--Users-ethan--claude/c9ad71b1-6df2-4070-8953-ff0adbded93d/scratchpad/data"
YEARS = list(range(2018, 2025))
POS = ["QB", "RB", "WR", "TE"]

def fetch(y):
    f = os.path.join(HERE, f"inj{y}.csv")
    if not os.path.exists(f):
        url = f"https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{y}.csv"
        try: urllib.request.urlretrieve(url, f)
        except Exception as e: print("  skip", y, e); return None
    try: return pd.read_csv(f, low_memory=False)
    except Exception: return None

inj = pd.concat([d for d in (fetch(y) for y in YEARS) if d is not None], ignore_index=True)
inj = inj[inj["game_type"].isin(["REG"])] if "game_type" in inj else inj
inj = inj.dropna(subset=["gsis_id", "week", "season"])
inj["week"] = inj["week"].astype(int); inj["season"] = inj["season"].astype(int)

wk = []
for y in YEARS:
    f = os.path.join(DATA, f"ps_{y}.csv")
    if not os.path.exists(f): continue
    d = pd.read_csv(f, low_memory=False)
    if "season_type" in d: d = d[d["season_type"] == "REG"]
    d = d[d["position"].isin(POS)]
    wk.append(d[["player_id", "position", "season", "week", "fantasy_points_ppr"]])
wk = pd.concat(wk, ignore_index=True)
wk = wk[wk["week"] <= 18]

# a player's own healthy baseline: weeks where he carried no designation
inj["status"] = inj["report_status"].fillna("None").str.strip()
key = ["player_id", "season", "week"]
m = wk.merge(inj[["gsis_id", "season", "week", "status"]],
             left_on=["player_id", "season", "week"], right_on=["gsis_id", "season", "week"], how="left")
m["status"] = m["status"].fillna("Healthy")

# baseline per player-season over healthy weeks only, and only for players who played enough
base = (m[m["status"] == "Healthy"].groupby(["player_id", "season"])["fantasy_points_ppr"]
        .agg(["mean", "count"]).reset_index().rename(columns={"mean": "base", "count": "hw"}))
base = base[(base["hw"] >= 6) & (base["base"] > 3)]
m = m.merge(base, on=["player_id", "season"], how="inner")
m["ratio"] = m["fantasy_points_ppr"] / m["base"]

print("WEEKLY COST OF A DESIGNATION (own healthy weeks = 1.00)")
print(f"{'status':<14}{'player-weeks':>13}{'played%':>9}{'mean':>8}{'median':>8}")
out = {}
for st in ["Healthy", "Questionable", "Doubtful", "Out"]:
    s = m[m["status"] == st]
    if len(s) < 30: continue
    played = (s["fantasy_points_ppr"] > 0).mean()
    out[st] = round(float(s["ratio"].mean()), 3)
    print(f"{st:<14}{len(s):>13}{played*100:>8.0f}%{s['ratio'].mean():>8.2f}{s['ratio'].median():>8.2f}")

print("\nby position (mean ratio)")
print(f"{'status':<14}" + "".join(f"{p:>8}" for p in POS))
bypos = {}
for st in ["Questionable", "Doubtful", "Out"]:
    row = {}
    line = f"{st:<14}"
    for p in POS:
        s = m[(m["status"] == st) & (m["position"] == p)]
        v = float(s["ratio"].mean()) if len(s) >= 25 else None
        row[p] = round(v, 3) if v is not None else None
        line += f"{v:>8.2f}" if v is not None else f"{'-':>8}"
    bypos[st] = row
    print(line)

# Rest-of-season: does a designation this week predict the WEEKS AFTER it?
print("\nREST-OF-SEASON effect: ratio in the 4 weeks AFTER a designation")
ros = {}
for st in ["Questionable", "Doubtful", "Out"]:
    vals = []
    sub = m[m["status"] == st][["player_id", "season", "week", "base"]]
    for r in sub.itertuples():
        later = m[(m["player_id"] == r.player_id) & (m["season"] == r.season)
                  & (m["week"] > r.week) & (m["week"] <= r.week + 4)]
        if len(later): vals.append(later["fantasy_points_ppr"].mean() / r.base)
    if vals:
        ros[st] = round(float(np.mean(vals)), 3)
        print(f"  after {st:<14} n={len(vals):>5}  ratio {np.mean(vals):.2f}")

json.dump({"weekly": out, "byPos": bypos, "ros": ros},
          open(os.path.join(HERE, "injury.json"), "w"), indent=1)
print("\nwrote injury.json")

"""
How much does fantasy scoring ACTUALLY vary, week to week and season to season?

The title simulator currently redraws a player's season-level outcome factor every single week.
That conflates two different things:

  * season-long TRUE-TALENT uncertainty — the projection itself might be wrong, and that error
    persists all year (measured already: the dist floor/ceil factors)
  * week-to-week NOISE — a player who ends the year exactly on projection still swings wildly
    from week to week

Redrawing the season factor weekly gets both wrong at once: season totals average out over 17
weeks and become near-deterministic, while weekly swings are far too small. Both errors push the
same way — the simulator becomes overconfident, and favourites win too often.

This measures the real quantities from 11 seasons of weekly results so the fix is calibrated
rather than guessed.
"""
import pandas as pd, numpy as np, os, json

DATA = r"C:/Users/ethan/AppData/Local/Temp/claude/C--Users-ethan--claude/c9ad71b1-6df2-4070-8953-ff0adbded93d/scratchpad/data"
YEARS = range(2014, 2026)
POS = ["QB", "RB", "WR", "TE"]

rows = []
for y in YEARS:
    f = os.path.join(DATA, f"ps_{y}.csv")
    if not os.path.exists(f): continue
    d = pd.read_csv(f, low_memory=False)
    if "season_type" in d: d = d[d["season_type"] == "REG"]
    d = d[d["position"].isin(POS)]
    rows.append(d[["player_id", "player_display_name", "position", "season", "week", "fantasy_points_ppr"]])
wk = pd.concat(rows, ignore_index=True)
wk = wk[wk["week"] <= 17]

# a player-season only tells us about weekly noise if he actually played most of it
g = wk.groupby(["player_id", "season", "position"])["fantasy_points_ppr"]
agg = g.agg(["count", "mean", "std", "sum"]).reset_index()
agg = agg[(agg["count"] >= 12) & (agg["mean"] > 3)]

print("WEEK-TO-WEEK noise, within a player-season (cv = sd / mean)")
print("  pos     n   median cv   mean cv")
weekly_cv = {}
for p in POS:
    s = agg[agg["position"] == p]
    cv = (s["std"] / s["mean"]).replace([np.inf, -np.inf], np.nan).dropna()
    weekly_cv[p] = float(cv.median())
    print(f"  {p:>3} {len(s):5}     {cv.median():.3f}     {cv.mean():.3f}")

# how much of a TEAM's week varies, once you add up a full starting lineup
# approximate a lineup as 1QB + 2RB + 3WR + 1TE of that season's startable players
print("\nTEAM weekly variation implied by those player numbers")
for label, lineup in [("1QB 2RB 3WR 1TE", {"QB": 1, "RB": 2, "WR": 3, "TE": 1})]:
    tot_mean, tot_var = 0.0, 0.0
    for p, n in lineup.items():
        s = agg[agg["position"] == p].sort_values("mean", ascending=False)
        top = s.head(400)                       # startable tier
        m = float(top["mean"].median())
        cv = float((top["std"] / top["mean"]).median())
        tot_mean += n * m
        tot_var += n * (m * cv) ** 2            # independent players
    print(f"  {label}: team mean {tot_mean:.1f}/wk, sd {np.sqrt(tot_var):.1f}, cv {np.sqrt(tot_var)/tot_mean:.3f}")

# ---- the real thing: actual team-week totals from optimal-ish lineups ----
# Build each season's top-N by position, randomly assemble 12 teams, and measure the spread of
# real weekly totals. This captures correlation the independent maths above misses.
print("\nTEAM weekly cv measured on REAL weekly scores (random legal lineups, 12 teams/season)")
rng = np.random.default_rng(7)
cvs = []
for season in sorted(wk["season"].unique()):
    ss = wk[wk["season"] == season]
    tot = ss.groupby(["player_id", "position"])["fantasy_points_ppr"].sum().reset_index()
    pick = {}
    for p, n in [("QB", 12), ("RB", 36), ("WR", 48), ("TE", 12)]:
        pick[p] = tot[tot["position"] == p].nlargest(n, "fantasy_points_ppr")["player_id"].tolist()
    piv = ss.pivot_table(index="week", columns="player_id", values="fantasy_points_ppr", aggfunc="sum").fillna(0)
    for t in range(12):
        ids = []
        for p, n in [("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1)]:
            avail = [i for i in pick[p] if i in piv.columns]
            if len(avail) < n: continue
            ids += list(rng.choice(avail, n, replace=False))
        if not ids: continue
        team_week = piv[ids].sum(axis=1)
        team_week = team_week[team_week > 0]
        if len(team_week) >= 12:
            cvs.append(team_week.std() / team_week.mean())
cvs = np.array(cvs)
print(f"  n={len(cvs)}  median cv {np.median(cvs):.3f}  mean {cvs.mean():.3f}  p25 {np.percentile(cvs,25):.3f}  p75 {np.percentile(cvs,75):.3f}")

out = {"weekly_cv_by_pos": weekly_cv, "team_weekly_cv": float(np.median(cvs))}
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "variance.json"), "w"), indent=1)
print("\n->", out)

"""
market_values.py — blended free-market trade values for the Draft War Room.

Pulled fresh by refresh.py each cycle. Three sources, three methodologies:
  KeepTradeCut    crowd-sourced keep/trade/cut votes (dynasty; 1QB, superflex, TE-premium)
  FantasyCalc     derived from ACTUAL completed trades, parameterised by league settings
  DynastyProcess  expert-consensus (ECR) derived, plus per-slot rookie pick values

Blending happens in log space so the sources' different dispersions can't let the
widest-spread one dominate; picks are rank-anchored onto the consensus scale. See the
comments on logstats() and the pick block for why.
"""
"""
Market trade values from every free source that publishes them.

Three independent sources, three different methodologies — which is exactly why blending them
beats any one of them:
  * KeepTradeCut     - crowd-sourced "keep/trade/cut" votes from real players. Dynasty only.
                       Has 1QB and superflex, each with TE-premium variants.
  * FantasyCalc      - derived from ACTUAL completed trades in Sleeper/MFL leagues, and
                       parameterised by league settings (dynasty, #QB, PPR, #teams).
  * DynastyProcess   - expert-consensus (ECR) derived, 1QB/2QB, plus per-slot rookie PICK values.

They live on different scales (KTC tops out ~9999, FantasyCalc ~10500, DP ~10200), so each is
normalised against its own #1 asset before blending.
"""
import json, urllib.request, re, csv, io, math

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def get(url, timeout=45):
    r = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(r, timeout=timeout).read().decode("utf-8", "replace")

def norm(n):
    n = str(n).lower()
    n = re.sub(r"[.'`]", "", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()

"""
Build the MARKET blob: blended trade values from every free source that publishes them.

Normalisation matters more than it looks. Dividing each source by its own #1 asset conflates
"these sources disagree" with "these sources have differently-shaped curves" — KTC's top is
flat (a dozen players near the max), DynastyProcess decays fast. So each source is instead
scaled so that its MEAN value over the players common to all sources equals 1. What's left
after that is genuine disagreement, which is worth showing the user rather than averaging away.

Picks come from the market too (FantasyCalc by exact slot, KTC by early/mid/late tier), on the
same scale as that source's players, so a pick and a player are directly comparable.
"""
import json, re, csv, io, statistics as st


ROUND_WORD = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
def tier_of_slot(slot):            # 12-team convention
    return "early" if slot <= 4 else ("mid" if slot <= 8 else "late")

def pick_key(year, rnd, tier):     # canonical
    return f"{year}|{rnd}|{tier}"

# ---------------------------------------------------------------- sources
def fantasycalc():
    series = [("dyn_sf",  "true", 2, 1), ("dyn_1qb", "true", 1, 1),
              ("red_sf",  "false", 2, 1), ("red_1qb", "false", 1, 1),
              ("red_1qb_std", "false", 1, 0), ("dyn_1qb_std", "true", 1, 0)]
    players, picks = {}, {}
    for key, dyn, qbs, ppr in series:
        try:
            rows = json.loads(get(f"https://api.fantasycalc.com/values/current?isDynasty={dyn}"
                                  f"&numQbs={qbs}&numTeams=12&ppr={ppr}"))
        except Exception as e:
            print("  FC", key, "failed:", e); continue
        for r in rows:
            pl = r.get("player") or {}
            nm, v = pl.get("name") or "", r.get("value") or 0
            if not v: continue
            m = re.match(r"(\d{4}) Pick (\d+)\.(\d+)$", nm)
            if m:
                y, rd, slot = int(m.group(1)), int(m.group(2)), int(m.group(3))
                picks.setdefault(pick_key(y, rd, tier_of_slot(slot)), {}).setdefault(key, []).append(v)
            elif pl.get("position") == "PICK":
                m2 = re.match(r"(\d{4}) (1st|2nd|3rd|4th)$", nm)
                if m2:
                    y, rd = int(m2.group(1)), ROUND_WORD[m2.group(2)]
                    picks.setdefault(pick_key(y, rd, "mid"), {}).setdefault(key, []).append(v)
            else:
                rec = players.setdefault(norm(nm), {"sid": pl.get("sleeperId"), "pos": pl.get("position"), "v": {}})
                rec["v"][key] = v
                if pl.get("sleeperId"): rec["sid"] = pl["sleeperId"]
        print(f"  FC {key}: {len(rows)}")
    picks = {k: {s: sum(v)/len(v) for s, v in d.items()} for k, d in picks.items()}
    return players, picks

def ktc():
    players, picks = {}, {}
    try:
        html = get("https://keeptradecut.com/dynasty-rankings")
    except Exception as e:
        print("  KTC failed:", e); return players, picks
    m = re.search(r"var playersArray\s*=\s*(\[.*?\]);", html, re.S)
    if not m:
        print("  KTC: markup changed, playersArray not found"); return players, picks
    arr = json.loads(m.group(1))
    MAP = [("dyn_1qb", "oneQBValues", "tep"), ("dyn_sf", "superflexValues", "tep"),
           ("dyn_1qb_tep", "oneQBValues", "tepp"), ("dyn_sf_tep", "superflexValues", "tepp")]
    for p in arr:
        nm = p.get("playerName") or ""
        vals = {}
        for key, side, tep in MAP:
            v = ((p.get(side) or {}).get(tep) or {}).get("value")
            if isinstance(v, (int, float)) and v > 0: vals[key] = v
        if not vals: continue
        pm = re.match(r"(\d{4}) (Early|Mid|Late) (1st|2nd|3rd|4th)", nm)
        if pm:
            y, tier, rd = int(pm.group(1)), pm.group(2).lower(), ROUND_WORD[pm.group(3)]
            picks[pick_key(y, rd, tier)] = vals
        else:
            # KTC publishes how many keep/trade/cut votes stand behind each player. That's the
            # sample size the whole crowd-sourced value rests on, and it spans ~270x across the
            # board — the single most useful confidence signal any of these sources exposes.
            n = 0
            for side in ("oneQBValues", "superflexValues"):
                b = p.get(side) or {}
                n = max(n, (b.get("kept") or 0) + (b.get("traded") or 0) + (b.get("cut") or 0))
            players[norm(nm)] = {"pos": p.get("position"), "v": vals, "age": p.get("age"), "n": n}
    print(f"  KTC: {len(arr)} rows -> {len(players)} players, {len(picks)} picks")
    return players, picks

def dynastyprocess():
    players = {}
    try:
        rows = list(csv.DictReader(io.StringIO(get(
            "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-players.csv"))))
        for r in rows:
            v = {}
            for key, col in (("dyn_1qb", "value_1qb"), ("dyn_sf", "value_2qb")):
                try:
                    f = float(r[col] or 0)
                    if f > 0: v[key] = f
                except Exception: pass
            if v: players[norm(r["player"])] = {"pos": r["pos"], "v": v, "age": r.get("age")}
        print(f"  DP: {len(players)} players")
    except Exception as e:
        print("  DP failed:", e)
    return players

# ---------------------------------------------------------------- blend
def logstats(sources, series):
    """
    Put every source on one scale, in LOG space.

    A trade calculator lives on ratios: two players must be addable and comparable against one.
    But the sources disagree about dispersion as much as about players — KTC's #1 sits ~3.2x the
    average player, DynastyProcess's ~8.8x. Rescaling by a constant can't reconcile that, and
    averaging raw values just lets the widest-spread source dominate every verdict.

    So each source's log-values are standardised (mean 0, sd 1) over the players all sources
    cover, averaged there, and mapped back out using the AVERAGE dispersion of the sources. That
    keeps ratio meaning, treats the three symmetrically, and leaves genuine disagreement — which
    becomes a confidence signal instead of being averaged into invisibility.
    """
    import math
    common = None
    for s in sources:
        have = {k for k, r in s.items() if series in r["v"] and r["v"][series] > 0}
        common = have if common is None else (common & have)
    common = common or set()
    stats = []
    for s in sources:
        vals = [math.log(s[k]["v"][series]) for k in common if k in s]
        if len(vals) >= 8:
            mu = st.mean(vals); sd = st.pstdev(vals) or 1.0
        else:
            mu, sd = 0.0, 1.0
        stats.append((mu, sd))
    sigma_star = st.mean([sd for _, sd in stats]) if stats else 1.0
    return stats, sigma_star, common

def build():
    print("FantasyCalc"); fc_p, fc_k = fantasycalc()
    print("KeepTradeCut"); kt_p, kt_k = ktc()
    print("DynastyProcess"); dp_p = dynastyprocess()

    SERIES = ["dyn_sf", "dyn_1qb", "red_sf", "red_1qb", "red_1qb_std", "dyn_1qb_std",
              "dyn_sf_tep", "dyn_1qb_tep"]
    srcs = [("fc", fc_p), ("ktc", kt_p), ("dp", dp_p)]
    out_players, spread_report = {}, {}

    import math
    scale_by_series = {}
    for series in SERIES:
        present = [(n, s) for n, s in srcs if any(series in r["v"] for r in s.values())]
        if not present: continue
        stats, sigma, common = logstats([s for _, s in present], series)
        scale_by_series[series] = (dict(zip([n for n, _ in present], stats)), sigma)
        z_by_name = {}
        for (name, s), (mu, sd) in zip(present, stats):
            for k, r in s.items():
                v = r["v"].get(series)
                if v and v > 0:
                    z_by_name.setdefault(k, {})[name] = (math.log(v) - mu) / sd
        diffs = []
        for k, d in z_by_name.items():
            rec = out_players.setdefault(k, {"v": {}, "src": {}})
            z = st.mean(d.values())
            rec["v"][series] = round(math.exp(z * sigma), 4)
            rec["src"][series] = {n: round(math.exp(zz * sigma), 4) for n, zz in d.items()}
            if len(d) >= 2:
                # disagreement in z units: how far apart are the sources, in sd of the market?
                rec.setdefault("dis", {})[series] = round(max(d.values()) - min(d.values()), 3)
                diffs.append(max(d.values()) - min(d.values()))
        spread_report[series] = (len(z_by_name), len(common),
                                 round(st.median(diffs), 3) if diffs else None)

    # carry the vote count through as the sample size behind each value
    for k, r in kt_p.items():
        if k in out_players and r.get("n"):
            out_players[k]["n"] = r["n"]
    # how many independent sources actually price this asset
    for k in out_players:
        out_players[k]["srcN"] = max((len(v) for v in out_players[k].get("src", {}).values()), default=0)

    # identity: sleeper id / position / age, from whichever source has it
    for k in out_players:
        for s in (fc_p, kt_p, dp_p):
            r = s.get(k)
            if not r: continue
            if r.get("sid") and "sid" not in out_players[k]: out_players[k]["sid"] = r["sid"]
            if r.get("pos") and "pos" not in out_players[k]: out_players[k]["pos"] = r["pos"]

    # Picks, rank-anchored onto the consensus scale (see below).
    out_picks = {}
    src_players = {"fc": fc_p, "ktc": kt_p, "dp": dp_p}
    for series in SERIES:
        if series not in scale_by_series: continue
        for name, pk in (("fc", fc_k), ("ktc", kt_k)):
            sp = src_players[name]
            # this source's own players, sorted by its own value, paired with the BLENDED value
            board = []
            for k, r in sp.items():
                v = r["v"].get(series)
                b = out_players.get(k, {}).get("v", {}).get(series)
                if v and b: board.append((v, b))
            if len(board) < 20: continue
            board.sort(key=lambda t: -t[0])
            raw = [t[0] for t in board]; blended = [t[1] for t in board]
            for key, vals in pk.items():
                v = vals.get(series)
                if not v: continue
                # where does this pick sit among that source's players? read the consensus
                # value off at the same spot. Rank-anchoring keeps a single-source pick on the
                # consensus scale instead of inheriting that source's dispersion.
                i = 0
                while i < len(raw) and raw[i] > v: i += 1
                if i == 0: est = blended[0] * (v / raw[0])
                elif i >= len(raw): est = blended[-1] * (v / raw[-1])
                else:
                    span = raw[i-1] - raw[i]
                    f = (raw[i-1] - v) / span if span else 0
                    est = blended[i-1] + (blended[i] - blended[i-1]) * f
                out_picks.setdefault(key, {}).setdefault(series, []).append(est)
    out_picks = {k: {s: round(st.mean(v), 4) for s, v in d.items()} for k, d in out_picks.items()}

    # Final scale: top asset in each series = 10000. That's the convention every published
    # trade chart uses, so the numbers land where a user's intuition already lives.
    for series in SERIES:
        vals = [r["v"][series] for r in out_players.values() if series in r["v"]]
        if not vals: continue
        f = 10000.0 / max(vals)
        for r in out_players.values():
            if series in r["v"]:
                r["v"][series] = round(r["v"][series] * f)
                r["src"][series] = {n: round(v * f) for n, v in r["src"][series].items()}
        for r in out_picks.values():
            if series in r: r[series] = round(r[series] * f)

    # Pick sanity. Thin samples on near-worthless assets produce small inversions (a "late 4th"
    # edging out an "early 4th"), and the furthest year is only quoted as a mid. Fill missing
    # tiers from the nearest year's shape, then enforce the orderings that must hold by
    # definition: earlier tier >= later tier, earlier round >= later round, sooner year >= later.
    TIERS = ["early", "mid", "late"]
    years = sorted({int(k.split("|")[0]) for k in out_picks})
    for series in SERIES:
        have = {k: v[series] for k, v in out_picks.items() if series in v}
        if len(have) < 6: continue
        for y in years:
            for rd in (1, 2, 3, 4):
                mid = have.get(f"{y}|{rd}|mid")
                if mid is None: continue
                for tier, mult in (("early", None), ("late", None)):
                    key = f"{y}|{rd}|{tier}"
                    if key in have: continue
                    # borrow the tier-to-mid ratio from the nearest year that has it
                    ratio = None
                    for oy in sorted(years, key=lambda o: abs(o - y)):
                        a, b = have.get(f"{oy}|{rd}|{tier}"), have.get(f"{oy}|{rd}|mid")
                        if a and b: ratio = a / b; break
                    if ratio: have[key] = mid * ratio
        for y in years:                                  # tier order within a round
            for rd in (1, 2, 3, 4):
                vals = [have.get(f"{y}|{rd}|{t}") for t in TIERS]
                if all(v is not None for v in vals):
                    for i in range(1, 3):
                        if vals[i] > vals[i-1]: vals[i] = vals[i-1] * 0.92
                    for t, v in zip(TIERS, vals): have[f"{y}|{rd}|{t}"] = v
        for y in years:                                  # round order within a year
            for t in TIERS:
                prev = None
                for rd in (1, 2, 3, 4):
                    k = f"{y}|{rd}|{t}"
                    if k not in have: continue
                    if prev is not None and have[k] > prev: have[k] = prev * 0.85
                    prev = have[k]
        for rd in (1, 2, 3, 4):                          # a later year is never worth more
            for t in TIERS:
                prev = None
                for y in years:
                    k = f"{y}|{rd}|{t}"
                    if k not in have: continue
                    if prev is not None and have[k] > prev: have[k] = prev * 0.95
                    prev = have[k]
        for k, v in have.items():
            out_picks.setdefault(k, {})[series] = round(v, 4)

    print("\nseries          players  common  median-source-spread")
    for s, (n, c, d) in spread_report.items():
        print(f"  {s:14} {n:6}  {c:6}  {d}")
    print(f"\npicks: {len(out_picks)} keys, e.g.")
    for k in sorted(out_picks)[:6]:
        print("   ", k, out_picks[k].get("dyn_sf"))
    return {"players": out_players, "picks": out_picks}



def build_market():
    """Entry point used by refresh.py. Returns the MARKET blob, or None on total failure."""
    try:
        data = build()
        if not data or not data.get("players"): return None
        return data
    except Exception as ex:
        print("  market: FAILED", ex)
        return None

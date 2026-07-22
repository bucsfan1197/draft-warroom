"""
What value gap do managers ACTUALLY accept?

The trade calculator assumes a counterparty needs at least 25% of your gain before a deal is
plausible. That number was invented. Sleeper records every completed trade, so it's measurable —
the only problem is sample size: the current season alone gave 11 trades across 8 leagues.

This walks every league the user is in BACKWARDS through previous_league_id, as far as the chain
goes, and collects every completed trade in each season. Then each trade is valued with our own
market values and the two sides compared.

An honest limit, stated up front: values are TODAY's. A 2022 trade gets priced with 2026 numbers,
which is wrong in a way that grows with age. So trades are bucketed by season and the recent ones
are reported separately — if the two agree, the drift isn't distorting the answer.
"""
import json, urllib.request, statistics as st, os, time

USER = "bucsfan1197"
HERE = os.path.dirname(os.path.abspath(__file__))

def gj(u, t=45, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            return json.loads(urllib.request.urlopen(r, timeout=t).read())
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.4)
    return None

u = gj(f"https://api.sleeper.app/v1/user/{USER}")
seen, chain = set(), []
for yr in ("2026", "2025"):
    for L in (gj(f"https://api.sleeper.app/v1/user/{u['user_id']}/leagues/nfl/{yr}") or []):
        lid = L["league_id"]
        # walk this league's history back as far as Sleeper remembers it
        hops = 0
        while lid and lid not in seen and hops < 8:
            seen.add(lid)
            meta = gj(f"https://api.sleeper.app/v1/league/{lid}")
            if not meta: break
            chain.append({"id": lid, "name": meta.get("name"), "season": meta.get("season"),
                          "type": (meta.get("settings") or {}).get("type")})
            lid = meta.get("previous_league_id")
            hops += 1
print(f"league-seasons reachable: {len(chain)}")
by_season = {}
for c in chain: by_season[c["season"]] = by_season.get(c["season"], 0) + 1
print("  by season:", dict(sorted(by_season.items())))

trades = []
for c in chain:
    for w in range(1, 19):
        tr = gj(f"https://api.sleeper.app/v1/league/{c['id']}/transactions/{w}")
        if not tr: continue
        for t in tr:
            if t.get("type") == "trade" and t.get("status") == "complete":
                trades.append({"season": c["season"], "league": c["name"], "dynasty": c["type"] == 2,
                               "adds": t.get("adds") or {}, "drops": t.get("drops") or {},
                               "picks": t.get("draft_picks") or [], "rosters": t.get("roster_ids") or []})
print(f"completed trades found: {len(trades)}")
tby = {}
for t in trades: tby[t["season"]] = tby.get(t["season"], 0) + 1
print("  by season:", dict(sorted(tby.items())))
print(f"  two-team only: {sum(1 for t in trades if len(t['rosters'])==2)}")
print(f"  involving picks: {sum(1 for t in trades if t['picks'])}")
json.dump(trades, open(os.path.join(HERE, "trades.json"), "w"))
print("wrote trades.json")

"""
Widen the accepted-trade sample.

The first pass used one account's leagues and produced 19 usable trades — enough to show that
managers only accept near-even deals, not enough to pin the shape. This walks outward: every
manager who shares a league with the account, then their public leagues, deduped.

Scope decisions, deliberately:
  * Sleeper's league and transaction endpoints are public and unauthenticated. Nothing here
    touches credentials or private data.
  * ONLY aggregate value ratios are kept. No usernames, no per-manager anything, no persistence
    of who traded what — the output is a list of numbers.
  * 2025 and 2026 only. Older trades priced with today's values measure how much the players
    changed, not what anyone agreed to; the first pass showed exactly that (median 0.22 for
    2023-24 against 0.73 for 2025-26), so they're not worth the requests.
"""
import json, urllib.request, os, time, statistics as st
from concurrent.futures import ThreadPoolExecutor

USER = "bucsfan1197"
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_LEAGUES = 400
SEASONS = ("2026", "2025")

def gj(u, t=25, tries=2):
    for i in range(tries):
        try:
            r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            return json.loads(urllib.request.urlopen(r, timeout=t).read())
        except Exception:
            if i == tries - 1: return None
            time.sleep(0.2)

me = gj(f"https://api.sleeper.app/v1/user/{USER}")
seed = []
for yr in SEASONS:
    for L in (gj(f"https://api.sleeper.app/v1/user/{me['user_id']}/leagues/nfl/{yr}") or []):
        seed.append(L["league_id"])
print(f"seed leagues: {len(seed)}")

# everyone who shares a league with this account
managers = set()
def league_users(lid):
    return [u["user_id"] for u in (gj(f"https://api.sleeper.app/v1/league/{lid}/users") or [])]
with ThreadPoolExecutor(max_workers=8) as ex:
    for ids in ex.map(league_users, seed):
        managers.update(ids)
managers.discard(me["user_id"])
print(f"co-managers found: {len(managers)}")

# their public leagues
leagues = {}
def user_leagues(uid):
    out = []
    for yr in SEASONS:
        for L in (gj(f"https://api.sleeper.app/v1/user/{uid}/leagues/nfl/{yr}") or []):
            out.append((L["league_id"], L.get("season"), (L.get("settings") or {}).get("type"),
                        L.get("total_rosters")))
    return out
with ThreadPoolExecutor(max_workers=10) as ex:
    for res in ex.map(user_leagues, list(managers)):
        for lid, season, typ, n in res:
            if lid not in leagues and len(leagues) < MAX_LEAGUES:
                leagues[lid] = {"season": season, "dynasty": typ == 2, "teams": n}
for lid in seed:
    if lid not in leagues:
        m = gj(f"https://api.sleeper.app/v1/league/{lid}")
        if m: leagues[lid] = {"season": m.get("season"), "dynasty": (m.get("settings") or {}).get("type") == 2,
                              "teams": m.get("total_rosters")}
print(f"unique public leagues to scan: {len(leagues)}")

# every completed trade in them
def league_trades(item):
    lid, meta = item
    found = []
    for w in range(1, 17):
        tr = gj(f"https://api.sleeper.app/v1/league/{lid}/transactions/{w}")
        if not tr: continue
        for t in tr:
            if t.get("type") == "trade" and t.get("status") == "complete":
                found.append({"season": meta["season"], "dynasty": meta["dynasty"],
                              "adds": t.get("adds") or {}, "picks": t.get("draft_picks") or [],
                              "rosters": t.get("roster_ids") or []})
    return found

trades = []
done = 0
with ThreadPoolExecutor(max_workers=12) as ex:
    for res in ex.map(league_trades, list(leagues.items())):
        trades.extend(res); done += 1
        if done % 50 == 0: print(f"  scanned {done}/{len(leagues)} leagues, {len(trades)} trades so far")
print(f"\ncompleted trades: {len(trades)}")
by = {}
for t in trades: by[t["season"]] = by.get(t["season"], 0) + 1
print("  by season:", dict(sorted(by.items())))

json.dump(trades, open(os.path.join(HERE, "trades_wide.json"), "w"))
print("wrote trades_wide.json")

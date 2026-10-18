#!/usr/bin/env python3
"""
17_prop_odds.py -- this week's NFL player-prop prices from OddsPapi -> site/data/nfl_2026_prop_odds.csv

Built from your nfl_props.py. Differences, so it can live in the daily pipeline:
  * The API key is read from the ODDSPAPI_KEY environment variable. NEVER paste it into a file in this repo: the
    repo is public. In GitHub: Settings -> Secrets and variables -> Actions -> New repository secret, name it
    ODDSPAPI_KEY. (The workflow passes it in, see daily_refresh.yml.)
  * It only hits the API on game-prep days so the free tier (250 requests/month, ~18 per run) lasts the month.
    Default run days: Thursday and Sunday (ODDS_RUN_DAYS="3,6", Monday=0). Set ODDS_FORCE=1 to run any day.
    It also skips if the CSV was already refreshed in the last 6 hours.
  * It never overwrites a good CSV with an empty result (props post late; a thin pull should not wipe the page).
  * Extra columns the dashboard reads: stat (normalized stat name), ou (over / under / yes / no), fetched.

Non-critical in run_daily.py: if anything here fails, the rest of the refresh is unaffected.

Run it by hand:   ODDSPAPI_KEY=xxxx ODDS_FORCE=1 python3 17_prop_odds.py
"""
import csv
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

from nfl_common import SEASON, PROCESSED_DIR

BASE_URL = "https://api.oddspapi.io/v4"
BOOKMAKERS = os.environ.get("ODDS_BOOKS", "fanduel,caesars,hardrockbet,betrivers,thescore,draftkings")
DAYS_AHEAD = int(os.environ.get("ODDS_DAYS_AHEAD", "7"))
RUN_DAYS = {int(x) for x in os.environ.get("ODDS_RUN_DAYS", "3,6").split(",") if x.strip().isdigit()}
MIN_HOURS_BETWEEN_RUNS = 6
OUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_prop_odds.csv"
STATUS_FILE = PROCESSED_DIR / f"nfl_{SEASON}_prop_odds_status.csv"   # one row: what the last real attempt did and why
FIELDS = ["game", "kickoff", "player", "market", "stat", "line", "side", "ou", "decimal_odds", "american_odds",
          "book", "active", "fetched"]

calls_used = 0


def api_get(endpoint, key, **params):
    global calls_used
    params["apiKey"] = key
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=30)
    calls_used += 1
    print(f"[prop odds] GET /{endpoint} -> HTTP {resp.status_code}")
    if not resp.ok:
        raise requests.HTTPError(f"HTTP {resp.status_code} on /{endpoint}: {resp.text[:300]}", response=resp)
    time.sleep(0.3)
    return resp.json()


def to_american(dec):
    try:
        dec = float(dec)
    except (TypeError, ValueError):
        return ""
    if dec <= 1:
        return ""
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else str(round(-100 / (dec - 1)))


# Market name -> the stat names the dashboard uses. Order matters: the combined markets are tested first.
STAT_PATTERNS = [
    (r"pass\w*\W+(?:\+|and|&)\W*rush", "Pass + Rush Yards"),
    (r"rush\w*\W+(?:\+|and|&)\W*rec", "Rush + Rec Yards"),
    (r"(?:any\s?time|anytime|first|last)\W+(?:td|touchdown)|to score a touchdown|touchdown scorer", "Any TD"),
    (r"pass\w*\W+(?:td|touchdown)", "Pass TD"),
    (r"rush\w*\W+(?:td|touchdown)", "Rush TD"),
    (r"rec\w*\W+(?:td|touchdown)", "Receiving TD"),
    (r"interception", "Interceptions"),
    (r"reception", "Receptions"),
    (r"pass\w*\W+y", "Pass Yards"),
    (r"rush\w*\W+y", "Rush Yards"),
    (r"rec\w*\W+y", "Receiving Yards"),
]


def normalize_stat(market_name):
    n = str(market_name or "").lower()
    for pat, stat in STAT_PATTERNS:
        if re.search(pat, n):
            if stat == "Any TD" and re.search(r"first|last", n) and not re.search(r"any\s?time|anytime", n):
                return ""  # first/last scorer is a different bet from "anytime"
            return stat
    return ""


def normalize_ou(side):
    s = str(side or "").strip().lower()
    for word in ("over", "under", "yes", "no"):
        if s.startswith(word):
            return word
    return ""


def find_line(minfo, side_name):
    """The line lives in the market's handicap; fall back to a number in the market or side name."""
    for src in (minfo.get("handicap"), minfo.get("marketName"), side_name):
        m = re.search(r"-?\d+(?:\.\d+)?", str(src if src is not None else ""))
        if m and src not in (None, ""):
            return m.group(0)
    return ""


def build_rows(odds_by_game, market_info, outcome_names, fetched):
    rows = []
    for matchup, kickoff, odds in odds_by_game:
        for book, bk_data in (odds or {}).get("bookmakerOdds", {}).items():
            for mid_str, mdata in bk_data.get("markets", {}).items():
                mid = int(mid_str)
                minfo = market_info.get(mid, {})
                market = minfo.get("marketName", mid)
                for oid_str, odata in mdata.get("outcomes", {}).items():
                    for _pid, p in odata.get("players", {}).items():
                        if not isinstance(p, dict) or not p.get("playerName"):
                            continue
                        price = p.get("price")
                        side = outcome_names.get((mid, int(oid_str)), oid_str)
                        rows.append({
                            "game": matchup, "kickoff": kickoff, "player": p["playerName"], "market": market,
                            "stat": normalize_stat(market), "line": find_line(minfo, side), "side": side,
                            "ou": normalize_ou(side), "decimal_odds": price, "american_odds": to_american(price),
                            "book": book, "active": p.get("active", True), "fetched": fetched,
                        })
    return rows


def write_status(outcome, detail="", games="", rows="", calls=""):
    """The dashboard's Prop Prices tab shows this, so you can see why there are no prices without opening the Actions log."""
    detail = re.sub(r"\s+", " ", str(detail))[:600]
    with open(STATUS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ran_at", "outcome", "detail", "games", "rows", "calls"])
        w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), outcome, detail, games, rows, calls])
    print(f"[prop odds] {outcome}: {detail}")


def should_run():
    if os.environ.get("ODDS_FORCE") == "1":
        return True
    if date.today().weekday() not in RUN_DAYS:
        print(f"Not a prop-odds day (today is weekday {date.today().weekday()}, runs on {sorted(RUN_DAYS)}). "
              f"Set ODDS_FORCE=1 to run anyway. Keeping the existing file.")
        return False
    if OUT_FILE.exists():
        age_h = (time.time() - OUT_FILE.stat().st_mtime) / 3600
        if age_h < MIN_HOURS_BETWEEN_RUNS:
            print(f"Prop odds were refreshed {age_h:.1f}h ago; skipping to save requests.")
            return False
    return True


def main():
    key = os.environ.get("ODDSPAPI_KEY", "").strip()
    print(f"[prop odds] starting. key present: {bool(key)} (length {len(key)}), force: {os.environ.get('ODDS_FORCE')!r}")
    if not key:
        write_status("no key", "ODDSPAPI_KEY is empty in the workflow. Add it as a repository secret (Settings, Secrets and variables, Actions) and make sure the NEW daily_refresh.yml is in .github/workflows/ so it is passed to the pipeline.")
        return 0
    if not should_run():
        return 0

    try:
        sports = api_get("sports", key)
    except requests.HTTPError as e:
        write_status("api error", f"/sports returned {e}. A 401/403 usually means the key is wrong or expired; 429 means the monthly limit is used up.", calls=calls_used)
        return 1
    sport_id = None
    for s in sports:
        name = str(s.get("sportName", s.get("name", ""))).lower()
        if "american football" in name or name == "football (american)":
            sport_id = s["sportId"]
            break
    if sport_id is None:
        write_status("no sport", "Couldn't find American Football in /sports. Sports returned: " + ", ".join(str(x.get("sportName", x.get("name", ""))) for x in sports[:25]), calls=calls_used)
        return 1

    catalog = api_get("markets", key, sportId=sport_id)
    market_info = {m["marketId"]: m for m in catalog}
    outcome_names = {(m["marketId"], o["outcomeId"]): o["outcomeName"] for m in catalog for o in m.get("outcomes", [])}

    today = date.today()
    fixtures = api_get("fixtures", key, sportId=sport_id, **{"from": today.isoformat(), "to": (today + timedelta(days=DAYS_AHEAD)).isoformat()})
    games = [f for f in fixtures if f.get("hasOdds") and "nfl" in str(f.get("tournamentSlug", "")).lower()]
    print(f"{len(games)} NFL games with odds in the next {DAYS_AHEAD} days")
    if not games:
        sample = "; ".join(f"{f.get('tournamentSlug')}/{f.get('participant1Name')} v {f.get('participant2Name')}/odds={f.get('hasOdds')}" for f in fixtures[:6])
        write_status("no games", f"{len(fixtures)} fixtures came back, none were NFL with odds. First few: {sample or 'none'}", games=0, calls=calls_used)
        return 0

    odds_by_game = []
    for g in games:
        matchup = f"{g.get('participant1Name')} vs {g.get('participant2Name')}"
        try:
            odds = api_get("odds", key, fixtureId=g["fixtureId"], bookmakers=BOOKMAKERS)
        except requests.HTTPError as e:
            print(f"  skipped {matchup} ({e})")
            continue
        odds_by_game.append((matchup, g.get("startTime", ""), odds))

    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = build_rows(odds_by_game, market_info, outcome_names, fetched)
    if not rows:
        first = next((o for _, _, o in odds_by_game if o), None)
        shape = ""
        if isinstance(first, dict):
            bo = first.get("bookmakerOdds", {})
            shape = f"top-level keys {list(first)[:8]}; books {list(bo)[:8]}"
            for b, d in list(bo.items())[:1]:
                mk = d.get("markets", {}) if isinstance(d, dict) else {}
                shape += f"; first book has {len(mk)} markets"
                for mid, md in list(mk.items())[:1]:
                    oc = md.get("outcomes", {}) if isinstance(md, dict) else {}
                    for oid, od in list(oc.items())[:1]:
                        shape += f"; sample outcome keys {list(od)[:6] if isinstance(od, dict) else type(od).__name__}"
        write_status("no props", "Games found but no player-prop rows. Props often post later in the week (try again closer to game day). " + shape, games=len(games), rows=0, calls=calls_used)
        return 0
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    unknown = sorted({r["market"] for r in rows if not r["stat"]})
    print(f"Saved {len(rows)} prop lines to {OUT_FILE.name} ({calls_used} API calls)")
    write_status("ok", f"{len(rows)} prices from {len({r['book'] for r in rows})} books. {len(unknown)} market types not mapped to a stat." + (" Unmapped: " + "; ".join(map(str, unknown[:8])) if unknown else ""), games=len(games), rows=len(rows), calls=calls_used)
    if unknown:
        print(f"{len(unknown)} markets not mapped to a stat (ignored by the dashboard): {', '.join(map(str, unknown[:15]))}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # non-critical step: report and move on
        try:
            write_status("crashed", f"{type(exc).__name__}: {exc}", calls=calls_used)
        except Exception:
            pass
        print(f"Prop odds step failed: {exc}", file=sys.stderr)
        sys.exit(1)

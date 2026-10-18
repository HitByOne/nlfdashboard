#!/usr/bin/env python3
"""
17_prop_odds.py -- this week's NFL player-prop prices from OddsPapi (v5 API) -> site/data/nfl_2026_prop_odds.csv

Rebuilt for OddsPapi v5 (https://docs.oddspapi.io): base https://v5.oddspapi.io/en, key sent in the X-API-Key header.
  * The API key is read from the ODDSPAPI_KEY environment variable. NEVER paste it into a file in this repo: the
    repo is public. In GitHub: Settings -> Secrets and variables -> Actions -> New repository secret, name it
    ODDSPAPI_KEY. (The workflow passes it in, see daily_refresh.yml.)
  * It only hits the API on game-prep days so a limited plan lasts the month (about 25 requests per run: a few lookups plus one per game).
    Default run days: Thursday and Sunday (ODDS_RUN_DAYS="3,6", Monday=0). Set ODDS_FORCE=1 to run any day.
    It also skips if the CSV was already refreshed in the last 6 hours.
  * It never overwrites a good CSV with an empty result (props post late; a thin pull should not wipe the page).
  * Extra columns the dashboard reads: stat (normalized stat name), ou (over / under / yes / no), fetched.

Runs as its own workflow step (continue-on-error): if anything here fails, the rest of the refresh is unaffected.

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

BASE_URL = "https://v5.oddspapi.io/en"
WANT_BOOKS = [b.strip() for b in os.environ.get("ODDS_BOOKS", "fanduel,draftkings,caesars,betmgm,betrivers,hardrockbet,espnbet,fanatics").split(",") if b.strip()]
DAYS_AHEAD = int(os.environ.get("ODDS_DAYS_AHEAD", "7"))
RUN_DAYS = {int(x) for x in os.environ.get("ODDS_RUN_DAYS", "3,6").split(",") if x.strip().isdigit()}
MIN_HOURS_BETWEEN_RUNS = 6
OUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_prop_odds.csv"
STATUS_FILE = PROCESSED_DIR / f"nfl_{SEASON}_prop_odds_status.csv"   # one row: what the last real attempt did and why
FIELDS = ["game", "kickoff", "player", "market", "stat", "line", "side", "ou", "decimal_odds", "american_odds",
          "book", "active", "fetched"]

calls_used = 0
_KEY = ""


def api_get(endpoint, **params):
    """GET one v5 endpoint. The key goes in a header so it never lands in a URL or a log line."""
    global calls_used
    params = {k: v for k, v in params.items() if v is not None}
    for attempt in (1, 2):
        resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, headers={"X-API-Key": _KEY}, timeout=40)
        calls_used += 1
        print(f"[prop odds] GET /{endpoint} {({k: v for k, v in params.items() if k != 'playerIds'})} -> HTTP {resp.status_code}")
        if resp.status_code == 429 and attempt == 1:
            try:
                wait = float(resp.json().get("error", {}).get("retryAfterSec", 5))
            except Exception:
                wait = 5
            time.sleep(min(wait, 30) + 0.5)
            continue
        break
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


def flip_name(name):
    """OddsPapi lists players as 'Last, First'; the dashboard uses 'First Last'."""
    name = str(name or "").strip()
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
        return f"{first} {last}".strip()
    return name


def build_rows(fixture_odds, markets, players, fetched):
    """fixture_odds: list of (game label, kickoff iso, odds dict book -> {key -> quote})."""
    outcome_names = {}
    for m in markets.values():
        for o in m.get("outcomes", []) or []:
            outcome_names[o.get("outcomeId")] = o.get("outcomeName")
    rows = []
    for game, kickoff, odds in fixture_odds:
        for book, quotes in (odds or {}).items():
            for _k, q in (quotes or {}).items():
                pid = q.get("playerId") or 0
                if not pid:
                    continue
                m = markets.get(q.get("marketId"))
                if not m or not m.get("playerProp"):
                    continue
                price = q.get("price")
                side = outcome_names.get(q.get("outcomeId"), "")
                am = q.get("priceAmerican")
                rows.append({
                    "game": game, "kickoff": kickoff, "player": flip_name(players.get(pid, "")), "market": m.get("marketName") or "",
                    "stat": normalize_stat(m.get("marketName")), "line": "" if m.get("handicap") in (None, 0, 0.0) else m.get("handicap"),
                    "side": side, "ou": normalize_ou(side), "decimal_odds": price,
                    "american_odds": (f"{am:+d}" if isinstance(am, int) else to_american(price)),
                    "book": book, "active": bool(q.get("active", True) and q.get("marketActive", True)), "fetched": fetched,
                })
    return [r for r in rows if r["player"]]


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
    global _KEY
    _KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
    print(f"[prop odds] starting (v5 API). key present: {bool(_KEY)} (length {len(_KEY)}), force: {os.environ.get('ODDS_FORCE')!r}")
    if not _KEY:
        write_status("no key", "ODDSPAPI_KEY is empty in the workflow. Add it as a repository secret (Settings, Secrets and variables, Actions) and make sure the NEW daily_refresh.yml is in .github/workflows/ so it is passed to the step.")
        return 0
    if not should_run():
        return 0

    try:
        sports = api_get("sports")
    except requests.HTTPError as e:
        write_status("api error", f"/sports -> {e}. 401/403: the key is wrong or is a v4 key (v5 keys come from your oddspapi.io account). 429: request limit used up.", calls=calls_used)
        return 1
    sport_id = next((s["sportId"] for s in sports if "american football" in str(s.get("sportName", "")).lower()), None)
    if sport_id is None:
        write_status("no sport", "American Football not in /sports. Returned: " + ", ".join(str(s.get("sportName")) for s in sports[:25]), calls=calls_used)
        return 1

    tournaments = api_get("tournaments", sportId=sport_id)
    nfl = [t for t in tournaments if str(t.get("tournamentName", "")).strip().lower() == "nfl"] or \
          [t for t in tournaments if "nfl" in str(t.get("tournamentSlug", "")).lower() and "ncaa" not in str(t.get("tournamentSlug", "")).lower()]
    if not nfl:
        write_status("no tournament", "No NFL tournament under American Football. Found: " + "; ".join(f"{t.get('tournamentId')}={t.get('tournamentName')}" for t in tournaments[:25]), calls=calls_used)
        return 1
    tournament_id = nfl[0]["tournamentId"]

    # Only ask for books that actually publish player props.
    books = []
    try:
        listed = api_get("bookmakers", playerProps="true")
        avail = {b.get("slug") for b in listed if b.get("active", True)}
        books = [b for b in WANT_BOOKS if b in avail]
        print(f"[prop odds] player-prop books available: {sorted(avail)[:40]}; using {books}")
    except Exception as e:
        print(f"[prop odds] couldn't list bookmakers ({e}); asking for the default list")
        books = WANT_BOOKS
    if not books:
        write_status("no books", "None of the wanted books offer player props on your plan. Available: " + ", ".join(sorted(avail)[:30]) + ". Set ODDS_BOOKS to pick others.", calls=calls_used)
        return 0
    book_param = ",".join(books)

    now = int(time.time())
    fixtures = api_get("fixtures", tournamentId=tournament_id, startTimeFrom=now - 3600, startTimeTo=now + DAYS_AHEAD * 86400)
    games = [f for f in fixtures if (f.get("status") or {}).get("statusId", 0) in (0, 1)]
    print(f"{len(games)} NFL fixtures in the next {DAYS_AHEAD} days")
    if not games:
        write_status("no games", f"{len(fixtures)} fixtures came back for the NFL (tournament {tournament_id}) in the next {DAYS_AHEAD} days, none upcoming.", games=0, calls=calls_used)
        return 0

    fixture_odds = []
    for g in games:
        parts = g.get("participants") or {}
        label = f"{parts.get('participant1Name')} vs {parts.get('participant2Name')}"
        kickoff = datetime.fromtimestamp(g["startTime"], timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if g.get("startTime") else ""
        try:
            data = api_get("fixtures/odds", fixtureId=g["fixtureId"], bookmakers=book_param)
        except requests.HTTPError as e:
            print(f"  skipped {label} ({e})")
            continue
        fixture_odds.append((label, kickoff, data.get("odds") or {}))

    mk = api_get("markets", sportId=sport_id)
    markets = {m["marketId"]: m for m in mk}

    needed = sorted({q.get("playerId") for _, _, o in fixture_odds for qs in o.values() for q in qs.values() if q.get("playerId")})
    players = {}
    for i in range(0, len(needed), 150):
        chunk = needed[i:i + 150]
        try:
            for pl in api_get("players", playerIds=",".join(map(str, chunk))):
                players[pl["playerId"]] = pl.get("playerName")
        except requests.HTTPError as e:
            print(f"  player lookup failed for a chunk ({e})")

    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = build_rows(fixture_odds, markets, players, fetched)
    if not rows:
        total = sum(len(qs) for _, _, o in fixture_odds for qs in o.values())
        pp = sum(1 for _, _, o in fixture_odds for qs in o.values() for q in qs.values() if q.get("playerId"))
        write_status("no props", f"{len(games)} games, {total} prices came back, {pp} were player prices, none usable. Props often post later in the week (try again closer to game day). Books asked: {book_param}.", games=len(games), rows=0, calls=calls_used)
        return 0
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    unknown = sorted({r["market"] for r in rows if not r["stat"]})
    print(f"Saved {len(rows)} prop lines to {OUT_FILE.name} ({calls_used} API calls)")
    write_status("ok", f"{len(rows)} prices from {len({r['book'] for r in rows})} books. {len(unknown)} market types not mapped to a stat." + (" Unmapped: " + "; ".join(map(str, unknown[:8])) if unknown else ""), games=len(games), rows=len(rows), calls=calls_used)
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

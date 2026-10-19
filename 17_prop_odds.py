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

Run it by hand:   RAPIDAPI_KEY=xxxx ODDS_FORCE=1 python3 17_prop_odds.py   (or ODDSPAPI_KEY=... for the direct oddspapi.io API)
"""
import csv
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

from nfl_common import SEASON, PROCESSED_DIR

# Two ways in. RapidAPI (RAPIDAPI_KEY set): host odds-api1.p.rapidapi.com, key in x-rapidapi-key. Direct (ODDSPAPI_KEY): v5.oddspapi.io.
RAPID_HOST = os.environ.get("ODDS_RAPIDAPI_HOST", "odds-api1.p.rapidapi.com").strip()
USE_RAPID = bool(os.environ.get("RAPIDAPI_KEY", "").strip())
BASE_URL = f"https://{RAPID_HOST}" if USE_RAPID else "https://v5.oddspapi.io/en"
WANT_BOOKS = [b.strip() for b in os.environ.get("ODDS_BOOKS", "fanduel,draftkings").split(",") if b.strip()]
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
        hdrs = {"x-rapidapi-key": _KEY, "x-rapidapi-host": RAPID_HOST} if USE_RAPID else {"X-API-Key": _KEY}
        resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, headers=hdrs, timeout=40)
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


def start_epoch(v):
    """startTime comes as epoch seconds in one version and an ISO string in another."""
    if v in (None, ""):
        return 0
    try:
        n = float(v)
        return int(n / 1000) if n > 1e11 else int(n)
    except (TypeError, ValueError):
        try:
            return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0


def normalize_odds(data):
    """Return ({book: {key: quote}}, {playerId: name}) from either response layout.
    v5: data['odds'][book][key] = {marketId, outcomeId, playerId, price, ...}
    v4: data['bookmakerOdds'][book]['markets'][marketId]['outcomes'][outcomeId]['players'][playerId] = {price, active, playerName}"""
    names = {}
    if isinstance(data, dict) and isinstance(data.get("odds"), dict):
        return data["odds"], names
    out = {}
    for book, bk in ((data or {}).get("bookmakerOdds") or {}).items():
        quotes = out.setdefault(book, {})
        for mid, md in (bk.get("markets") or {}).items():
            for oid, od in (md.get("outcomes") or {}).items():
                for pid, pl in (od.get("players") or {}).items():
                    if not isinstance(pl, dict) or str(pid) in ("0", ""):
                        continue
                    if pl.get("playerName"):
                        names[int(pid)] = pl["playerName"]
                    quotes[f"{mid}:{oid}:{pid}"] = {"marketId": int(mid), "outcomeId": int(oid), "playerId": int(pid), "price": pl.get("price"),
                                                    "priceAmerican": None, "active": pl.get("active", True), "marketActive": True}
    return out, names


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
            if book not in WANT_BOOKS:      # only the books you asked for, even if the API sends more
                continue
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
                    "_main": q.get("mainLine"),
                })
    for r in rows:
        r.pop("_main", None)
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
    _KEY = (os.environ.get("RAPIDAPI_KEY", "") or os.environ.get("ODDSPAPI_KEY", "")).strip()
    print(f"[prop odds] starting. via {'RapidAPI ' + RAPID_HOST if USE_RAPID else 'oddspapi.io direct'}. key present: {bool(_KEY)} (length {len(_KEY)}), force: {os.environ.get('ODDS_FORCE')!r}")
    if not _KEY:
        write_status("no key", "No key reached the workflow. Add RAPIDAPI_KEY as a repository secret (Settings, Secrets and variables, Actions, Repository secrets) and make sure the NEW daily_refresh.yml is in .github/workflows/ so it is passed to the step.")
        return 0
    if not should_run():
        return 0

    # American football is sport 14 and the NFL is tournament 31 in OddsPapi. Look them up when the API offers it, else use those.
    sport_id, tournament_id = 14, 31
    try:
        sports = api_get("sports")
        sport_id = next((s["sportId"] for s in sports if "american football" in str(s.get("sportName", "")).lower()), sport_id)
        tournaments = api_get("tournaments", sportId=sport_id)
        nfl = [t for t in tournaments if str(t.get("tournamentName", "")).strip().lower() == "nfl"] or \
              [t for t in tournaments if "nfl" in str(t.get("tournamentSlug", "")).lower() and "ncaa" not in str(t.get("tournamentSlug", "")).lower()]
        if nfl:
            tournament_id = nfl[0]["tournamentId"]
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403, 429):
            write_status("api error", f"{e}. 401/403: the key was rejected (check it is the RapidAPI key for this API and that you are subscribed). 429: request limit used up.", calls=calls_used)
            return 1
        print(f"[prop odds] sport/tournament lookup unavailable ({e}); using sport 14, tournament 31")
    print(f"[prop odds] sport {sport_id}, tournament {tournament_id}")

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
    iso_from, iso_to = date.today().isoformat(), (date.today() + timedelta(days=DAYS_AHEAD + 1)).isoformat()
    fixtures = None
    errors = []
    styles = [("fixtures", {"sportId": sport_id, "tournamentId": tournament_id, "startTimeFrom": now - 3600, "startTimeTo": now + DAYS_AHEAD * 86400}),
              ("fixtures", {"sportId": sport_id, "tournamentId": tournament_id, "from": iso_from, "to": iso_to}),
              ("fixtures", {"sportId": sport_id, "tournamentId": tournament_id}),
              ("fixtures", {"tournamentId": tournament_id}),
              ("fixtures", {"sportId": sport_id}),
              ("fixtures", {})]
    for path, prm in styles:
        try:
            fixtures = api_get(path, **prm)
            if isinstance(fixtures, dict):   # some wrappers nest the list
                fixtures = next((v for v in fixtures.values() if isinstance(v, list)), [])
            break
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403, 429):
                write_status("api error", f"/fixtures -> {e}", calls=calls_used)
                return 1
            errors.append(f"{sorted(prm)} -> {str(e)[:160]}")
            print(f"[prop odds] /fixtures rejected {sorted(prm)} ({e}); trying the next style")
    if fixtures is None:
        write_status("api error", "/fixtures rejected every request. Errors: " + " | ".join(errors[:3]) + f" (host {RAPID_HOST if USE_RAPID else 'direct'}, sport {sport_id}, tournament {tournament_id})", calls=calls_used)
        return 1
    window_end = now + DAYS_AHEAD * 86400
    games = []
    for f in fixtures:
        ts = start_epoch(f.get("startTime"))
        st = (f.get("status") or {}).get("statusId", 0)
        if st not in (0, 1):
            continue
        if ts and not (now - 3600 <= ts <= window_end):
            continue
        games.append(f)
    print(f"{len(games)} NFL fixtures in the next {DAYS_AHEAD} days")
    if not games:
        write_status("no games", f"{len(fixtures)} fixtures came back for the NFL (tournament {tournament_id}), none upcoming in the next {DAYS_AHEAD} days.", games=0, calls=calls_used)
        return 0

    fixture_odds = []
    players = {}   # playerId -> name, filled from the odds when they carry names, else from /players
    for g in games:
        parts = g.get("participants") or {}
        label = f"{parts.get('participant1Name')} vs {parts.get('participant2Name')}"
        ts = start_epoch(g.get("startTime"))
        kickoff = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if ts else ""
        try:
            data = api_get("fixtures/odds", fixtureId=g["fixtureId"], bookmakers=book_param)   # all lines; the dashboard hides alternates
        except requests.HTTPError as e:
            print(f"  skipped {label} ({e})")
            continue
        odds, names = normalize_odds(data)
        players.update(names)
        fixture_odds.append((label, kickoff, odds))

    mk = api_get("markets", sportId=sport_id)
    markets = {m["marketId"]: m for m in mk}

    needed = sorted({q.get("playerId") for _, _, o in fixture_odds for qs in o.values() for q in qs.values() if q.get("playerId")})
    for i in range(0, len(needed), 150):
        chunk = [n for n in needed[i:i + 150] if n not in players]
        if not chunk:
            continue
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

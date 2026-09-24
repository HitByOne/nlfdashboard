"""
08_injury_report.py

Pulls each team's current injury report from ESPN. This uses a DIFFERENT
ESPN subdomain than the rest of this pipeline (sports.core.api.espn.com,
the "core" API, vs site.api.espn.com used elsewhere) -- found via public
documentation of ESPN's endpoints, but NOT verified against a live
response from where this was written (that domain wasn't reachable).

Written defensively for that reason: ESPN's core API commonly returns
paged collections where each item is a {"$ref": "..."} pointer needing a
follow-up fetch, rather than the fully-inlined objects site.api.espn.com
returns -- this handles that pattern, but if the real shape differs, it
prints the raw item once so it can be fixed with certainty on the first
real run, same approach that correctly fixed player position earlier.

Non-critical in run_daily.py -- if this fails, the rest of the dashboard
still refreshes normally.
"""
import sys
import time

import pandas as pd
import requests

from nfl_common import SEASON, PROCESSED_DIR, get_teams

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_injury_report.csv"
INJURIES_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams/{team_id}/injuries"

_debug_logged = False


def fetch_team_injuries(team_id: str, team_abbr: str) -> list[dict]:
    global _debug_logged

    resp = requests.get(INJURIES_URL.format(team_id=team_id), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    rows = []

    for item in items:
        # ESPN's core API can return this two different ways: sometimes the
        # injury item is fully inlined already (status/type/details present
        # directly, just the nested "athlete" field is a $ref), and
        # sometimes the item itself is nothing but a bare {"$ref": "..."}
        # pointer with no other keys at all -- confirmed from a real
        # response. Handle both: resolve the item's own $ref first if
        # that's all it has, then resolve the athlete's $ref as before.
        if isinstance(item, dict) and set(item.keys()) == {"$ref"}:
            try:
                item_resp = requests.get(item["$ref"], timeout=30)
                item_resp.raise_for_status()
                item = item_resp.json()
            except Exception as exc:
                print(f"    Failed to resolve injury item for {team_abbr}: {exc}")
                continue

        athlete = item.get("athlete", {})
        player_name = None
        if isinstance(athlete, dict):
            if athlete.get("displayName"):
                player_name = athlete.get("displayName")
            elif athlete.get("$ref"):
                try:
                    athlete_resp = requests.get(athlete["$ref"], timeout=30)
                    athlete_resp.raise_for_status()
                    player_name = athlete_resp.json().get("displayName")
                except Exception as exc:
                    print(f"    Failed to resolve athlete name for {team_abbr}: {exc}")

        status = item.get("status")
        if isinstance(status, dict):
            status = status.get("description") or status.get("name")

        details = item.get("details")
        injury_type = None
        if isinstance(details, dict):
            injury_type = details.get("type") or details.get("detail")
        if not injury_type:
            injury_type = item.get("shortComment") or item.get("longComment")

        if not player_name:
            if not _debug_logged:
                print(f"    DEBUG: unrecognized injury item shape for {team_abbr}. Raw item: {item}")
                _debug_logged = True
            continue

        rows.append({
            "Team": team_abbr,
            "Player": player_name,
            "Status": status or "Unknown",
            "Injury": injury_type or "",
        })
        time.sleep(0.1)  # be polite -- this now makes one extra call per injury

    return rows


def main():
    teams = get_teams(SEASON)
    print(f"Found {len(teams)} teams.")

    all_rows = []
    for team_id, team_abbr in teams:
        print(f"Fetching injuries for {team_abbr}...")
        try:
            rows = fetch_team_injuries(team_id, team_abbr)
            all_rows.extend(rows)
            print(f"    {len(rows)} injury report entries.")
        except Exception as exc:
            print(f"    Failed to fetch injuries for {team_abbr}: {exc}")
        time.sleep(0.2)

    if not all_rows:
        print("\nNo injury rows collected. If you saw DEBUG lines above, that's the real "
              "ESPN response shape -- send it back and the parsing can be fixed for certain.")
        sys.exit(1)

    df = pd.DataFrame(all_rows)
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Tracked {len(df)} injury report entries across {df['Team'].nunique()} teams.")
    print("\nSample:")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()

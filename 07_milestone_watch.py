"""
07_milestone_watch.py

TRUE career milestone tracking, using ESPN's own per-athlete career stats
endpoint -- verified against a REAL response (Josh Allen's actual stats
page) rather than guessed. This replaces the earlier nflverse-based
approach entirely: nflverse stopped at 2024 and needed a manual backfill
for the gap season, whereas this endpoint's "totals" field is ESPN's own
pre-computed career sum across every season, already including whatever
has happened in the most recent season and even the current one in
progress. One call per player gets the complete, always-current total --
no separate season-baseline math needed at all.

Verified real shape (site.web.api.espn.com/apis/common/v3/sports/football/
nfl/athletes/{id}/stats):
  data["categories"] -> list of stat categories (passing/rushing/receiving/...)
  category["names"]  -> machine-readable stat keys, e.g. "passingTouchdowns"
  category["totals"] -> career sum, positionally matching category["names"]
                         (values are strings, sometimes comma-formatted,
                         e.g. "30,684" -- stripped before converting)

Non-critical in run_daily.py -- if ESPN changes this shape again, the rest
of the dashboard still refreshes normally, and this prints a DEBUG line
with the raw category list for whichever player it can't parse, same
safety net used elsewhere in this pipeline.
"""
import sys
import time

import pandas as pd
import requests

from nfl_common import SEASON, PROCESSED_DIR, get_teams

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_milestones.csv"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
STATS_URL = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{id}/stats"

TRACKED_POSITIONS = {"QB", "RB", "WR", "TE"}
MIN_CAREER_TOTAL = 5  # skip near-zero totals as noise

MILESTONE_STEP = {
    "Pass TD": 50, "Rush TD": 50, "Receiving TD": 50, "Receptions": 50,
    "Pass Yards": 1000, "Rush Yards": 1000, "Receiving Yards": 1000,
    "Interceptions": 25,
}

# Our stat name -> (ESPN category name, ESPN stat key within that category).
STAT_MAP = {
    "Pass TD":         ("passing", "passingTouchdowns"),
    "Pass Yards":      ("passing", "passingYards"),
    "Interceptions":   ("passing", "interceptions"),
    "Rush TD":         ("rushing", "rushingTouchdowns"),
    "Rush Yards":      ("rushing", "rushingYards"),
    "Receiving TD":    ("receiving", "receivingTouchdowns"),
    "Receiving Yards": ("receiving", "receivingYards"),
    "Receptions":      ("receiving", "receptions"),
}

_debug_logged = False


def get_stat_total(stats_json: dict, category_name: str, stat_key: str, player_label: str):
    global _debug_logged
    categories = stats_json.get("categories", [])
    for cat in categories:
        if cat.get("name") != category_name:
            continue
        names = cat.get("names", [])
        if stat_key not in names:
            continue
        idx = names.index(stat_key)
        totals = cat.get("totals", [])
        if idx >= len(totals):
            continue
        raw = totals[idx]
        try:
            return float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def get_roster(team_id: str, team_abbr: str) -> list[tuple[str, str, str]]:
    """Returns list of (athlete_id, player_name, position) for tracked positions."""
    resp = requests.get(ROSTER_URL.format(team_id=team_id), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    players = []
    for group in data.get("athletes", []):
        for athlete in group.get("items", []):
            pos = (athlete.get("position") or {}).get("abbreviation")
            if pos not in TRACKED_POSITIONS:
                continue
            players.append((athlete.get("id"), athlete.get("displayName") or athlete.get("fullName"), pos))
    return players


def next_milestone(current: float, stat_name: str) -> tuple[float, float]:
    step = MILESTONE_STEP[stat_name]
    if current <= 0:
        return step, step
    if current % step == 0:
        milestone = current + step
    else:
        milestone = ((current // step) + 1) * step
    return milestone, milestone - current


def main():
    global _debug_logged

    print("Fetching team list...")
    teams = get_teams(SEASON)
    print(f"Found {len(teams)} teams.\n")

    rows = []
    for team_id, team_abbr in teams:
        print(f"Rostering {team_abbr}...")
        try:
            roster = get_roster(team_id, team_abbr)
        except Exception as exc:
            print(f"    Failed to fetch roster for {team_abbr}: {exc}")
            continue
        time.sleep(0.15)

        for athlete_id, player_name, position in roster:
            if not athlete_id or not player_name:
                continue
            try:
                resp = requests.get(STATS_URL.format(id=athlete_id), timeout=20)
                resp.raise_for_status()
                stats_json = resp.json()
            except Exception as exc:
                print(f"    Failed to fetch stats for {player_name}: {exc}")
                continue
            time.sleep(0.15)

            found_any = False
            for stat_name, (cat_name, stat_key) in STAT_MAP.items():
                current = get_stat_total(stats_json, cat_name, stat_key, player_name)
                if current is None or current < MIN_CAREER_TOTAL:
                    continue
                found_any = True
                milestone, remaining = next_milestone(current, stat_name)
                rows.append({
                    "Player": player_name, "Team": team_abbr, "Position": position,
                    "Stat": stat_name, "Current Total": int(current),
                    "Milestone": int(milestone), "Remaining": int(remaining),
                })

            if not found_any and not _debug_logged:
                print(f"    DEBUG: no tracked stats matched for {player_name}. "
                      f"Categories present: {[c.get('name') for c in stats_json.get('categories', [])]}")
                _debug_logged = True

    if not rows:
        print("\nNo milestone rows produced -- check the DEBUG line above for the real response shape.")
        sys.exit(1)

    result = pd.DataFrame(rows).sort_values(["Remaining", "Current Total"], ascending=[True, False])
    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Tracked {len(result)} player/stat milestone rows across {len(teams)} teams.")
    print("\nClosest to hitting a milestone right now:")
    print(result.head(10)[["Player", "Team", "Stat", "Current Total", "Milestone", "Remaining"]].to_string(index=False))


if __name__ == "__main__":
    main()

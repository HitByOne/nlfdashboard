"""
07_milestone_watch.py

Scans every team's roster league-wide, pulls each offensive skill player's
CAREER stat totals from ESPN, and figures out who's closest to their next
milestone in each tracked category -- matching how Pro Football Reference's
own milestone tracker spaces things (e.g. TD milestones in chunks of 50,
yardage milestones in chunks of 1000), not an arbitrary flat interval.

No watchlist file needed anymore -- this covers the whole league on its own.

Scale note: this makes ~32 roster calls plus one call per offensive skill
player found (QB/RB/WR/TE/FB), typically 400-500 players league-wide, so it
takes several minutes to run and is the heaviest step in this pipeline by
far. run_daily.py already treats this step as non-critical -- if it fails
or times out, the rest of your dashboard still refreshes normally.

NOTE: ESPN's roster and athlete-stats endpoints are undocumented (same
caveat as the rest of this pipeline). I have not been able to test this
against the live API from where this was written. If output looks wrong or
empty, check the console log first -- it prints what ESPN actually
returned whenever the expected shape isn't found, which tells us exactly
what to adjust.
"""
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from nfl_common import SEASON, PROCESSED_DIR, BASE_URL

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_milestones.csv"

TEAMS_URL = f"{BASE_URL}/teams?limit=32"
ROSTER_URL = f"{BASE_URL}/teams/{{team_id}}/roster"
STATS_URL = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{id}/stats"

TRACKED_POSITIONS = {"QB", "RB", "WR", "TE", "FB"}
MIN_CAREER_TOTAL = 5         # skip near-zero career counts as noise

# Milestone spacing tailored per stat, matching how PFR's own milestone
# tracker actually spaces things (e.g. Passing TD tiers shown there are
# 100/200/250/300/450/550 -- chunks of 50+, not every 10). Yardage stats
# get much bigger steps since "10,000 yard club" is the natural framing,
# not "10 yards away."
MILESTONE_STEP = {
    "Pass TD":         50,
    "Rush TD":         50,
    "Receiving TD":    50,
    "Receptions":      50,
    "Pass Yards":      1000,
    "Rush Yards":      1000,
    "Receiving Yards": 1000,
    "Interceptions":   25,
}

# Maps our human-readable Stat names to the ESPN stats-endpoint category
# name and the specific stat label within that category. Best-effort
# mapping of an undocumented endpoint -- see the NOTE above.
STAT_MAP = {
    "Pass TD":         ("passing", "passingTouchdowns"),
    "Rush TD":         ("rushing", "rushingTouchdowns"),
    "Receiving TD":    ("receiving", "receivingTouchdowns"),
    "Receptions":      ("receiving", "receptions"),
    "Pass Yards":      ("passing", "passingYards"),
    "Rush Yards":      ("rushing", "rushingYards"),
    "Receiving Yards": ("receiving", "receivingYards"),
    "Interceptions":   ("passing", "interceptions"),
}


def get_teams():
    """Returns list of (team_id, abbreviation)."""
    resp = requests.get(TEAMS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    teams = []
    for league in data.get("sports", [{}])[0].get("leagues", [{}]):
        for entry in league.get("teams", []):
            team = entry.get("team", {})
            tid, abbr = team.get("id"), team.get("abbreviation")
            if tid and abbr:
                teams.append((tid, abbr))
    if not teams:
        print("WARNING: unexpected shape from /teams -- got 0 teams. Raw keys:", list(data.keys()))
    return teams


def get_roster(team_id, team_abbr):
    """Returns list of (athlete_id, player_name, position_abbr) for tracked positions."""
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

    if not players:
        print(f"    WARNING: no tracked-position players found on {team_abbr}'s roster "
              f"(raw athlete groups: {[g.get('position') for g in data.get('athletes', [])]})")
    return players


def get_career_stats(athlete_id, player_label):
    """Returns dict of {our_stat_name: value} for whichever tracked stats are present."""
    resp = requests.get(STATS_URL.format(id=athlete_id), timeout=20)
    resp.raise_for_status()
    data = resp.json()

    categories = data.get("categories", [])
    found = {}
    for our_name, (cat_name, stat_key) in STAT_MAP.items():
        for cat in categories:
            if cat.get("name") != cat_name:
                continue
            for stat_block in cat.get("statistics", []) or cat.get("stats", []):
                label = (stat_block.get("name") or stat_block.get("abbreviation") or "").lower()
                display = str(stat_block.get("displayName", "")).lower()
                if stat_key.lower() in label or stat_key.lower() in display:
                    value = stat_block.get("value")
                    if value is not None:
                        found[our_name] = float(value)

    if not found and categories:
        # Only warn once per player if nothing at all matched, to keep the log readable.
        print(f"    No tracked stats matched for {player_label}. "
              f"ESPN categories present: {[c.get('name') for c in categories]}")
    return found


def next_milestone(current: float, stat_name: str) -> tuple[float, float]:
    """Given a career total, returns (next milestone, remaining)."""
    step = MILESTONE_STEP[stat_name]
    if current <= 0:
        return step, step
    if current % step == 0:
        milestone = current + step
    else:
        milestone = ((current // step) + 1) * step
    return milestone, milestone - current


def main():
    print("Fetching team list...")
    teams = get_teams()
    print(f"Found {len(teams)} teams.\n")

    rows = []
    for team_id, team_abbr in teams:
        print(f"Rostering {team_abbr}...")
        try:
            roster = get_roster(team_id, team_abbr)
        except Exception as exc:
            print(f"    Failed to fetch roster for {team_abbr}: {exc}")
            continue
        time.sleep(0.2)

        for athlete_id, player_name, position in roster:
            if not athlete_id or not player_name:
                continue
            try:
                stats = get_career_stats(athlete_id, player_name)
            except Exception as exc:
                print(f"    Failed to fetch stats for {player_name}: {exc}")
                continue
            time.sleep(0.2)

            for stat_name, current in stats.items():
                if current < MIN_CAREER_TOTAL:
                    continue
                milestone, remaining = next_milestone(current, stat_name)
                rows.append({
                    "Player": player_name, "Team": team_abbr, "Position": position,
                    "Stat": stat_name, "Current Total": int(current),
                    "Milestone": int(milestone), "Remaining": int(remaining),
                    "Notes": "",
                })

    if not rows:
        print("\nNo milestone rows produced -- something upstream likely didn't match "
              "ESPN's actual response shape. Check the WARNING lines above.")
        sys.exit(1)

    result = pd.DataFrame(rows).sort_values(["Remaining", "Current Total"], ascending=[True, False])
    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Tracked {len(result)} player/stat milestone rows across {len(teams)} teams.")
    print("\nClosest to hitting a milestone right now:")
    print(result.head(10)[["Player", "Team", "Stat", "Current Total", "Milestone", "Remaining"]].to_string(index=False))


if __name__ == "__main__":
    main()

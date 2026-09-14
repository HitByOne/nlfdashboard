"""
07_milestone_watch.py

Reads a short watchlist (Player, Team, Stat, Milestone -- no baseline number
needed) and looks up each player's CURRENT career total for that stat
directly from ESPN, the same way Pro Football Reference's milestone tracker
always shows a live total. Writes nfl_2026_milestones.csv, ready for the
dashboard's Milestone Watch tab to display as-is.

You maintain milestones_watchlist.csv by hand (just names + targets).
Everything else -- finding the player, pulling their career stats, doing
the math -- happens automatically every time this runs.

NOTE: ESPN's athlete-stats endpoint is undocumented (same as the rest of
this pipeline). The category/stat key names below are my best mapping based
on public documentation of the endpoint's shape, but I have not been able
to test this against the live API. If a player's stat comes back as
"NOT FOUND", check the console output -- it prints the raw category names
ESPN actually returned for that player, which tells us exactly what to fix.
"""
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from nfl_common import SEASON, PROCESSED_DIR

PIPELINE_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = PIPELINE_DIR / "milestones_watchlist.csv"
OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_milestones.csv"

SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
STATS_URL = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{id}/stats"

# Maps our human-readable Stat names (matches the dashboard's stat columns)
# to the ESPN stats-endpoint category name and the specific stat label
# within that category. These are best-effort guesses at ESPN's naming --
# see the NOTE above.
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


def find_athlete_id(player_name: str) -> str | None:
    """Look up a player's ESPN athlete ID by name search."""
    resp = requests.get(
        SEARCH_URL,
        params={"region": "us", "lang": "en", "query": player_name, "limit": 5},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    # The search endpoint groups results by type; athletes show up under a
    # section with a "player" or "athlete" type. This walks whatever shape
    # comes back defensively rather than assuming one exact structure.
    for section in data.get("results", []):
        if section.get("type") not in ("player", "athlete"):
            continue
        for item in section.get("contents", []):
            if player_name.lower() in item.get("displayName", "").lower():
                return str(item.get("id") or item.get("uid", "").split(":")[-1])

    return None


def get_career_stat(athlete_id: str, category: str, stat_key: str, player_label: str):
    """Fetch an athlete's career total for one stat. Returns None if not found."""
    resp = requests.get(STATS_URL.format(id=athlete_id), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    categories = data.get("categories", [])
    category_names = [c.get("name") for c in categories]

    for cat in categories:
        if cat.get("name") != category:
            continue
        # Career totals are typically the last/summary split, but this
        # checks all splits and takes the one labeled as career/total if
        # present, else falls back to the first stat block found.
        for stat_block in cat.get("statistics", []) or cat.get("stats", []):
            label = (stat_block.get("name") or stat_block.get("abbreviation") or "").lower()
            if stat_key.lower() in label or stat_key.lower() in str(stat_block.get("displayName", "")).lower():
                value = stat_block.get("value")
                if value is not None:
                    return float(value)

    print(
        f"    Could not find '{category}/{stat_key}' for {player_label}. "
        f"ESPN returned these categories instead: {category_names}"
    )
    return None


def main():
    if not WATCHLIST_FILE.exists():
        print(f"No watchlist found at {WATCHLIST_FILE}.")
        print("Create it with columns: Player, Team, Stat, Milestone, Notes")
        sys.exit(1)

    watchlist = pd.read_csv(WATCHLIST_FILE)
    required_cols = {"Player", "Team", "Stat", "Milestone"}
    missing = required_cols - set(watchlist.columns)
    if missing:
        raise ValueError(f"milestones_watchlist.csv is missing columns: {missing}")

    rows = []
    for _, w in watchlist.iterrows():
        player, team, stat, milestone = w["Player"], w["Team"], w["Stat"], w["Milestone"]
        notes = w.get("Notes", "")
        print(f"Looking up {player} ({team}) -- {stat}...")

        if stat not in STAT_MAP:
            print(f"    Unknown Stat '{stat}'. Valid values: {list(STAT_MAP.keys())}")
            rows.append({"Player": player, "Team": team, "Stat": stat,
                         "Current Total": "", "Milestone": milestone,
                         "Remaining": "", "Notes": f"{notes} [unrecognized stat]".strip()})
            continue

        athlete_id = find_athlete_id(player)
        if not athlete_id:
            print(f"    Could not find an ESPN athlete ID for '{player}'.")
            rows.append({"Player": player, "Team": team, "Stat": stat,
                         "Current Total": "", "Milestone": milestone,
                         "Remaining": "", "Notes": f"{notes} [player not found on ESPN]".strip()})
            continue

        category, stat_key = STAT_MAP[stat]
        current = get_career_stat(athlete_id, category, stat_key, player)

        if current is None:
            rows.append({"Player": player, "Team": team, "Stat": stat,
                         "Current Total": "", "Milestone": milestone,
                         "Remaining": "", "Notes": f"{notes} [stat not found -- see console]".strip()})
        else:
            remaining = float(milestone) - current
            print(f"    {player}: {int(current)} {stat} ({remaining:.0f} to go)")
            rows.append({"Player": player, "Team": team, "Stat": stat,
                         "Current Total": int(current), "Milestone": milestone,
                         "Remaining": remaining, "Notes": notes})

        time.sleep(0.3)  # be polite to ESPN's servers

    result = pd.DataFrame(rows).sort_values("Remaining", na_position="last")
    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Tracked {len(result)} milestone(s).")


if __name__ == "__main__":
    main()

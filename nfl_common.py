"""
nfl_common.py

Shared config + helpers for the ESPN pull pipeline.
Import this from every script instead of redefining PROJECT_ROOT /
PROCESSED_DIR / week-detection logic in each file.

    from nfl_common import SEASON, PROCESSED_DIR, get_completed_weeks, get_next_pull_week

Fixes two problems that break daily automation:
  1. `Path.cwd().parent` depends on the directory you launch from, which
     changes under cron / GitHub Actions. This uses the file's own location
     instead, so it's stable no matter how the script is invoked.
  2. Hardcoded WEEK constants require manual editing every week. The
     helpers here ask ESPN what week it actually is.
"""
from pathlib import Path

import requests

SEASON = 2026

# Anchor to this file's location, not the current working directory.
# nfl_common.py is expected to live in the same folder as the pull scripts.
PROJECT_ROOT = Path(__file__).resolve().parent
# CSVs live inside site/data so index.html (served from site/) can fetch
# them directly with a relative path -- no separate "build" step needed.
PROCESSED_DIR = PROJECT_ROOT / "site" / "data"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

PLAYER_FILE = PROCESSED_DIR / f"nfl_{SEASON}_player_game_stats.csv"
TEAM_WEEK_FILE = PROCESSED_DIR / f"nfl_{SEASON}_team_week_stats.csv"
TEAM_SUMMARY_FILE = PROCESSED_DIR / f"nfl_{SEASON}_team_summary.csv"
DVP_FILE = PROCESSED_DIR / f"nfl_{SEASON}_defense_vs_position.csv"
SOS_FILE = PROCESSED_DIR / f"nfl_{SEASON}_strength_of_schedule.csv"
# Stable filename -- no week number in it, so build_dashboard.py and the
# GitHub Actions workflow never need to change as the season progresses.
UPCOMING_MATCHUPS_FILE = PROCESSED_DIR / f"nfl_{SEASON}_upcoming_matchups.csv"


def is_week_complete(week: int, season: int = SEASON) -> bool:
    """
    True only if every game scheduled for this week has finished.

    Queries by week number specifically (not a date range) so a game dated
    after "today" -- e.g. a Monday night game when this runs on Sunday --
    is still counted. A date-range query would silently miss that game
    entirely and could report the week complete before it actually is.
    """
    url = f"{BASE_URL}/scoreboard?seasontype=2&week={week}&season={season}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    events = response.json().get("events", [])

    if not events:
        return False  # week isn't populated/scheduled yet

    return all(
        event.get("status", {}).get("type", {}).get("completed", False)
        for event in events
    )


def get_completed_weeks(season: int = SEASON) -> list[int]:
    """
    Ask ESPN which week numbers are FULLY complete (every game in that week
    finished), by checking week 1, 2, 3... in order and stopping at the
    first incomplete one. Used for informational purposes (e.g. picking a
    preview week); get_next_pull_week below does its own direct check
    rather than relying on this, since that one actually decides what gets
    permanently written to the data files.
    """
    completed = []
    week = 1
    while True:
        if not is_week_complete(week, season):
            break
        completed.append(week)
        week += 1
        if week > 25:  # safety valve, well past any realistic NFL week count
            break
    return completed


def get_next_pull_week(existing_player_csv: Path = PLAYER_FILE, season: int = SEASON) -> int | None:
    """
    Figure out which week the weekly player-stat pull should fetch next.

    Only returns a week once EVERY game in it has finished -- so a fast
    Thursday/Sunday slate doesn't get pulled (and locked in as "done") before
    a lagging Monday night game finishes. Returns None if the next week
    isn't fully done yet -- callers should treat that as "nothing to do
    today," not an error.
    """
    import pandas as pd

    if not existing_player_csv.exists():
        # No player file yet at all -> caller should run the initial pull instead.
        return None

    existing = pd.read_csv(existing_player_csv)
    next_week = 1 if existing.empty else int(pd.to_numeric(existing["Week"], errors="coerce").max()) + 1

    if is_week_complete(next_week, season):
        return next_week
    return None  # next week isn't fully finished yet


def get_preview_week(season: int = SEASON) -> int:
    """The next week to build an 'upcoming matchups' preview for."""
    completed = get_completed_weeks(season)
    return (max(completed) + 1) if completed else 1

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
from datetime import date
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


def get_completed_weeks(season: int = SEASON) -> list[int]:
    """Ask ESPN which week numbers currently have at least one completed game."""
    start_date = f"{season}0901"
    end_date = date.today().strftime("%Y%m%d")
    url = f"{BASE_URL}/scoreboard?limit=1000&dates={start_date}-{end_date}&seasontype=2"

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    events = response.json().get("events", [])

    weeks = set()
    for event in events:
        is_completed = event.get("status", {}).get("type", {}).get("completed", False)
        week_number = event.get("week", {}).get("number")
        if is_completed and week_number is not None:
            weeks.add(week_number)

    return sorted(weeks)


def get_next_pull_week(existing_player_csv: Path = PLAYER_FILE, season: int = SEASON) -> int | None:
    """
    Figure out which week the weekly player-stat pull should fetch next.

    Returns None if there's no new completed week to pull yet (e.g. it's
    Wednesday and this week's games haven't finished) -- callers should
    treat that as "nothing to do today," not an error.
    """
    import pandas as pd

    completed = get_completed_weeks(season)
    if not completed:
        return None
    latest_completed = max(completed)

    if not existing_player_csv.exists():
        # No player file yet at all -> caller should run the initial pull instead.
        return None

    existing = pd.read_csv(existing_player_csv)
    if existing.empty:
        return latest_completed

    max_week_in_csv = int(pd.to_numeric(existing["Week"], errors="coerce").max())

    if latest_completed > max_week_in_csv:
        return max_week_in_csv + 1
    return None  # already up to date


def get_preview_week(season: int = SEASON) -> int:
    """The next week to build an 'upcoming matchups' preview for."""
    completed = get_completed_weeks(season)
    return (max(completed) + 1) if completed else 1

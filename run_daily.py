#!/usr/bin/env python3
"""
run_daily.py

Runs the data pipeline in dependency order:

  1. Player stats   (initial pull if no CSV yet, otherwise weekly update)
  2. Team tables     (team_week_stats, team_summary)      <- needs player stats
  3. Strength of schedule                                  <- needs team_week
  4. Defense vs position                                    <- needs player stats
  5. Upcoming matchups                                      <- needs DvP + team_summary

There is no "build the dashboard" step. site/index.html fetches these CSVs
directly from site/data/ every time someone loads the page, so refreshing
the CSVs *is* refreshing the dashboard -- same mechanism for every week,
including week 1.

Schedule this with cron or a GitHub Actions workflow, e.g. daily at 6am:
    0 6 * * *  cd /path/to/pipeline && python3 run_daily.py
"""
import subprocess
import sys
from pathlib import Path

from nfl_common import PLAYER_FILE

PIPELINE_DIR = Path(__file__).resolve().parent


def run(script_name: str, critical: bool = True) -> None:
    script_path = PIPELINE_DIR / script_name
    print(f"\n=== Running {script_name} ===")
    result = subprocess.run([sys.executable, str(script_path)], cwd=PIPELINE_DIR)
    if result.returncode != 0:
        if critical:
            raise RuntimeError(f"{script_name} failed with exit code {result.returncode}")
        print(f"WARNING: {script_name} failed (exit {result.returncode}) -- continuing, "
              f"this step is not required for the core dashboard.")


def main():
    # Step 1: player stats. Initial pull only if we've never pulled before.
    if PLAYER_FILE.exists():
        run("02_weekly_player_update.py")
    else:
        run("01_initial_player_pull.py")

    # Steps 2-5: derived tables. Safe to re-run even if step 1 found
    # nothing new -- they just recompute the same values.
    run("03_build_team_tables.py")
    run("04_strength_of_schedule.py")
    run("05_defense_vs_position.py")
    run("06_matchup_preview.py")

    # Milestone Watch is a nice-to-have layered on top of everything else --
    # an empty watchlist or a flaky ESPN lookup shouldn't block the core
    # weekly refresh, so this one is non-critical.
    run("07_milestone_watch.py", critical=False)

    print("\nPipeline complete. site/data/*.csv is up to date --")
    print("reload the dashboard page to see the refresh.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"\nPIPELINE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

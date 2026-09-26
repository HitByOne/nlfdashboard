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
import argparse
import subprocess
import sys
from pathlib import Path

from nfl_common import PLAYER_FILE

PIPELINE_DIR = Path(__file__).resolve().parent


def run(script_name: str, critical: bool = True, extra_args: list[str] | None = None) -> None:
    script_path = PIPELINE_DIR / script_name
    cmd = [sys.executable, str(script_path)] + (extra_args or [])
    print(f"\n=== Running {script_name} {' '.join(extra_args or [])} ===")
    result = subprocess.run(cmd, cwd=PIPELINE_DIR)
    if result.returncode != 0:
        if critical:
            raise RuntimeError(f"{script_name} failed with exit code {result.returncode}")
        print(f"WARNING: {script_name} failed (exit {result.returncode}) -- continuing, "
              f"this step is not required for the core dashboard.")


def main(force_week: int | None = None):
    # Step 1: player stats.
    if force_week is not None:
        # Force-refresh a specific week regardless of what's already in the
        # CSV -- overwrites that week's rows with a fresh pull.
        run("02_weekly_player_update.py", extra_args=["--week", str(force_week)])
    elif PLAYER_FILE.exists():
        run("02_weekly_player_update.py")
    else:
        run("01_initial_player_pull.py")

    # Steps 2-5: derived tables. Safe to re-run even if step 1 found
    # nothing new -- they just recompute the same values.
    run("03_build_team_tables.py")
    run("04_strength_of_schedule.py")
    run("05_defense_vs_position.py")
    run("06_matchup_preview.py")

    # Injury report hits an unverified ESPN endpoint -- non-critical so a
    # bad response there can't block the core weekly refresh.
    run("08_injury_report.py", critical=False)

    # EPA/efficiency depends on an external nflverse download on top of
    # ESPN -- non-critical so it can't block the core weekly refresh.
    run("09_epa_efficiency.py", critical=False)

    # Per-game detailed stats (real scores, sacks, penalties, QB
    # performance) -- same external dependency as EPA, non-critical.
    run("10_game_stats.py", critical=False)

    # Red zone usage depends on the same external nflverse download as EPA
    # and game stats -- non-critical for the same reason.
    run("11_redzone_usage.py", critical=False)

    # Coaching tendencies depends on the same external nflverse download as
    # EPA, game stats, and red zone usage -- non-critical for the same reason.
    run("12_coaching_tendencies.py", critical=False)

    # Milestone Watch is a nice-to-have layered on top of everything else --
    # a flaky nflverse download shouldn't block the core weekly refresh, so
    # this one is non-critical.
    run("07_milestone_watch.py", critical=False)

    print("\nPipeline complete. site/data/*.csv is up to date --")
    print("reload the dashboard page to see the refresh.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument(
        "--week", type=int, default=None,
        help="Force-refresh this specific week's player stats instead of auto-detecting "
             "the next new one, then rebuild everything downstream from it.",
    )
    cli_args = cli.parse_args()

    try:
        main(force_week=cli_args.week)
    except RuntimeError as exc:
        print(f"\nPIPELINE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

"""
07_milestone_watch.py

TRUE CAREER milestone tracking, built from a legitimate, free, actively
maintained public dataset instead of scraping Pro Football Reference
(against their terms) or guessing at ESPN's undocumented per-athlete stats
endpoint (which didn't pan out -- see git history).

Source: nflverse-data (https://github.com/nflverse/nflverse-data), a
week-level player stats file spanning many NFL seasons, published
deliberately for exactly this kind of use as downloadable files -- not
scraped, no ToS conflict.

How it works:
  1. Download nflverse's player_stats file (one row per player per week).
  2. Sum every season STRICTLY BEFORE this app's current SEASON to get each
     player's career baseline entering this season.
  3. Add this season's totals from OUR OWN player_game_stats.csv (already
     reliably tracked via the ESPN pipeline) on top, for an up-to-date
     current total.
  4. Compute each player's next milestone (TDs/receptions in steps of 50,
     yardage in steps of 1000, interceptions in steps of 25 -- verified
     against Pro Football Reference's own milestone tracker earlier).

KNOWN LIMITATION: nflverse's data has, at various points, lagged behind by
close to a full season. If it hasn't yet published the season immediately
before SEASON, that season's production won't be counted in the baseline
-- there is no way to detect or backfill this automatically. It self-heals
the moment nflverse publishes that season; no code change needed here.
This script prints a clear note when that gap exists so it's never silent.
"""
import sys

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, PLAYER_FILE

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_milestones.csv"
NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.parquet"

TRACKED_POSITIONS = {"QB", "RB", "WR", "TE"}
MIN_CAREER_TOTAL = 5  # skip near-zero totals as noise

MILESTONE_STEP = {
    "Pass TD": 50, "Rush TD": 50, "Receiving TD": 50, "Receptions": 50,
    "Pass Yards": 1000, "Rush Yards": 1000, "Receiving Yards": 1000,
    "Interceptions": 25,
}

# Our stat name -> nflverse's column name.
NFLVERSE_STAT_MAP = {
    "Pass TD": "passing_tds",
    "Pass Yards": "passing_yards",
    "Interceptions": "interceptions",
    "Rush TD": "rushing_tds",
    "Rush Yards": "rushing_yards",
    "Receiving TD": "receiving_tds",
    "Receiving Yards": "receiving_yards",
    "Receptions": "receptions",
}


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
    print("Downloading nflverse player_stats (career history)...")
    nv = pd.read_parquet(NFLVERSE_URL)
    min_nv_season, max_nv_season = int(nv["season"].min()), int(nv["season"].max())
    print(f"nflverse data covers seasons {min_nv_season}-{max_nv_season}.")

    if max_nv_season < SEASON - 1:
        print(
            f"    NOTE: nflverse's most recent season ({max_nv_season}) is more than one "
            f"season behind this app's current season ({SEASON}). Career totals below will "
            f"be missing production from {max_nv_season + 1} through {SEASON - 1} until "
            f"nflverse publishes that data -- this is a known gap, not a bug here."
        )

    nv_career = nv[
        (nv["season"] < SEASON)
        & (nv["position"].isin(TRACKED_POSITIONS))
        & (nv["season_type"] == "REG")  # exclude playoffs -- career trackers (PFR included) report regular season only
    ]
    nv_cols = list(NFLVERSE_STAT_MAP.values())
    for col in nv_cols:
        nv_career[col] = pd.to_numeric(nv_career[col], errors="coerce").fillna(0)

    baseline = (
        nv_career.groupby("player_display_name")[nv_cols]
        .sum()
        .reset_index()
        .set_index("player_display_name")
    )

    if not PLAYER_FILE.exists():
        print(f"No current-season player file found at {PLAYER_FILE} -- run the main pipeline first.")
        sys.exit(1)

    current = pd.read_csv(PLAYER_FILE)
    current = current[current["Position"].isin(TRACKED_POSITIONS)].copy()

    our_stat_cols = list(NFLVERSE_STAT_MAP.keys())
    for col in our_stat_cols:
        current[col] = pd.to_numeric(current[col], errors="coerce").fillna(0)

    current_totals = (
        current.groupby(["Player", "Team", "Position"])[our_stat_cols]
        .sum()
        .reset_index()
    )

    rows = []
    unmatched = set()

    for _, row in current_totals.iterrows():
        player, team, position = row["Player"], row["Team"], row["Position"]

        if player in baseline.index:
            base_vals = baseline.loc[player]
        else:
            unmatched.add(player)
            base_vals = {c: 0 for c in nv_cols}

        for our_name, nv_col in NFLVERSE_STAT_MAP.items():
            season_total = row[our_name]
            career_total = float(base_vals[nv_col]) + float(season_total)
            if career_total < MIN_CAREER_TOTAL:
                continue
            milestone, remaining = next_milestone(career_total, our_name)
            rows.append({
                "Player": player, "Team": team, "Position": position, "Stat": our_name,
                "Current Total": int(career_total), "Milestone": int(milestone),
                "Remaining": int(remaining),
            })

    if unmatched:
        sample = sorted(unmatched)[:15]
        print(
            f"\n{len(unmatched)} current-season player(s) had no nflverse career match "
            f"(likely rookies with no prior-season history, or a name-format mismatch): "
            f"{sample}{'...' if len(unmatched) > 15 else ''}"
        )

    if not rows:
        print("\nNo milestone rows produced -- check that PLAYER_FILE has current data.")
        sys.exit(1)

    result = pd.DataFrame(rows).sort_values(["Remaining", "Current Total"], ascending=[True, False])
    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Tracked {len(result)} player/stat milestone rows.")
    print("\nClosest to hitting a milestone right now:")
    print(result.head(10)[["Player", "Team", "Stat", "Current Total", "Milestone", "Remaining"]].to_string(index=False))


if __name__ == "__main__":
    main()

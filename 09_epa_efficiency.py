"""
09_epa_efficiency.py

Team-level EPA (Expected Points Added per play) and success rate, computed
from nflverse's real play-by-play data for the CURRENT season -- verified
live to actually be current (weeks update within the season), unlike the
player_stats rollup file used elsewhere in this pipeline, which lags a
full season behind. Play-by-play is a separate, per-season nflverse
release that updates much more promptly.

EPA measures how much each individual play increased or decreased a
team's expected points, averaged per play. It's a materially better
indicator of true team quality than raw yards or points allowed, because
it accounts for down, distance, and field position context on every single
play instead of just game totals -- two teams can allow the same yards/game
with very different EPA if one is bending-not-breaking on early downs and
the other is getting embarrassed in the red zone.

Computes, per team, as BOTH the offense and the defense:
  - EPA/play, season-to-date and last 3 games
  - Pass EPA/play and Rush EPA/play splits
  - Success rate (the fraction of plays graded a "success" by down/distance,
    nflverse's own standard definition)
  - Games played -- EPA is genuinely noisy in a small sample. Two games of
    data is not a settled read on a team; treat anything under ~4 games
    with real caution. The dashboard surfaces this directly rather than
    hiding it.

Non-critical in run_daily.py -- if nflverse's PBP release has an issue,
the rest of the dashboard still refreshes normally.
"""
import sys

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_team_epa.csv"
PBP_URL = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{SEASON}.parquet"


def build_side(df: pd.DataFrame, team_col: str, last3_weeks: set) -> pd.DataFrame:
    rows = []
    for team, g in df.groupby(team_col):
        if not isinstance(team, str) or not team:
            continue

        g_last3 = g[g["week"].isin(last3_weeks)]
        g_pass = g[g["pass"] == 1]
        g_rush = g[g["rush"] == 1]

        rows.append({
            "Team": team,
            "Games Played": g["game_id"].nunique(),
            "EPA/Play": round(g["epa"].mean(), 3),
            "Success Rate": round(g["success"].mean(), 3),
            "Pass EPA/Play": round(g_pass["epa"].mean(), 3) if len(g_pass) else None,
            "Rush EPA/Play": round(g_rush["epa"].mean(), 3) if len(g_rush) else None,
            "Last3 EPA/Play": round(g_last3["epa"].mean(), 3) if len(g_last3) else None,
            "Last3 Success Rate": round(g_last3["success"].mean(), 3) if len(g_last3) else None,
        })
    return pd.DataFrame(rows)


def main():
    print(f"Downloading {SEASON} play-by-play data from nflverse...")
    try:
        pbp = pd.read_parquet(PBP_URL)
    except Exception as exc:
        print(f"Failed to download play-by-play data: {exc}")
        sys.exit(1)

    print(f"Raw plays: {len(pbp):,}")

    # Only real pass/run plays count toward EPA/success rate the way this
    # is normally computed -- excludes kneels, spikes, penalty-only plays.
    plays = pbp[((pbp["pass"] == 1) | (pbp["rush"] == 1)) & pbp["epa"].notna()].copy()
    print(f"Pass/rush plays with EPA: {len(plays):,}")

    if plays.empty:
        print("No usable plays found -- nothing to save.")
        sys.exit(1)

    max_week = int(plays["week"].max())
    last3_weeks = set(range(max(1, max_week - 2), max_week + 1))
    print(f"Current through week {max_week}. 'Last 3' covers weeks {sorted(last3_weeks)}.")

    offense = build_side(plays, "posteam", last3_weeks)
    offense["Side"] = "Offense"
    defense = build_side(plays, "defteam", last3_weeks)
    defense["Side"] = "Defense"

    result = pd.concat([offense, defense], ignore_index=True)
    result = result.sort_values(["Side", "EPA/Play"], ascending=[True, False])
    result.to_csv(OUTPUT_FILE, index=False)

    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Teams covered: {offense['Team'].nunique()}")
    print("\nTop 5 offenses by EPA/play:")
    print(offense.sort_values("EPA/Play", ascending=False).head(5)
          [["Team", "EPA/Play", "Success Rate", "Games Played"]].to_string(index=False))
    print("\nTop 5 stingiest defenses by EPA/play allowed (lower = better):")
    print(defense.sort_values("EPA/Play", ascending=True).head(5)
          [["Team", "EPA/Play", "Success Rate", "Games Played"]].to_string(index=False))


if __name__ == "__main__":
    main()

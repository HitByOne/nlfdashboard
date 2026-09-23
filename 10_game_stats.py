"""
10_game_stats.py

Detailed PER-GAME team stats and QB performance, computed directly from
nflverse's play-by-play data (the same source already used for EPA/
Efficiency). This adds things a season-aggregate view can't show: real
final scores, EPA broken down by pass/rush for one specific game, sacks,
penalties, turnovers, third-down conversions, and per-QB performance
(including a standard NFL passer rating computed from the box score).

Also worth noting: this has the REAL final score directly from play-by-play,
unlike strength_of_schedule.py elsewhere in this pipeline, which only ever
infers wins/losses from touchdown count. That's a known limitation flagged
in that script; this data could correct it in the future if wanted.

Outputs two files:
  nfl_{SEASON}_game_team_stats.csv -- one row per team per game
  nfl_{SEASON}_game_qb_stats.csv   -- one row per QB per game

Non-critical in run_daily.py -- if nflverse's PBP release has an issue,
the rest of the dashboard still refreshes normally.
"""
import sys

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, fix_nflverse_team_abbrs

TEAM_STATS_FILE = PROCESSED_DIR / f"nfl_{SEASON}_game_team_stats.csv"
QB_STATS_FILE = PROCESSED_DIR / f"nfl_{SEASON}_game_qb_stats.csv"
PBP_URL = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{SEASON}.parquet"


def passer_rating(cmp, att, yds, td, ints):
    """Standard NFL passer rating formula."""
    if att == 0:
        return 0.0
    a = max(0, min(2.375, (cmp / att - 0.3) * 5))
    b = max(0, min(2.375, (yds / att - 3) * 0.25))
    c = max(0, min(2.375, (td / att) * 20))
    d = max(0, min(2.375, 2.375 - (ints / att) * 25))
    return round((a + b + c + d) / 6 * 100, 1)


def main():
    print(f"Downloading {SEASON} play-by-play data...")
    try:
        pbp = pd.read_parquet(PBP_URL)
    except Exception as exc:
        print(f"Failed to download play-by-play data: {exc}")
        sys.exit(1)

    print(f"Raw plays: {len(pbp):,}")

    # Normalize nflverse's team abbreviations to this pipeline's standard
    # ones (WAS->WSH, LA->LAR) before any grouping/matching happens.
    for col in ["posteam", "defteam", "home_team", "away_team", "penalty_team"]:
        if col in pbp.columns:
            pbp[col] = fix_nflverse_team_abbrs(pbp[col])

    team_rows = []
    offense_plays = pbp[(pbp["pass_attempt"] == 1) | (pbp["rush_attempt"] == 1)]

    for (game_id, team), g in offense_plays.groupby(["game_id", "posteam"]):
        if not isinstance(team, str) or not team:
            continue

        full_game = pbp[pbp["game_id"] == game_id]
        first_row = full_game.iloc[0]
        is_home = first_row["home_team"] == team
        opponent = first_row["away_team"] if is_home else first_row["home_team"]

        last_play = full_game.iloc[-1]
        final_score = last_play["total_home_score"] if is_home else last_play["total_away_score"]
        opp_score = last_play["total_away_score"] if is_home else last_play["total_home_score"]

        pass_plays = g[g["pass_attempt"] == 1]
        rush_plays = g[g["rush_attempt"] == 1]
        sack_plays = full_game[(full_game["sack"] == 1) & (full_game["posteam"] == team)]
        pen_plays = full_game[full_game["penalty_team"] == team]
        third_down = g[g["down"] == 3]

        team_rows.append({
            "Game ID": game_id, "Week": int(g["week"].iloc[0]), "Team": team, "Opponent": opponent,
            "Home/Away": "Home" if is_home else "Away",
            "Final Score": int(final_score), "Opponent Score": int(opp_score),
            "Total Yards": int(g["yards_gained"].sum()), "Total Plays": int(len(g)),
            "EPA/Play": round(g["epa"].mean(), 3),
            "Pass Completions": int(pass_plays["complete_pass"].sum()), "Pass Attempts": int(len(pass_plays)),
            "Pass Yards": int(pass_plays["yards_gained"].sum()),
            "EPA/Pass": round(pass_plays["epa"].mean(), 3) if len(pass_plays) else None,
            "Rush Attempts": int(len(rush_plays)), "Rush Yards": int(rush_plays["yards_gained"].sum()),
            "EPA/Rush": round(rush_plays["epa"].mean(), 3) if len(rush_plays) else None,
            "First Downs": int(g["first_down"].sum()),
            "Third Down Conversions": int(third_down["third_down_converted"].sum()),
            "Third Down Attempts": int(third_down["third_down_converted"].sum() + third_down["third_down_failed"].sum()),
            "Interceptions Thrown": int(pass_plays["interception"].sum()),
            "Fumbles Lost": int(g["fumble_lost"].sum()),
            "Sacks Taken": int(len(sack_plays)), "Sack Yards": int(sack_plays["yards_gained"].sum()),
            "Sack EPA": round(sack_plays["epa"].sum(), 2) if len(sack_plays) else 0,
            "Penalties": int(len(pen_plays)), "Penalty Yards": int(pen_plays["penalty_yards"].sum()),
            "Penalty EPA": round(pen_plays["epa"].sum(), 2) if len(pen_plays) else 0,
        })

    qb_rows = []
    passing_plays = pbp[pbp["passer_player_name"].notna()]
    for (game_id, passer), g in passing_plays.groupby(["game_id", "passer_player_name"]):
        team = g["posteam"].iloc[0]
        rush_plays = pbp[(pbp["game_id"] == game_id) & (pbp["rusher_player_name"] == passer)]

        completions = int(g["complete_pass"].sum())
        attempts = int(len(g))
        pass_yards = int(g["yards_gained"].sum())
        pass_td = int(g["pass_touchdown"].sum())
        ints = int(g["interception"].sum())

        qb_rows.append({
            "Game ID": game_id, "Week": int(g["week"].iloc[0]), "Player": passer, "Team": team,
            "Completions": completions, "Attempts": attempts, "Pass Yards": pass_yards,
            "Pass TD": pass_td, "Interceptions": ints,
            "Rush Attempts": int(len(rush_plays)), "Rush Yards": int(rush_plays["yards_gained"].sum()),
            "Rush TD": int(rush_plays["rush_touchdown"].sum()) if len(rush_plays) else 0,
            "Total EPA": round(g["qb_epa"].sum(), 2),
            "EPA/Play": round(g["qb_epa"].mean(), 3),
            "Passer Rating": passer_rating(completions, attempts, pass_yards, pass_td, ints),
        })

    if not team_rows:
        print("No team rows produced -- nothing to save.")
        sys.exit(1)

    pd.DataFrame(team_rows).to_csv(TEAM_STATS_FILE, index=False)
    pd.DataFrame(qb_rows).to_csv(QB_STATS_FILE, index=False)
    print(f"\nSaved: {TEAM_STATS_FILE} ({len(team_rows)} rows)")
    print(f"Saved: {QB_STATS_FILE} ({len(qb_rows)} rows)")


if __name__ == "__main__":
    main()

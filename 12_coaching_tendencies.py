"""
12_coaching_tendencies.py

Team-level coaching tendency metrics, computed from nflverse's play-by-play
data (the same source already used for EPA, per-game stats, and red zone
usage):

  - Pass Rate Over Expectation (proxy): each team's pass rate in "neutral
    script" situations (1st-3rd quarter, game within one score, 1st or 2nd
    down -- i.e. not an obvious passing or clock-killing situation) minus
    the LEAGUE-WIDE average pass rate in those same situations. Positive =
    more pass-heavy than the league in neutral spots; negative = more
    run-heavy. This is a proxy for the real PROE metric (which needs a
    trained down/distance/score model this pipeline doesn't have) but
    captures the same idea: tendency independent of obvious game-script
    passing.
  - Red Zone Pass Rate: share of red zone offensive plays (yardline_100 <=
    20) that are a pass instead of a run.
  - 4th Down Go Rate: of all real 4th-down decisions (punt, field goal, or
    going for it -- excludes penalties/kneels/spikes, which aren't real
    decisions), the share where the team went for it instead of kicking.

Outputs: nfl_{SEASON}_coaching_tendencies.csv, one row per team.

Non-critical in run_daily.py -- if nflverse's PBP release has an issue,
the rest of the dashboard still refreshes normally.
"""
import sys

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, fix_nflverse_team_abbrs

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_coaching_tendencies.csv"
PBP_URL = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{SEASON}.parquet"


def main():
    print(f"Downloading {SEASON} play-by-play data...")
    try:
        pbp = pd.read_parquet(PBP_URL)
    except Exception as exc:
        print(f"Failed to download play-by-play data: {exc}")
        sys.exit(1)

    print(f"Raw plays: {len(pbp):,}")

    for col in ["posteam", "defteam"]:
        if col in pbp.columns:
            pbp[col] = fix_nflverse_team_abbrs(pbp[col])

    offense_plays = pbp[(pbp["pass_attempt"] == 1) | (pbp["rush_attempt"] == 1)].copy()

    # Neutral script: not fourth quarter (avoids garbage-time pass-heavy or
    # run-out-the-clock distortion), within one score, early downs (avoids
    # 3rd/4th down obligatory passing).
    neutral = offense_plays[
        (offense_plays["qtr"] <= 3)
        & (offense_plays["score_differential"].abs() <= 8)
        & (offense_plays["down"].isin([1, 2]))
    ]
    league_neutral_pass_rate = neutral["pass_attempt"].mean() if len(neutral) else None
    print(f"Neutral-script plays: {len(neutral):,}, league pass rate: {league_neutral_pass_rate}")

    redzone_plays = offense_plays[offense_plays["yardline_100"] <= 20]

    # Real 4th-down decisions only -- punt, field goal, or going for it.
    fourth_downs = pbp[(pbp["down"] == 4) & (pbp["play_type"].isin(["punt", "field_goal", "pass", "run"]))]

    rows = []
    teams = sorted(offense_plays["posteam"].dropna().unique())
    for team in teams:
        team_neutral = neutral[neutral["posteam"] == team]
        team_redzone = redzone_plays[redzone_plays["posteam"] == team]
        team_fourth = fourth_downs[fourth_downs["posteam"] == team]

        neutral_pass_rate = team_neutral["pass_attempt"].mean() if len(team_neutral) else None
        proe_proxy = (
            round(neutral_pass_rate - league_neutral_pass_rate, 3)
            if neutral_pass_rate is not None and league_neutral_pass_rate is not None
            else None
        )
        rz_pass_rate = round(team_redzone["pass_attempt"].mean(), 3) if len(team_redzone) else None
        fourth_go_rate = (
            round((team_fourth["play_type"].isin(["pass", "run"])).mean(), 3)
            if len(team_fourth) else None
        )

        rows.append({
            "Team": team,
            "Neutral Pass Rate": round(neutral_pass_rate, 3) if neutral_pass_rate is not None else None,
            "PROE Proxy": proe_proxy,
            "Red Zone Pass Rate": rz_pass_rate,
            "Red Zone Plays": len(team_redzone),
            "4th Down Go Rate": fourth_go_rate,
            "4th Down Decisions": len(team_fourth),
            "Neutral Script Plays": len(team_neutral),
        })

    if not rows:
        print("No team rows produced -- nothing to save.")
        sys.exit(1)

    result = pd.DataFrame(rows).sort_values("PROE Proxy", ascending=False)
    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"League neutral-script pass rate: {round(league_neutral_pass_rate, 3)}")
    print("\nMost pass-heavy (highest PROE proxy):")
    print(result.head(5)[["Team", "PROE Proxy", "Red Zone Pass Rate", "4th Down Go Rate"]].to_string(index=False))
    print("\nMost run-heavy (lowest PROE proxy):")
    print(result.tail(5)[["Team", "PROE Proxy", "Red Zone Pass Rate", "4th Down Go Rate"]].to_string(index=False))


if __name__ == "__main__":
    main()

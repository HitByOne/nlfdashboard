import pandas as pd

from nfl_common import PLAYER_FILE, TEAM_WEEK_FILE, TEAM_SUMMARY_FILE

if not PLAYER_FILE.exists():
    raise FileNotFoundError(
        f"Player CSV not found: {PLAYER_FILE}\n"
        "Run 01_initial_player_pull.py first."
    )

player_stats = pd.read_csv(PLAYER_FILE)

stat_columns = [
    "Pass Yards",
    "Pass TD",
    "Rush Attempts",
    "Rush Yards",
    "Rush TD",
    "Receptions",
    "Receiving Yards",
    "Receiving TD",
    "Targets",
    "PPR Points",
]

for column in stat_columns:
    if column not in player_stats.columns:
        player_stats[column] = 0

    player_stats[column] = pd.to_numeric(
        player_stats[column],
        errors="coerce",
    ).fillna(0)

team_week = (
    player_stats
    .groupby(
        ["Game ID", "Date", "Week", "Team", "Opponent"],
        as_index=False,
    )[stat_columns]
    .sum()
)

team_week["Offensive Yards"] = (
    team_week["Pass Yards"]
    + team_week["Rush Yards"]
)

team_week["Total TD Made"] = (
    team_week["Pass TD"]
    + team_week["Rush TD"]
)

opponent_totals = team_week[
    [
        "Game ID",
        "Team",
        "Pass Yards",
        "Pass TD",
        "Rush Attempts",
        "Rush Yards",
        "Rush TD",
        "Receptions",
        "Receiving Yards",
        "Receiving TD",
        "Targets",
        "Offensive Yards",
        "Total TD Made",
    ]
].rename(
    columns={
        "Team": "Opponent",
        "Pass Yards": "Pass Yards Allowed",
        "Pass TD": "Passing TD Allowed",
        "Rush Attempts": "Rush Attempts Allowed",
        "Rush Yards": "Rush Yards Allowed",
        "Rush TD": "Rushing TD Allowed",
        "Receptions": "Receptions Allowed",
        "Receiving Yards": "Receiving Yards Allowed",
        "Receiving TD": "Receiving TD Allowed",
        "Targets": "Targets Allowed",
        "Offensive Yards": "Offensive Yards Allowed",
        "Total TD Made": "Total TD Allowed",
    }
)

team_week = team_week.merge(
    opponent_totals,
    on=["Game ID", "Opponent"],
    how="left",
    validate="one_to_one",
)

team_week = (
    team_week
    .sort_values(["Week", "Team"])
    .reset_index(drop=True)
)

team_week.to_csv(TEAM_WEEK_FILE, index=False)

summary_columns = [
    "Pass Yards",
    "Pass TD",
    "Rush Attempts",
    "Rush Yards",
    "Rush TD",
    "Receptions",
    "Receiving Yards",
    "Receiving TD",
    "Targets",
    "PPR Points",
    "Offensive Yards",
    "Total TD Made",
    "Pass Yards Allowed",
    "Passing TD Allowed",
    "Rush Attempts Allowed",
    "Rush Yards Allowed",
    "Rushing TD Allowed",
    "Receptions Allowed",
    "Receiving Yards Allowed",
    "Receiving TD Allowed",
    "Targets Allowed",
    "Offensive Yards Allowed",
    "Total TD Allowed",
]

team_summary = (
    team_week
    .groupby("Team", as_index=False)
    .agg(
        **{
            "Games Played": ("Game ID", "nunique"),
            **{
                column: (column, "sum")
                for column in summary_columns
            },
        }
    )
)

for column in summary_columns:
    team_summary[f"{column} Per Game"] = (
        team_summary[column]
        / team_summary["Games Played"]
    ).round(2)

team_summary = (
    team_summary
    .sort_values(
        ["Total TD Made", "Offensive Yards"],
        ascending=[False, False],
    )
    .reset_index(drop=True)
)

team_summary.to_csv(TEAM_SUMMARY_FILE, index=False)

print("Clean team tables built.")
print(f"Player-game rows read: {len(player_stats):,}")
print(f"Team-game rows created: {len(team_week):,}")
print(f"Team summary rows created: {len(team_summary):,}")
print(f"\nSaved: {TEAM_WEEK_FILE}")
print(f"Saved: {TEAM_SUMMARY_FILE}")

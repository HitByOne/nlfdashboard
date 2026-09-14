import pandas as pd

from nfl_common import PLAYER_FILE, DVP_FILE

if not PLAYER_FILE.exists():
    raise FileNotFoundError(
        f"Player CSV not found: {PLAYER_FILE}\n"
        "Run 01_initial_player_pull.py first."
    )

player_stats = pd.read_csv(PLAYER_FILE)

required_columns = [
    "Game ID",
    "Week",
    "Team",
    "Opponent",
    "Player",
    "Position",
    "Pass Yards",
    "Pass TD",
    "Interceptions",
    "Rush Attempts",
    "Rush Yards",
    "Rush TD",
    "Receptions",
    "Receiving Yards",
    "Receiving TD",
    "Targets",
    "PPR Points",
]

missing_columns = [
    column
    for column in required_columns
    if column not in player_stats.columns
]

if missing_columns:
    raise ValueError(
        "Your player CSV is missing these required columns:\n"
        + "\n".join(missing_columns)
    )

player_stats = (
    player_stats
    .loc[
        player_stats["Position"].isin(
            ["QB", "RB", "WR", "TE"]
        )
    ]
    .copy()
)

numeric_columns = [
    "Pass Yards",
    "Pass TD",
    "Interceptions",
    "Rush Attempts",
    "Rush Yards",
    "Rush TD",
    "Receptions",
    "Receiving Yards",
    "Receiving TD",
    "Targets",
    "PPR Points",
]

for column in numeric_columns:
    player_stats[column] = pd.to_numeric(
        player_stats[column],
        errors="coerce",
    ).fillna(0)

player_stats["Pass Yards Allowed"] = player_stats["Pass Yards"]
player_stats["Pass TD Allowed"] = player_stats["Pass TD"]
player_stats["Interceptions Forced"] = player_stats["Interceptions"]

player_stats["Rush Attempts Allowed"] = (
    player_stats["Rush Attempts"]
)

player_stats["Rush Yards Allowed"] = player_stats["Rush Yards"]
player_stats["Rush TD Allowed"] = player_stats["Rush TD"]

player_stats["Targets Allowed"] = player_stats["Targets"]
player_stats["Receptions Allowed"] = player_stats["Receptions"]

player_stats["Receiving Yards Allowed"] = (
    player_stats["Receiving Yards"]
)

player_stats["Receiving TD Allowed"] = (
    player_stats["Receiving TD"]
)

player_stats["PPR Points Allowed"] = (
    player_stats["PPR Points"]
)

dvp_summary = (
    player_stats
    .groupby(
        ["Opponent", "Position"],
        as_index=False,
    )
    .agg(
        **{
            "Games Played": ("Game ID", "nunique"),
            "Players Faced": ("Player", "nunique"),
            "Pass Yards Allowed": ("Pass Yards Allowed", "sum"),
            "Pass TD Allowed": ("Pass TD Allowed", "sum"),
            "Interceptions Forced": (
                "Interceptions Forced",
                "sum",
            ),
            "Rush Attempts Allowed": (
                "Rush Attempts Allowed",
                "sum",
            ),
            "Rush Yards Allowed": ("Rush Yards Allowed", "sum"),
            "Rush TD Allowed": ("Rush TD Allowed", "sum"),
            "Targets Allowed": ("Targets Allowed", "sum"),
            "Receptions Allowed": (
                "Receptions Allowed",
                "sum",
            ),
            "Receiving Yards Allowed": (
                "Receiving Yards Allowed",
                "sum",
            ),
            "Receiving TD Allowed": (
                "Receiving TD Allowed",
                "sum",
            ),
            "PPR Points Allowed": (
                "PPR Points Allowed",
                "sum",
            ),
        }
    )
)

dvp_summary = dvp_summary.rename(
    columns={"Opponent": "Defense"}
)

total_columns = [
    "Pass Yards Allowed",
    "Pass TD Allowed",
    "Interceptions Forced",
    "Rush Attempts Allowed",
    "Rush Yards Allowed",
    "Rush TD Allowed",
    "Targets Allowed",
    "Receptions Allowed",
    "Receiving Yards Allowed",
    "Receiving TD Allowed",
    "PPR Points Allowed",
]

for column in total_columns:
    dvp_summary[f"{column} Per Game"] = (
        dvp_summary[column]
        / dvp_summary["Games Played"]
    ).round(2)

dvp_summary["PPR Points Allowed Rank"] = (
    dvp_summary
    .groupby("Position")["PPR Points Allowed Per Game"]
    .rank(
        method="min",
        ascending=False,
    )
    .astype(int)
)

dvp_summary["Matchup Label"] = (
    dvp_summary["Position"]
    .fillna("Unknown")
    .astype("string")
    .str.cat(
        dvp_summary["Defense"]
        .fillna("Unknown")
        .astype("string"),
        sep=" vs ",
    )
)

dvp_summary = (
    dvp_summary
    .sort_values(
        ["Position", "PPR Points Allowed Per Game"],
        ascending=[True, False],
    )
    .reset_index(drop=True)
)

dvp_summary.to_csv(DVP_FILE, index=False)

print("Defense-versus-position table built.")
print(f"Player-game rows analyzed: {len(player_stats):,}")
print(f"Defense-position rows created: {len(dvp_summary):,}")
print(f"Saved: {DVP_FILE}")

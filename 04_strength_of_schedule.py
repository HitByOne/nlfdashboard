import pandas as pd

from nfl_common import TEAM_WEEK_FILE, SOS_FILE

team_week = pd.read_csv(TEAM_WEEK_FILE)

required_columns = [
    "Game ID",
    "Week",
    "Team",
    "Opponent",
    "Total TD Made",
    "Total TD Allowed",
]

missing_columns = [
    column
    for column in required_columns
    if column not in team_week.columns
]

if missing_columns:
    raise ValueError(
        "Missing required columns in team-week CSV:\n"
        + "\n".join(missing_columns)
    )

team_week = (
    team_week
    .drop_duplicates(subset=["Game ID", "Team"])
    .copy()
)

team_week["Total TD Made"] = pd.to_numeric(
    team_week["Total TD Made"],
    errors="coerce",
).fillna(0)

team_week["Total TD Allowed"] = pd.to_numeric(
    team_week["Total TD Allowed"],
    errors="coerce",
).fillna(0)

# NOTE: Win/Loss here is inferred from touchdown count, not final score.
# This is a proxy, not the real game result (field goals/extra points can
# flip the actual outcome). Records and SOS below will occasionally
# disagree with real NFL standings. To fix properly, capture each
# competitor's actual `score` field from the ESPN scoreboard response in
# the pull scripts and thread it through to team_week, then compare score
# instead of TD count here.
team_week["Win"] = (
    team_week["Total TD Made"]
    > team_week["Total TD Allowed"]
).astype(int)

team_week["Loss"] = (
    team_week["Total TD Made"]
    < team_week["Total TD Allowed"]
).astype(int)

team_week["Tie"] = (
    team_week["Total TD Made"]
    == team_week["Total TD Allowed"]
).astype(int)

team_records = (
    team_week
    .groupby("Team", as_index=False)
    .agg(
        **{
            "Games Played": ("Game ID", "nunique"),
            "Wins": ("Win", "sum"),
            "Losses": ("Loss", "sum"),
            "Ties": ("Tie", "sum"),
            "Total TD Made": ("Total TD Made", "sum"),
            "Total TD Allowed": ("Total TD Allowed", "sum"),
        }
    )
)

team_records["Win Pct"] = (
    (
        team_records["Wins"]
        + team_records["Ties"] * 0.5
    )
    / team_records["Games Played"]
).round(3)

team_records["Record"] = (
    team_records["Wins"].astype(int).astype(str)
    + "-"
    + team_records["Losses"].astype(int).astype(str)
    + "-"
    + team_records["Ties"].astype(int).astype(str)
)

opponent_records = team_records.rename(
    columns={
        "Team": "Opponent",
        "Games Played": "Opponent Games Played",
        "Wins": "Opponent Wins",
        "Losses": "Opponent Losses",
        "Ties": "Opponent Ties",
        "Win Pct": "Opponent Win Pct",
        "Record": "Opponent Record",
        "Total TD Made": "Opponent Total TD Made",
        "Total TD Allowed": "Opponent Total TD Allowed",
    }
)

sos_detail = team_week[
    [
        "Game ID",
        "Date",
        "Week",
        "Team",
        "Opponent",
    ]
].merge(
    opponent_records,
    on="Opponent",
    how="left",
    validate="many_to_one",
)

sos_summary = (
    sos_detail
    .groupby("Team", as_index=False)
    .agg(
        **{
            "Games Played": ("Game ID", "nunique"),
            "Opponents Played": (
                "Opponent",
                lambda x: ", ".join(sorted(x.dropna().unique())),
            ),
            "Opponent Combined Wins": ("Opponent Wins", "sum"),
            "Opponent Combined Losses": ("Opponent Losses", "sum"),
            "Opponent Combined Ties": ("Opponent Ties", "sum"),
            "Played SOS": ("Opponent Win Pct", "mean"),
            "Opponent TD Made Per Game": (
                "Opponent Total TD Made",
                "mean",
            ),
            "Opponent TD Allowed Per Game": (
                "Opponent Total TD Allowed",
                "mean",
            ),
        }
    )
)

sos_summary["Opponent Combined Record"] = (
    sos_summary["Opponent Combined Wins"].astype(int).astype(str)
    + "-"
    + sos_summary["Opponent Combined Losses"].astype(int).astype(str)
    + "-"
    + sos_summary["Opponent Combined Ties"].astype(int).astype(str)
)

sos_summary["Played SOS"] = (
    sos_summary["Played SOS"]
    .fillna(0)
    .round(3)
)

sos_summary["Opponent TD Made Per Game"] = (
    sos_summary["Opponent TD Made Per Game"]
    / sos_summary["Games Played"]
).round(2)

sos_summary["Opponent TD Allowed Per Game"] = (
    sos_summary["Opponent TD Allowed Per Game"]
    / sos_summary["Games Played"]
).round(2)

sos_summary = (
    sos_summary
    .sort_values(
        "Played SOS",
        ascending=False,
    )
    .reset_index(drop=True)
)

sos_summary.insert(
    0,
    "Played SOS Rank",
    sos_summary.index + 1,
)

sos_summary.to_csv(SOS_FILE, index=False)

print("Strength-of-schedule table built.")
print(f"Teams summarized: {len(sos_summary):,}")
print(f"Saved: {SOS_FILE}")

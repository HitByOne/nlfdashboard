from time import sleep

import pandas as pd
import requests

from nfl_common import SEASON, PLAYER_FILE, BASE_URL
from datetime import date

START_DATE = f"{SEASON}0901"
END_DATE = date.today().strftime("%Y%m%d")

CSV_FILE = PLAYER_FILE

scoreboard_url = (
    f"{BASE_URL}/scoreboard"
    f"?limit=1000&dates={START_DATE}-{END_DATE}&seasontype=2"
)

response = requests.get(scoreboard_url, timeout=30)
response.raise_for_status()

events = response.json().get("events", [])

completed_events = [
    event
    for event in events
    if event.get("status", {})
    .get("type", {})
    .get("completed", False)
]

if not completed_events:
    raise ValueError(
        "No completed NFL games found in the selected date range."
    )

print(f"Completed games found: {len(completed_events)}")

all_rows = []

for event in completed_events:
    game_id = str(event["id"])
    game_date = event.get("date", "")[:10]
    week = event.get("week", {}).get("number")

    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])

    team_abbreviations = [
        competitor.get("team", {}).get("abbreviation")
        for competitor in competitors
    ]

    summary_url = f"{BASE_URL}/summary?event={game_id}"

    summary_response = requests.get(summary_url, timeout=30)
    summary_response.raise_for_status()

    boxscore = summary_response.json().get("boxscore", {})
    players_in_game = {}

    for team_block in boxscore.get("players", []):
        team = team_block.get("team", {}).get("abbreviation")

        opponent = next(
            (
                abbreviation
                for abbreviation in team_abbreviations
                if abbreviation != team
            ),
            None,
        )

        for category in team_block.get("statistics", []):
            stat_type = category.get("name", "").lower()

            if stat_type not in {"passing", "rushing", "receiving"}:
                continue

            labels = category.get("labels", [])

            for athlete_row in category.get("athletes", []):
                athlete = athlete_row.get("athlete", {})
                player_id = athlete.get("id")

                if not player_id:
                    continue

                player_key = (game_id, player_id)

                if player_key not in players_in_game:
                    players_in_game[player_key] = {
                        "Game ID": game_id,
                        "Date": game_date,
                        "Week": week,
                        "Team": team,
                        "Opponent": opponent,
                        "Player": athlete.get("displayName"),
                        "Player ID": player_id,
                        "Position": athlete.get(
                            "position",
                            {},
                        ).get("abbreviation"),
                        "Pass Completions": 0,
                        "Pass Attempts": 0,
                        "Pass Yards": 0,
                        "Pass TD": 0,
                        "Interceptions": 0,
                        "Rush Attempts": 0,
                        "Rush Yards": 0,
                        "Rush TD": 0,
                        "Receptions": 0,
                        "Receiving Yards": 0,
                        "Receiving TD": 0,
                        "Targets": 0,
                    }

                player = players_in_game[player_key]
                values = dict(
                    zip(labels, athlete_row.get("stats", []))
                )

                if stat_type == "passing":
                    comp_att = values.get("C/ATT", "0/0").split("/")

                    player["Pass Completions"] = pd.to_numeric(
                        comp_att[0],
                        errors="coerce",
                    ) or 0

                    player["Pass Attempts"] = pd.to_numeric(
                        comp_att[1]
                        if len(comp_att) > 1
                        else 0,
                        errors="coerce",
                    ) or 0

                    player["Pass Yards"] = pd.to_numeric(
                        values.get("YDS", 0),
                        errors="coerce",
                    ) or 0

                    player["Pass TD"] = pd.to_numeric(
                        values.get("TD", 0),
                        errors="coerce",
                    ) or 0

                    player["Interceptions"] = pd.to_numeric(
                        values.get("INT", 0),
                        errors="coerce",
                    ) or 0

                elif stat_type == "rushing":
                    player["Rush Attempts"] = pd.to_numeric(
                        values.get("CAR", 0),
                        errors="coerce",
                    ) or 0

                    player["Rush Yards"] = pd.to_numeric(
                        values.get("YDS", 0),
                        errors="coerce",
                    ) or 0

                    player["Rush TD"] = pd.to_numeric(
                        values.get("TD", 0),
                        errors="coerce",
                    ) or 0

                elif stat_type == "receiving":
                    player["Receptions"] = pd.to_numeric(
                        values.get("REC", 0),
                        errors="coerce",
                    ) or 0

                    player["Receiving Yards"] = pd.to_numeric(
                        values.get("YDS", 0),
                        errors="coerce",
                    ) or 0

                    player["Receiving TD"] = pd.to_numeric(
                        values.get("TD", 0),
                        errors="coerce",
                    ) or 0

                    player["Targets"] = pd.to_numeric(
                        values.get("TGTS", 0),
                        errors="coerce",
                    ) or 0

    all_rows.extend(players_in_game.values())

    print(f"Loaded: {event.get('name', game_id)}")
    sleep(0.2)

player_stats = pd.DataFrame(all_rows)

if player_stats.empty:
    raise ValueError(
        "No offensive player stats were returned from ESPN."
    )

numeric_columns = [
    "Pass Completions",
    "Pass Attempts",
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
]

for column in numeric_columns:
    player_stats[column] = pd.to_numeric(
        player_stats[column],
        errors="coerce",
    ).fillna(0)

player_stats["PPR Points"] = (
    player_stats["Pass Yards"] * 0.04
    + player_stats["Pass TD"] * 4
    - player_stats["Interceptions"] * 2
    + player_stats["Rush Yards"] * 0.10
    + player_stats["Rush TD"] * 6
    + player_stats["Receptions"]
    + player_stats["Receiving Yards"] * 0.10
    + player_stats["Receiving TD"] * 6
).round(2)

player_stats = (
    player_stats
    .drop_duplicates(
        subset=["Game ID", "Player ID"],
        keep="last",
    )
    .sort_values(["Week", "Team", "Player"])
    .reset_index(drop=True)
)

player_stats.to_csv(CSV_FILE, index=False)

print("\nInitial player pull complete.")
print(f"Player-game rows: {len(player_stats):,}")
print(f"Saved: {CSV_FILE}")

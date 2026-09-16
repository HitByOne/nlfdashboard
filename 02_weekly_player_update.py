import argparse
import sys
from time import sleep

import pandas as pd
import requests

from nfl_common import SEASON, PLAYER_FILE, BASE_URL, get_next_pull_week

CSV_FILE = PLAYER_FILE

parser = argparse.ArgumentParser()
parser.add_argument(
    "--week", type=int, default=None,
    help="Force-refresh this specific week (overwrites any existing rows for it), "
         "instead of auto-detecting the next new week. Use this to re-pull a week "
         "whose stats ESPN corrected after the fact, or to redo a week that was "
         "pulled incorrectly the first time.",
)
args = parser.parse_args()

if args.week is not None:
    WEEK_TO_ADD = args.week
    print(f"Forcing refresh of Week {WEEK_TO_ADD} (overriding auto-detection)...")
else:
    # Ask ESPN what the next not-yet-pulled completed week is, instead of a
    # hardcoded WEEK_TO_ADD. Returns None if there's nothing new to pull yet
    # (e.g. this week's games haven't finished) -- that's not an error for a
    # daily job, just "nothing to do today."
    WEEK_TO_ADD = get_next_pull_week(CSV_FILE, SEASON)
    if WEEK_TO_ADD is None:
        print("No new completed week to pull yet. Nothing to do.")
        sys.exit(0)
    print(f"Pulling Week {WEEK_TO_ADD}...")

existing_stats = pd.read_csv(CSV_FILE)

scoreboard_url = (
    f"{BASE_URL}/scoreboard"
    f"?limit=1000&dates={SEASON}&seasontype=2&week={WEEK_TO_ADD}"
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
        f"No completed games found for {SEASON} Week {WEEK_TO_ADD}."
    )

new_rows = []

for event in completed_events:
    game_id = str(event["id"])
    game_date = event.get("date", "")[:10]

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
                        "Week": WEEK_TO_ADD,
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

    new_rows.extend(players_in_game.values())
    print(f"Loaded: {event.get('name', game_id)}")
    sleep(0.2)

new_week_stats = pd.DataFrame(new_rows)

if new_week_stats.empty:
    raise ValueError(
        "No player-stat rows were returned for the completed games."
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
    new_week_stats[column] = pd.to_numeric(
        new_week_stats[column],
        errors="coerce",
    ).fillna(0)

new_week_stats["PPR Points"] = (
    new_week_stats["Pass Yards"] * 0.04
    + new_week_stats["Pass TD"] * 4
    - new_week_stats["Interceptions"] * 2
    + new_week_stats["Rush Yards"] * 0.10
    + new_week_stats["Rush TD"] * 6
    + new_week_stats["Receptions"]
    + new_week_stats["Receiving Yards"] * 0.10
    + new_week_stats["Receiving TD"] * 6
).round(2)

existing_stats = existing_stats[
    pd.to_numeric(
        existing_stats["Week"],
        errors="coerce",
    ) != WEEK_TO_ADD
].copy()

player_stats = pd.concat(
    [existing_stats, new_week_stats],
    ignore_index=True,
)

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

print("\nWeekly player update complete.")
print(f"Week {WEEK_TO_ADD} rows added: {len(new_week_stats):,}")
print(f"Total player-game rows: {len(player_stats):,}")
print(f"Saved: {CSV_FILE}")

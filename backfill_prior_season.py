"""
backfill_prior_season.py

One-time (or occasional) backfill of a full prior season's player stats,
using your own ESPN pipeline instead of waiting on nflverse's data to catch
up. This exists specifically to close the gap 07_milestone_watch.py flags:
nflverse's public dataset currently stops at 2024, so career totals miss
whatever happened in SEASON - 1 (e.g. 2025) until nflverse publishes it.

This pulls every completed game across all weeks of SEASON - 1 directly
from ESPN (same extraction logic as 01/02, kept self-contained here rather
than shared, so this backfill can't accidentally destabilize the daily
pipeline scripts), aggregates to one row per player for that whole season,
and saves it as nfl_{SEASON-1}_player_season_totals.csv.

07_milestone_watch.py automatically picks this file up if present and adds
it into the career baseline alongside nflverse's data.

This is NOT part of the daily run_daily.py pipeline -- it's meant to be
triggered manually, once, since a completed season's stats don't change.
Re-running it is harmless (just re-downloads and overwrites the same
totals) if you ever want to refresh it.

Usage:
    python3 backfill_prior_season.py             # backfills SEASON - 1
    python3 backfill_prior_season.py --season 2025
"""
import argparse
import sys
import time

import pandas as pd
import requests

from nfl_common import SEASON, PROCESSED_DIR, BASE_URL, build_player_position_map

TRACKED_POSITIONS = {"QB", "RB", "WR", "TE"}
MAX_WEEKS = 18  # standard NFL regular-season length


def to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_event_player_rows(event: dict, position_map: dict) -> list[dict]:
    """Same extraction logic as 01/02 (kept self-contained here on purpose)."""
    game_id = str(event["id"])
    week = event.get("week", {}).get("number")

    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])
    team_abbreviations = [c.get("team", {}).get("abbreviation") for c in competitors]

    summary_url = f"{BASE_URL}/summary?event={game_id}"
    resp = requests.get(summary_url, timeout=30)
    resp.raise_for_status()
    boxscore = resp.json().get("boxscore", {})

    players_in_game = {}
    for team_block in boxscore.get("players", []):
        team = team_block.get("team", {}).get("abbreviation")
        opponent = next((a for a in team_abbreviations if a != team), None)

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
                key = (game_id, player_id)
                if key not in players_in_game:
                    players_in_game[key] = {
                        "Week": week, "Team": team, "Opponent": opponent,
                        "Player": athlete.get("displayName"), "Player ID": player_id,
                        "Position": position_map.get(str(player_id), ""),
                        "Pass Yards": 0, "Pass TD": 0, "Interceptions": 0,
                        "Rush Yards": 0, "Rush TD": 0,
                        "Receptions": 0, "Receiving Yards": 0, "Receiving TD": 0,
                    }
                player = players_in_game[key]
                values = dict(zip(labels, athlete_row.get("stats", [])))
                if stat_type == "passing":
                    player["Pass Yards"] = to_num(values.get("YDS", 0))
                    player["Pass TD"] = to_num(values.get("TD", 0))
                    player["Interceptions"] = to_num(values.get("INT", 0))
                elif stat_type == "rushing":
                    player["Rush Yards"] = to_num(values.get("YDS", 0))
                    player["Rush TD"] = to_num(values.get("TD", 0))
                elif stat_type == "receiving":
                    player["Receptions"] = to_num(values.get("REC", 0))
                    player["Receiving Yards"] = to_num(values.get("YDS", 0))
                    player["Receiving TD"] = to_num(values.get("TD", 0))

    return list(players_in_game.values())


def main(season: int):
    output_file = PROCESSED_DIR / f"nfl_{season}_player_season_totals.csv"

    print(f"Building player-position lookup from current team rosters...")
    position_map = build_player_position_map(SEASON)  # position rarely changes; today's rosters are fine

    all_rows = []
    for week in range(1, MAX_WEEKS + 1):
        print(f"\nWeek {week}...")
        url = f"{BASE_URL}/scoreboard?seasontype=2&week={week}&season={season}"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            print(f"    Failed to fetch Week {week} schedule: {exc}")
            continue

        events = resp.json().get("events", [])
        completed = [e for e in events if e.get("status", {}).get("type", {}).get("completed", False)]
        if not completed:
            print(f"    No completed games found for Week {week} -- skipping.")
            continue

        for event in completed:
            try:
                rows = parse_event_player_rows(event, position_map)
                all_rows.extend(rows)
                print(f"    Loaded: {event.get('name', event.get('id'))}")
            except Exception as exc:
                print(f"    Failed to parse {event.get('name', event.get('id'))}: {exc}")
            time.sleep(0.2)

    if not all_rows:
        print("\nNo player rows collected -- nothing to save.")
        sys.exit(1)

    df = pd.DataFrame(all_rows)
    df = df[df["Position"].isin(TRACKED_POSITIONS)].copy()

    stat_cols = ["Pass Yards", "Pass TD", "Interceptions", "Rush Yards", "Rush TD",
                 "Receptions", "Receiving Yards", "Receiving TD"]
    for col in stat_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # One row per player for the whole season -- Team is whichever team they
    # most recently appeared with, for players who changed teams mid-season.
    season_totals = (
        df.sort_values("Week")
        .groupby("Player", as_index=False)
        .agg({
            "Team": "last", "Position": "last",
            **{col: "sum" for col in stat_cols},
        })
    )

    season_totals.to_csv(output_file, index=False)
    print(f"\nSaved: {output_file}")
    print(f"Season {season} totals for {len(season_totals)} players.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=SEASON - 1,
                         help="Which season to backfill (defaults to the season just before this app's current SEASON).")
    args = parser.parse_args()
    main(args.season)

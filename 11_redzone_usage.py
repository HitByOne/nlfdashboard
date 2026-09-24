"""
11_redzone_usage.py

Player-level red zone usage (targets and rush attempts inside the
opponent's 20-yard line), computed from nflverse's play-by-play data --
the same source already used for EPA/Efficiency and per-game team stats.

Red zone player usage is a straightforward play-level filter (yardline_100
<= 20), unlike TEAM-level red zone efficiency, which needs harder
drive-level logic (a scored trip vs an unscored one) -- that's why this
pipeline has never attempted a team-level red zone % anywhere else.

IMPORTANT name-matching note: nflverse's play-by-play data identifies
players by an abbreviated name (e.g. "D.Prescott"), not the full name
("Dak Prescott") this pipeline uses everywhere else -- and nflverse adds a
longer disambiguating prefix when two players share a last name (e.g.
"Bi.Robinson" vs "Br.Robinson" for Bijan vs Brian Robinson). This script
builds a Team + Last-Name crosswalk from this pipeline's OWN
player_game_stats.csv (already using full names) to convert nflverse's
abbreviated names to the same full names used across the rest of the
dashboard, so red zone data joins cleanly with everything else. Falls back
to the abbreviated name if no confident match is found -- still usable,
just not converted to the full name.

Outputs: nfl_{SEASON}_redzone_usage.csv, one row per player with:
  RZ Targets, RZ Target Share (of team total), RZ Receptions, RZ Rec TD,
  RZ Rush Attempts, RZ Rush Share (of team total), RZ Rush TD

Non-critical in run_daily.py -- if nflverse's PBP release has an issue,
the rest of the dashboard still refreshes normally.
"""
import sys

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, PLAYER_FILE, fix_nflverse_team_abbrs

OUTPUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_redzone_usage.csv"
PBP_URL = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{SEASON}.parquet"

SUFFIXES = {"jr.", "sr.", "ii", "iii", "iv"}


def build_name_crosswalk() -> dict:
    """(Team, last_name_lowercase) -> full name, from our own player data."""
    if not PLAYER_FILE.exists():
        print(f"No current-season player file found at {PLAYER_FILE} -- names will stay abbreviated.")
        return {}

    df = pd.read_csv(PLAYER_FILE)
    crosswalk = {}
    for _, row in df[["Player", "Team"]].drop_duplicates().iterrows():
        full_name = row["Player"]
        team = row["Team"]
        if not isinstance(full_name, str) or not full_name.strip():
            continue
        parts = full_name.split()
        last_name = parts[-1]
        if last_name.lower().rstrip(".") in SUFFIXES and len(parts) > 1:
            last_name = parts[-2]
        crosswalk[(team, last_name.lower())] = full_name
    return crosswalk


def resolve_name(abbrev_name: str, team: str, crosswalk: dict) -> str:
    """'D.Prescott' + team -> 'Dak Prescott', via last-name + team match."""
    if not isinstance(abbrev_name, str) or "." not in abbrev_name:
        return abbrev_name
    last_name = abbrev_name.split(".", 1)[1]
    return crosswalk.get((team, last_name.lower()), abbrev_name)


def main():
    print(f"Downloading {SEASON} play-by-play data...")
    try:
        pbp = pd.read_parquet(PBP_URL)
    except Exception as exc:
        print(f"Failed to download play-by-play data: {exc}")
        sys.exit(1)

    for col in ["posteam", "defteam"]:
        if col in pbp.columns:
            pbp[col] = fix_nflverse_team_abbrs(pbp[col])

    crosswalk = build_name_crosswalk()
    print(f"Built name crosswalk with {len(crosswalk)} entries.")

    redzone = pbp[pbp["yardline_100"] <= 20].copy()
    print(f"Red zone plays: {len(redzone):,}")
    if redzone.empty:
        print("No red zone plays found -- nothing to save.")
        sys.exit(1)

    # Team-level red zone totals, for share calculations.
    team_rz_targets = redzone[redzone["pass_attempt"] == 1].groupby("posteam").size().to_dict()
    team_rz_rushes = redzone[redzone["rush_attempt"] == 1].groupby("posteam").size().to_dict()

    rows = {}

    def get_or_init(full_name, team):
        key = (full_name, team)
        if key not in rows:
            rows[key] = {
                "Player": full_name, "Team": team,
                "RZ Targets": 0, "RZ Receptions": 0, "RZ Rec TD": 0,
                "RZ Rush Attempts": 0, "RZ Rush TD": 0,
            }
        return rows[key]

    rec_plays = redzone[redzone["receiver_player_name"].notna()]
    for (name, team), g in rec_plays.groupby(["receiver_player_name", "posteam"]):
        row = get_or_init(resolve_name(name, team, crosswalk), team)
        row["RZ Targets"] += len(g)
        row["RZ Receptions"] += int(g["complete_pass"].sum())
        row["RZ Rec TD"] += int(g["pass_touchdown"].sum())

    rush_plays = redzone[redzone["rusher_player_name"].notna()]
    for (name, team), g in rush_plays.groupby(["rusher_player_name", "posteam"]):
        row = get_or_init(resolve_name(name, team, crosswalk), team)
        row["RZ Rush Attempts"] += len(g)
        row["RZ Rush TD"] += int(g["rush_touchdown"].sum())

    for row in rows.values():
        team = row["Team"]
        team_targets = team_rz_targets.get(team, 0)
        team_rushes = team_rz_rushes.get(team, 0)
        row["RZ Target Share"] = round(row["RZ Targets"] / team_targets, 3) if team_targets else 0
        row["RZ Rush Share"] = round(row["RZ Rush Attempts"] / team_rushes, 3) if team_rushes else 0

    result = pd.DataFrame(rows.values()).sort_values("RZ Targets", ascending=False)
    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Tracked {len(result)} players.")
    print("\nTop 10 by red zone targets:")
    print(result.head(10)[["Player", "Team", "RZ Targets", "RZ Target Share", "RZ Rush Attempts", "RZ Rush Share"]].to_string(index=False))


if __name__ == "__main__":
    main()

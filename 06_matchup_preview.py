import pandas as pd
import requests

from nfl_common import (
    SEASON,
    BASE_URL,
    DVP_FILE,
    TEAM_SUMMARY_FILE,
    UPCOMING_MATCHUPS_FILE,
    get_preview_week,
)

# Dynamic instead of hardcoded -- always previews whichever week is next,
# so this script (and its output filename) never need manual edits again.
WEEK = get_preview_week(SEASON)
OUTPUT_FILE = UPCOMING_MATCHUPS_FILE  # stable name, no week number in it

_odds_debug_logged = False


def extract_odds(comp: dict) -> dict:
    """
    Best-effort extraction of betting odds from a competition's 'odds' list,
    present whenever a sportsbook has posted a line for that game. This is
    an undocumented part of ESPN's API, same caveat as the rest of this
    pipeline -- if odds are present but nothing recognizable comes out, the
    raw shape is printed once so the extraction can be fixed with certainty
    instead of guessed at.
    """
    global _odds_debug_logged
    odds_list = comp.get("odds") or []
    if not odds_list:
        return {}

    o = odds_list[0]  # first/featured provider's line
    result = {}
    if o.get("details"):
        result["Spread"] = o.get("details")
    if o.get("overUnder") is not None:
        result["Over/Under"] = o.get("overUnder")
    provider_name = (o.get("provider") or {}).get("name")
    if provider_name:
        result["Odds Provider"] = provider_name
    home_ml = (o.get("homeTeamOdds") or {}).get("moneyLine")
    away_ml = (o.get("awayTeamOdds") or {}).get("moneyLine")
    if home_ml is not None:
        result["Home Moneyline"] = home_ml
    if away_ml is not None:
        result["Away Moneyline"] = away_ml

    if not result and not _odds_debug_logged:
        print(f"    DEBUG: odds present but nothing recognized. Raw odds[0]: {o}")
        _odds_debug_logged = True

    return result


# Step 1: Fetch the schedule from ESPN
print(f"Fetching Week {WEEK} schedule from ESPN...")

schedule_url = (
    f"{BASE_URL}/scoreboard"
    f"?seasontype=2&week={WEEK}&season={SEASON}"
)

response = requests.get(schedule_url, timeout=15)
response.raise_for_status()
schedule_data = response.json()

events = schedule_data.get("events", [])

matchups = []
games_with_odds = 0

for event in events:
    competitions = event.get("competitions", [])

    if not competitions:
        continue

    comp = competitions[0]
    competitors = comp.get("competitors", [])
    game_id = event.get("id")
    game_date = event.get("date")

    home_team = None
    away_team = None

    for team in competitors:
        team_abbr = team.get("team", {}).get("abbreviation")
        is_home = team.get("homeAway") == "home"

        if is_home:
            home_team = team_abbr
        else:
            away_team = team_abbr

    odds = extract_odds(comp)
    if odds:
        games_with_odds += 1

    if home_team and away_team:
        matchups.append(
            {
                "Game ID": game_id,
                "Date": game_date,
                "Week": WEEK,
                "Away Team": away_team,
                "Home Team": home_team,
                "Spread": odds.get("Spread", ""),
                "Over/Under": odds.get("Over/Under", ""),
                "Odds Provider": odds.get("Odds Provider", ""),
                "Home Moneyline": odds.get("Home Moneyline", ""),
                "Away Moneyline": odds.get("Away Moneyline", ""),
            }
        )

schedule_df = pd.DataFrame(matchups)

print(f"Games found: {len(schedule_df)}")
print(f"Games with betting odds posted: {games_with_odds}")

if schedule_df.empty:
    raise ValueError(
        f"No games found on ESPN's schedule for Week {WEEK} -- "
        "schedule may not be posted yet."
    )

# Build team -> opponent mapping (each team's next opponent). Spread and
# Over/Under are shared per game; moneyline is specific to each team's side.
away_rows = schedule_df.rename(
    columns={
        "Away Team": "Team",
        "Home Team": "Opponent",
        "Away Moneyline": "Team Moneyline",
    }
).drop(columns=["Home Moneyline"])[
    ["Game ID", "Date", "Week", "Team", "Opponent", "Spread", "Over/Under", "Odds Provider", "Team Moneyline"]
]
away_rows["Home/Away"] = "Away"

home_rows = schedule_df.rename(
    columns={
        "Home Team": "Team",
        "Away Team": "Opponent",
        "Home Moneyline": "Team Moneyline",
    }
).drop(columns=["Away Moneyline"])[
    ["Game ID", "Date", "Week", "Team", "Opponent", "Spread", "Over/Under", "Odds Provider", "Team Moneyline"]
]
home_rows["Home/Away"] = "Home"

team_matchups = pd.concat(
    [away_rows, home_rows], ignore_index=True
)

team_matchups = team_matchups.sort_values(
    ["Date", "Team"]
).reset_index(drop=True)

# Step 2: Load and pivot DvP data
print("\nLoading defense-vs-position data...")

dvp = pd.read_csv(DVP_FILE)

dvp_wide = dvp.pivot_table(
    index="Defense",
    columns="Position",
    values=[
        "PPR Points Allowed Per Game",
        "Pass Yards Allowed Per Game",
        "Pass TD Allowed Per Game",
        "Rush Yards Allowed Per Game",
        "Rush TD Allowed Per Game",
        "Targets Allowed Per Game",
        "Receptions Allowed Per Game",
        "Receiving Yards Allowed Per Game",
        "Receiving TD Allowed Per Game",
    ],
    aggfunc="first",
)

dvp_wide.columns = [
    f"{stat} vs {pos}" for stat, pos in dvp_wide.columns
]

dvp_wide = dvp_wide.reset_index()

print(f"Defenses in DvP table: {len(dvp_wide)}")

# Step 3: Merge matchups with DvP data
print("\nBuilding matchup table...")

matchup_table = team_matchups.merge(
    dvp_wide,
    left_on="Opponent",
    right_on="Defense",
    how="left",
)

matchup_table = matchup_table.drop(columns=["Defense"])

# Step 4: Load team summary for offensive context
print("Loading team offensive stats...")

team_summary = pd.read_csv(TEAM_SUMMARY_FILE)

offense_stats = team_summary[
    [
        "Team",
        "Games Played",
        "Pass Yards Per Game",
        "Pass TD Per Game",
        "Rush Yards Per Game",
        "Rush TD Per Game",
        "PPR Points Per Game",
        "Offensive Yards Per Game",
    ]
].rename(
    columns={
        "Pass Yards Per Game": "Team Pass Yds/G",
        "Pass TD Per Game": "Team Pass TD/G",
        "Rush Yards Per Game": "Team Rush Yds/G",
        "Rush TD Per Game": "Team Rush TD/G",
        "PPR Points Per Game": "Team PPR/G",
        "Offensive Yards Per Game": "Team Off Yds/G",
    }
)

matchup_table = matchup_table.merge(
    offense_stats,
    on="Team",
    how="left",
)

# Step 5: Sort and save
matchup_table = matchup_table.sort_values(
    "Date"
).reset_index(drop=True)

matchup_table.to_csv(OUTPUT_FILE, index=False)

print("\nUpcoming matchup table built.")
print(f"Teams: {len(matchup_table)}")
print(f"Saved: {OUTPUT_FILE}")

"""
13_line_play.py

Offensive and defensive line information, built from three nflverse datasets
(the same public source already used for EPA, game stats, and red zone usage):

  play_by_play   -> team-level pass protection / pass rush / run blocking /
                    run stopping rates, plus sack / QB-hit / TFL credits by
                    defender name
  snap_counts    -> who actually plays on the line (positions T, G, C, OL for
                    the offense; DE, DT, NT, DL for the defense) and how many
                    snaps each gets, which is how "starters" are identified
  injuries       -> the official weekly injury report, which (unlike ESPN's
                    feed this project uses elsewhere) carries a position for
                    every player, so linemen can be picked out of it

Outputs (all in site/data):
  nfl_{SEASON}_line_team.csv      one row per team: OL metrics, DL metrics,
                                  plus starter-health and continuity columns
  nfl_{SEASON}_line_players.csv   one row per lineman: snaps, starter role,
                                  and (for DL) sacks / QB hits / TFL
  nfl_{SEASON}_line_injuries.csv  linemen on the latest injury report, each
                                  flagged starter vs reserve

Known limits, stated here so nobody over-reads the numbers:
  * Team rates are credited to the whole unit. Sacks and stuffs also come
    from linebackers and defensive backs, and QB/coverage factors affect
    sacks allowed, so these are line-play indicators, not individual grades.
  * nflverse has no pressure data (that's PFF / Next Gen Stats), so "QB hit"
    and "sack" are the only pass-rush outcomes available.
  * The per-play participation file isn't published for this season, so
    individual blocking assignments can't be isolated.
  * "Starters" are the top 5 OL (top 4 DL) by snaps in a team's most recent
    game -- a snap-based proxy, not an official depth chart.
  * Edge rushers that Pro Football Reference lists as LB are included in the
    DL player table (Role "Edge (LB)") when they have >= 1 sack or >= 2 QB
    hits, since otherwise ~40% of sacks would be invisible there.

Non-critical in run_daily.py: if nflverse hiccups, the rest of the dashboard
still refreshes normally.
"""
import re
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, fix_nflverse_team_abbrs

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
PBP_URL = f"{BASE}/pbp/play_by_play_{SEASON}.parquet"
SNAP_URL = f"{BASE}/snap_counts/snap_counts_{SEASON}.parquet"
INJ_URL = f"{BASE}/injuries/injuries_{SEASON}.parquet"

TEAM_FILE = PROCESSED_DIR / f"nfl_{SEASON}_line_team.csv"
PLAYER_FILE = PROCESSED_DIR / f"nfl_{SEASON}_line_players.csv"
INJURY_FILE = PROCESSED_DIR / f"nfl_{SEASON}_line_injuries.csv"

OL_POS = {"T", "G", "C", "OL"}
DL_POS = {"DE", "DT", "NT", "DL"}
N_STARTERS = {"OL": 5, "DL": 4}

# Availability buckets used for the team health columns.
OUT_STATUSES = {"Out", "Doubtful"}
RISK_STATUSES = {"Questionable", "DNP (no game status yet)"}

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


# ---------------------------------------------------------------- name helpers
def _tokens(full_name):
    tokens = re.sub(r"[.,]", " ", str(full_name)).split()
    return [t for t in tokens if t.lower() not in NAME_SUFFIXES]


def pbp_style_name(full_name):
    """'Dante Stills' -> 'D.Stills' (how nflverse play-by-play abbreviates)."""
    tokens = _tokens(full_name)
    if len(tokens) < 2:
        return None
    first, rest = tokens[0], tokens[1:]
    # "T.J. Watt" tokenizes to T / J / Watt; play-by-play writes "T.Watt".
    while len(rest) > 1 and len(rest[0]) == 1:
        rest = rest[1:]
    return f"{first[0]}.{' '.join(rest)}"


def norm_name(full_name):
    """Lowercase letters only, suffixes dropped -- for matching across sources."""
    return "".join(re.sub(r"[^a-z]", "", t.lower()) for t in _tokens(full_name))


# ---------------------------------------------------------------- team metrics
def side_metrics(pbp, team_col, prefix):
    """Pass and run metrics for every team on one side of the ball.

    team_col='posteam' gives the OFFENSE view (pass protection, run blocking);
    team_col='defteam' gives the DEFENSE view (pass rush, run stopping).
    """
    dropbacks = pbp[pbp["qb_dropback"] == 1].copy()
    dropbacks["_disrupt"] = ((dropbacks["sack"] == 1) | (dropbacks["qb_hit"] == 1)).astype(int)

    runs = pbp[
        (pbp["rush_attempt"] == 1) & (pbp["play_type"] == "run")
        & (pbp["qb_scramble"] != 1) & (pbp["qb_kneel"] != 1)
    ].copy()
    if "two_point_attempt" in runs.columns:
        runs = runs[runs["two_point_attempt"] != 1]
    runs["_stuff"] = (runs["yards_gained"] <= 0).astype(int)
    runs["_explosive"] = (runs["yards_gained"] >= 10).astype(int)

    d = dropbacks.groupby(team_col)
    r = runs.groupby(team_col)
    return pd.DataFrame({
        f"{prefix} Dropbacks": d.size(),
        f"{prefix} Sack Rate": d["sack"].mean(),
        f"{prefix} QB Hit Rate": d["qb_hit"].mean(),
        f"{prefix} Disruption Rate": d["_disrupt"].mean(),
        f"{prefix} Runs": r.size(),
        f"{prefix} Stuff Rate": r["_stuff"].mean(),
        f"{prefix} Explosive Run Rate": r["_explosive"].mean(),
        f"{prefix} YPC": r["yards_gained"].mean(),
        f"{prefix} Rush EPA": r["epa"].mean(),
    })


# ---------------------------------------------------------------- player table
def defender_credits(pbp):
    """(defense team, 'D.Stills') -> {'Sacks','QB Hits','TFL'} from play-by-play."""
    credits = defaultdict(lambda: {"Sacks": 0.0, "QB Hits": 0.0, "TFL": 0.0})
    sources = [
        ("sack_player_name", "Sacks", 1.0), ("half_sack_1_player_name", "Sacks", 0.5),
        ("half_sack_2_player_name", "Sacks", 0.5),
        ("qb_hit_1_player_name", "QB Hits", 1.0), ("qb_hit_2_player_name", "QB Hits", 1.0),
        ("tackle_for_loss_1_player_name", "TFL", 1.0), ("tackle_for_loss_2_player_name", "TFL", 1.0),
    ]
    for col, field, amount in sources:
        if col not in pbp.columns:
            continue
        sub = pbp[pbp[col].notna()]
        for (team, name), n in sub.groupby(["defteam", col]).size().items():
            credits[(team, name)][field] += n * amount
    return credits


def build_players(snaps, pbp):
    """One row per lineman. Includes LB-listed players who are clearly rushing
    the passer (>= 1 sack or >= 2 QB hits), because Pro Football Reference
    lists many edge rushers as LB and a DL-only table would hide ~40% of sacks."""
    snaps = snaps.copy()
    snaps["Group"] = np.select(
        [snaps["position"].isin(OL_POS), snaps["position"].isin(DL_POS), snaps["position"] == "LB"],
        ["OL", "DL", "LB"], default="OTHER",
    )
    is_off = snaps["Group"] == "OL"
    snaps["snap_ct"] = np.where(is_off, snaps["offense_snaps"], snaps["defense_snaps"])
    snaps["snap_pct"] = np.where(is_off, snaps["offense_pct"], snaps["defense_pct"])

    # A team's most recent game (any position) defines "last game" for everyone on it.
    team_last_week = snaps.groupby("team")["week"].max().to_dict()

    # Ambiguity guard: if two defenders on one team abbreviate identically
    # ('J.Williams'), credits can't be assigned safely, so nobody gets them.
    defenders = snaps[snaps["defense_snaps"].fillna(0) > 0].drop_duplicates(["team", "player"])
    abbr_counts = defaultdict(int)
    for _, row in defenders.iterrows():
        abbr_counts[(row["team"], pbp_style_name(row["player"]))] += 1

    credits = defender_credits(pbp)
    empty = {"Sacks": 0.0, "QB Hits": 0.0, "TFL": 0.0}

    played = snaps[(snaps["Group"] != "OTHER") & (snaps["snap_ct"].fillna(0) > 0)]
    rows = []
    for (team, group), grp in played.groupby(["team", "Group"]):
        if group == "LB":
            continue  # handled below, only for clear pass rushers
        last_week = team_last_week[team]
        last = grp[grp["week"] == last_week].sort_values(["snap_ct", "player"], ascending=[False, True])
        starters = set(last.head(N_STARTERS[group])["player"])
        for player, pg in grp.groupby("player"):
            rows.append(_player_row(team, group, player, pg, last_week,
                                    "Starter" if player in starters else "Reserve", credits, abbr_counts, empty))

    # Edge rushers listed as LB: included only on pass-rush production.
    for (team, player), pg in played[played["Group"] == "LB"].groupby(["team", "player"]):
        key = (team, pbp_style_name(player))
        if not key[1] or abbr_counts[key] != 1:
            continue
        c = credits.get(key, empty)
        if c["Sacks"] >= 1 or c["QB Hits"] >= 2:
            rows.append(_player_row(team, "DL", player, pg, team_last_week[team],
                                    "Edge (LB)", credits, abbr_counts, empty))

    players = pd.DataFrame(rows)

    # How much DL-credited production landed on a player in this table?
    matched = players[players["Group"] == "DL"][["Sacks", "QB Hits", "TFL"]].fillna(0).sum()
    total = pd.Series({"Sacks": 0.0, "QB Hits": 0.0, "TFL": 0.0})
    for c in credits.values():
        for k in total.index:
            total[k] += c[k]
    return players, matched, total


def _player_row(team, group, player, pg, last_week, role, credits, abbr_counts, empty):
    last_row = pg[pg["week"] == last_week]
    row = {
        "Player": player, "Team": team, "Group": group, "Position": pg.iloc[-1]["position"],
        "Games": pg["week"].nunique(), "Snaps": int(pg["snap_ct"].sum()),
        "Avg Snap Pct": round(float(pg["snap_pct"].mean()), 3),
        "Last Game Week": int(last_week),
        "Last Game Snaps": int(last_row["snap_ct"].sum()) if len(last_row) else 0,
        "Last Game Snap Pct": round(float(last_row["snap_pct"].iloc[0]), 3) if len(last_row) else 0.0,
        "Role": role, "Sacks": np.nan, "QB Hits": np.nan, "TFL": np.nan,
    }
    if group == "DL":
        key = (team, pbp_style_name(player))
        if key[1] and abbr_counts[key] == 1:
            c = credits.get(key, empty)
            row["Sacks"], row["QB Hits"], row["TFL"] = c["Sacks"], c["QB Hits"], c["TFL"]
    return row


# ---------------------------------------------------------------- injuries
def availability(row):
    status = row.get("report_status")
    if isinstance(status, str) and status:
        return status                       # Out / Doubtful / Questionable
    practice = str(row.get("practice_status") or "")
    if "Did Not" in practice:
        return "DNP (no game status yet)"   # missed practice, no game designation yet
    if "Limited" in practice:
        return "Limited"
    return "Full"


def build_injuries(inj, players):
    latest = int(inj["week"].max())
    edge_keys = {(p["Team"], norm_name(p["Player"])) for _, p in players.iterrows() if p["Role"] == "Edge (LB)"}
    is_edge = inj.apply(lambda r: r["position"] == "LB" and (r["team"], norm_name(r["full_name"])) in edge_keys, axis=1)
    inj = inj[(inj["week"] == latest) & (inj["position"].isin(OL_POS | DL_POS) | is_edge)].copy()
    inj["Group"] = np.where(inj["position"].isin(OL_POS), "OL", "DL")
    inj["Availability"] = inj.apply(availability, axis=1)
    inj = inj[inj["Availability"] != "Full"]   # listed but fully practicing -> not actionable

    lookup = {}
    for _, p in players.iterrows():
        lookup[(p["Team"], norm_name(p["Player"]), p["Group"])] = (p["Role"], p["Snaps"])

    rows = []
    for _, r in inj.iterrows():
        role, snaps = lookup.get((r["team"], norm_name(r["full_name"]), r["Group"]), ("No snaps yet", 0))
        rows.append({
            "Team": r["team"], "Player": r["full_name"], "Group": r["Group"], "Position": r["position"],
            "Availability": r["Availability"],
            "Report Status": r["report_status"] if isinstance(r["report_status"], str) else "",
            "Practice Status": r["practice_status"] if isinstance(r["practice_status"], str) else "",
            "Injury": r["report_primary_injury"] if isinstance(r["report_primary_injury"], str)
                      else (r["practice_primary_injury"] if isinstance(r["practice_primary_injury"], str) else ""),
            "Role": role, "Season Snaps": int(snaps), "Week": latest,
        })
    return pd.DataFrame(rows), latest


# ---------------------------------------------------------------- team health
def health_columns(players, injuries, snaps):
    """Per-team starter health + OL continuity, as extra team columns.

    OL: the five starters. DL: the four starters plus any LB-listed edge
    rushers, since those are real pass-rush contributors too."""
    out = defaultdict(dict)
    key_roles = {"OL": {"Starter"}, "DL": {"Starter", "Edge (LB)"}}
    labels = {"OL": "Starters", "DL": "Key Players"}

    for group in ("OL", "DL"):
        key = players[(players["Group"] == group) & players["Role"].isin(key_roles[group])]
        for team in key["Team"].unique():
            inj = injuries[(injuries["Team"] == team) & (injuries["Group"] == group) & injuries["Role"].isin(key_roles[group])]
            out[team][f"{group} {labels[group]} Out"] = int(inj["Availability"].isin(OUT_STATUSES).sum())
            out[team][f"{group} {labels[group]} At Risk"] = int(inj["Availability"].isin(RISK_STATUSES).sum())
            flagged = inj[inj["Availability"].isin(OUT_STATUSES | RISK_STATUSES)]
            out[team][f"{group} Health Note"] = "; ".join(
                f"{r['Player']} ({r['Position']}) {r['Availability']}" for _, r in flagged.iterrows()
            )

    # OL continuity: top-5 OL by snaps each week; how many different lineups so
    # far, and how many of the latest five also started the previous game.
    ol = snaps[snaps["position"].isin(OL_POS) & (snaps["offense_snaps"].fillna(0) > 0)]
    for team, grp in ol.groupby("team"):
        lineups = {}
        for week, wg in grp.groupby("week"):
            lineups[week] = frozenset(wg.sort_values(["offense_snaps", "player"], ascending=[False, True]).head(5)["player"])
        weeks = sorted(lineups)
        out[team]["OL Games Tracked"] = len(weeks)
        out[team]["OL Lineups Used"] = len(set(lineups.values()))
        out[team]["OL Same Starters vs Prev Game"] = (
            len(lineups[weeks[-1]] & lineups[weeks[-2]]) if len(weeks) >= 2 else np.nan
        )
    return out


# ---------------------------------------------------------------- main
def main():
    print(f"Downloading {SEASON} play-by-play, snap counts, and injuries...")
    try:
        pbp = pd.read_parquet(PBP_URL)
        snaps = pd.read_parquet(SNAP_URL)
        inj = pd.read_parquet(INJ_URL)
    except Exception as exc:
        print(f"Failed to download line-play data: {exc}")
        sys.exit(1)

    for df, cols in ((pbp, ["posteam", "defteam"]), (snaps, ["team", "opponent"]), (inj, ["team"])):
        for col in cols:
            if col in df.columns:
                df[col] = fix_nflverse_team_abbrs(df[col])

    pbp = pbp[pbp["season_type"] == "REG"].copy()
    for col in ("sack", "qb_hit", "qb_scramble", "qb_kneel"):
        pbp[col] = pbp[col].fillna(0)
    snaps = snaps[snaps["game_type"] == "REG"].copy()

    print(f"Plays: {len(pbp):,}  |  snap rows: {len(snaps):,}  |  injury rows: {len(inj):,}")

    team_df = side_metrics(pbp, "posteam", "OL").join(side_metrics(pbp, "defteam", "DL"), how="outer")
    team_df.index.name = "Team"
    team_df["Games"] = pbp.groupby("posteam")["week"].nunique()

    players, matched, total = build_players(snaps, pbp)
    injuries, latest_week = build_injuries(inj, players)
    health = health_columns(players, injuries, snaps)
    health_df = pd.DataFrame.from_dict(health, orient="index")

    team_df = team_df.join(health_df, how="left").reset_index()
    for col in ("OL Starters Out", "OL Starters At Risk", "DL Key Players Out", "DL Key Players At Risk"):
        team_df[col] = team_df[col].fillna(0).astype(int)
    for col in ("OL Health Note", "DL Health Note"):
        team_df[col] = team_df[col].fillna("")

    num_cols = [c for c in team_df.columns if c.endswith("Rate") or c.endswith("YPC") or c.endswith("EPA")]
    team_df[num_cols] = team_df[num_cols].round(4)

    team_df.sort_values("Team").to_csv(TEAM_FILE, index=False)
    players.sort_values(["Team", "Group", "Snaps"], ascending=[True, True, False]).to_csv(PLAYER_FILE, index=False)
    injuries.sort_values(["Team", "Group", "Role"]).to_csv(INJURY_FILE, index=False)

    print(f"\nSaved: {TEAM_FILE.name} ({len(team_df)} teams)")
    print(f"Saved: {PLAYER_FILE.name} ({len(players)} linemen: "
          f"{(players['Group'] == 'OL').sum()} OL, {(players['Group'] == 'DL').sum()} DL)")
    print(f"Saved: {INJURY_FILE.name} ({len(injuries)} linemen on the week-{latest_week} report)")

    print("\nCoverage checks:")
    for k in ("Sacks", "QB Hits", "TFL"):
        share = matched[k] / total[k] if total[k] else 0
        print(f"  {k}: {share:.0%} of defender credits landed on a listed DL player "
              f"(remainder are other LB/DB players)")
    print(f"  Injury rows matched to a snap-count lineman: "
          f"{(injuries['Role'] != 'No snaps yet').sum()} of {len(injuries)}")

    hurt = team_df[(team_df["OL Starters Out"] > 0)].sort_values("OL Starters Out", ascending=False)
    if len(hurt):
        print("\nTeams with OL starters Out/Doubtful:")
        for _, r in hurt.head(8).iterrows():
            print(f"  {r['Team']}: {r['OL Health Note']}")


if __name__ == "__main__":
    main()

"""
14_defense_roster.py

Defensive personnel for the "Defense Field" tab: who actually plays at DL, LB,
CB and S for every team, how many snaps they get, what the play-by-play credits
them with, whether they're on the injury report, and a coverage PROXY log.

Built from the same public nflverse datasets as 13_line_play.py
(play_by_play, snap_counts, injuries).

Outputs (site/data):
  nfl_{SEASON}_defense_roster.csv    one row per defender (DL/LB/CB/S)
  nfl_{SEASON}_defense_coverage.csv  one row per pass play where a defender is
                                     credited against a named receiver

Known limits -- read these before trusting a number:
  * nflverse has NO coverage assignments and NO alignment data (that is PFF /
    Next Gen Stats tracking data). Nobody here can say a corner "shadowed" a
    receiver or lined up left or right. The coverage log only contains plays
    where the defender is CREDITED: a pass breakup, an interception, or the
    tackle after a catch. A corner who covered a receiver perfectly and never
    touched the ball does not appear. Treat it as a floor, not a full picture.
  * "Starters" are the seven DL+LB, three CB (third = nickel) and two S with
    the most snaps in a team's most recent game -- a snap-based proxy, not an
    official depth chart. Because DL and LB are ranked together, a 3-4 front
    shows up as 3 DL + 4 LB automatically.
  * The per-play participation file isn't published for this season.

Non-critical in run_daily.py.
"""
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, PLAYER_FILE, fix_nflverse_team_abbrs

_spec = importlib.util.spec_from_file_location("line_play", Path(__file__).with_name("13_line_play.py"))
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
PBP_URL = f"{BASE}/pbp/play_by_play_{SEASON}.parquet"
SNAP_URL = f"{BASE}/snap_counts/snap_counts_{SEASON}.parquet"
INJ_URL = f"{BASE}/injuries/injuries_{SEASON}.parquet"

ROSTER_FILE = PROCESSED_DIR / f"nfl_{SEASON}_defense_roster.csv"
COVERAGE_FILE = PROCESSED_DIR / f"nfl_{SEASON}_defense_coverage.csv"

GROUP_OF = {
    "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL",
    "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
    "CB": "CB", "DB": "CB",
    "S": "S", "FS": "S", "SS": "S", "SAF": "S",
}
N_FRONT, N_CB, N_S = 7, 3, 2


def credit_table(pbp):
    """(defteam, 'D.Stills') -> season counting stats from play-by-play."""
    fields = {
        "Sacks": [("sack_player_name", 1.0), ("half_sack_1_player_name", 0.5), ("half_sack_2_player_name", 0.5)],
        "QB Hits": [("qb_hit_1_player_name", 1.0), ("qb_hit_2_player_name", 1.0)],
        "TFL": [("tackle_for_loss_1_player_name", 1.0), ("tackle_for_loss_2_player_name", 1.0)],
        "Tackles": [("solo_tackle_1_player_name", 1.0), ("solo_tackle_2_player_name", 1.0),
                    ("assist_tackle_1_player_name", 1.0), ("assist_tackle_2_player_name", 1.0),
                    ("assist_tackle_3_player_name", 1.0), ("assist_tackle_4_player_name", 1.0)],
        "Pass Breakups": [("pass_defense_1_player_name", 1.0), ("pass_defense_2_player_name", 1.0)],
        "INT": [("interception_player_name", 1.0)],
        "Forced Fumbles": [("forced_fumble_player_1_player_name", 1.0), ("forced_fumble_player_2_player_name", 1.0)],
    }
    out = defaultdict(lambda: {k: 0.0 for k in fields})
    for field, sources in fields.items():
        for col, amount in sources:
            if col not in pbp.columns:
                continue
            sub = pbp[pbp[col].notna()]
            for (team, name), n in sub.groupby(["defteam", col]).size().items():
                out[(team, name)][field] += n * amount
    return out


def receiver_name_map(player_file):
    """(team, 'D.Adams') -> full dashboard name, only where unambiguous."""
    if not player_file.exists():
        return {}
    df = pd.read_csv(player_file, usecols=["Player", "Team"]).drop_duplicates()
    seen = defaultdict(set)
    for _, r in df.iterrows():
        seen[(r["Team"], lp.pbp_style_name(r["Player"]))].add(r["Player"])
    return {k: next(iter(v)) for k, v in seen.items() if k[1] and len(v) == 1}


def build_coverage(pbp, name_map):
    """Plays where a named defender is credited against a named receiver."""
    need = {"receiver_player_name", "pass_attempt", "posteam", "defteam", "week"}
    if not need.issubset(pbp.columns):
        return pd.DataFrame()
    passes = pbp[(pbp["pass_attempt"] == 1) & pbp["receiver_player_name"].notna()].copy()
    rows = []
    events = [
        ("pass_defense_1_player_name", "Pass breakup"), ("pass_defense_2_player_name", "Pass breakup"),
        ("interception_player_name", "Interception"),
    ]
    for col, label in events:
        if col in passes.columns:
            for _, r in passes[passes[col].notna()].iterrows():
                rows.append((r["defteam"], r[col], r["posteam"], r["receiver_player_name"], int(r["week"]), label, 0))
    if "complete_pass" in passes.columns and "solo_tackle_1_player_name" in passes.columns:
        done = passes[(passes["complete_pass"] == 1) & passes["solo_tackle_1_player_name"].notna()]
        if "solo_tackle_1_team" in done.columns:
            done = done[done["solo_tackle_1_team"] == done["defteam"]]
        for _, r in done.iterrows():
            rows.append((r["defteam"], r["solo_tackle_1_player_name"], r["posteam"], r["receiver_player_name"],
                         int(r["week"]), "Tackled after catch", int(r["yards_gained"]) if pd.notna(r["yards_gained"]) else 0))
    cov = pd.DataFrame(rows, columns=["Team", "Defender Abbr", "Receiver Team", "Receiver Abbr", "Week", "Event", "Yards"])
    cov["Receiver"] = [name_map.get((t, a), a) for t, a in zip(cov["Receiver Team"], cov["Receiver Abbr"])]
    return cov


def build_roster(snaps, pbp, inj):
    snaps = snaps.copy()
    snaps["Group"] = snaps["position"].map(GROUP_OF)
    snaps = snaps[snaps["Group"].notna() & (snaps["defense_snaps"].fillna(0) > 0)]

    credits = credit_table(pbp)
    empty = {k: 0.0 for k in next(iter(credits.values()), {"Sacks": 0})} if credits else {}
    abbr_counts = defaultdict(int)
    for (team, player), _ in snaps.groupby(["team", "player"]):
        abbr_counts[(team, lp.pbp_style_name(player))] += 1

    team_last = snaps.groupby("team")["week"].max().to_dict()
    inj_lat = pd.DataFrame()
    if inj is not None and len(inj):
        latest = int(inj["week"].max())
        inj_lat = inj[inj["week"] == latest].copy()
        inj_lat["Availability"] = inj_lat.apply(lp.availability, axis=1)
        inj_lat["_k"] = [(t, lp.norm_name(n)) for t, n in zip(inj_lat["team"], inj_lat["full_name"])]
    inj_by = {r["_k"]: r for _, r in inj_lat.iterrows()} if len(inj_lat) else {}

    rows = []
    for team, tg in snaps.groupby("team"):
        lw = team_last[team]
        last = tg[tg["week"] == lw].groupby(["player", "Group"], as_index=False).agg(
            snaps=("defense_snaps", "sum"), pct=("defense_pct", "max"))
        front = last[last["Group"].isin(["DL", "LB"])].sort_values(["snaps", "player"], ascending=[False, True])
        cb = last[last["Group"] == "CB"].sort_values(["snaps", "player"], ascending=[False, True])
        sf = last[last["Group"] == "S"].sort_values(["snaps", "player"], ascending=[False, True])
        role = {}
        for p in front.head(N_FRONT)["player"]:
            role[p] = "Starter"
        for i, p in enumerate(cb.head(N_CB)["player"]):
            role[p] = "Starter" if i < 2 else "Nickel"
        for p in sf.head(N_S)["player"]:
            role[p] = "Starter"

        for (player, group), pg in tg.groupby(["player", "Group"]):
            lastrow = pg[pg["week"] == lw]
            key = (team, lp.pbp_style_name(player))
            c = credits.get(key, None) if key[1] and abbr_counts[key] == 1 else None
            r = {
                "Player": player, "Team": team, "Group": group, "Position": pg.iloc[-1]["position"],
                "Games": pg["week"].nunique(), "Snaps": int(pg["defense_snaps"].sum()),
                "Last Game Week": int(lw),
                "Last Game Snaps": int(lastrow["defense_snaps"].sum()) if len(lastrow) else 0,
                "Last Game Snap Pct": round(float(lastrow["defense_pct"].max()), 3) if len(lastrow) else 0.0,
                "Role": role.get(player, "Reserve"),
            }
            for k in ("Tackles", "Sacks", "QB Hits", "TFL", "Pass Breakups", "INT", "Forced Fumbles"):
                r[k] = c[k] if c else np.nan
            ij = inj_by.get((team, lp.norm_name(player)))
            avail = ij["Availability"] if ij is not None else "Full"
            r["Availability"] = avail
            r["Injury"] = ""
            if ij is not None:
                for col in ("report_primary_injury", "practice_primary_injury"):
                    if isinstance(ij.get(col), str) and ij.get(col):
                        r["Injury"] = ij[col]
                        break
            rows.append(r)
    return pd.DataFrame(rows)


def main():
    print(f"Downloading {SEASON} play-by-play, snap counts, injuries...")
    try:
        pbp = pd.read_parquet(PBP_URL)
        snaps = pd.read_parquet(SNAP_URL)
        try:
            inj = pd.read_parquet(INJ_URL)
        except Exception:
            inj = None
    except Exception as exc:
        print(f"Failed to download defense data: {exc}")
        sys.exit(1)

    for df, cols in ((pbp, ["posteam", "defteam", "solo_tackle_1_team"]), (snaps, ["team", "opponent"]), (inj, ["team"])):
        if df is None:
            continue
        for col in cols:
            if col in df.columns:
                df[col] = fix_nflverse_team_abbrs(df[col])

    pbp = pbp[pbp["season_type"] == "REG"].copy()
    snaps = snaps[snaps["game_type"] == "REG"].copy()

    roster = build_roster(snaps, pbp, inj)
    roster.sort_values(["Team", "Group", "Snaps"], ascending=[True, True, False]).to_csv(ROSTER_FILE, index=False)
    print(f"Saved: {ROSTER_FILE.name} ({len(roster)} defenders, {roster['Team'].nunique()} teams)")

    cov = build_coverage(pbp, receiver_name_map(PLAYER_FILE))
    if len(cov):
        # Resolve defender abbreviations to full names via the roster (unambiguous only).
        full = defaultdict(set)
        for _, r in roster.iterrows():
            full[(r["Team"], lp.pbp_style_name(r["Player"]))].add(r["Player"])
        cov["Defender"] = [next(iter(full[(t, a)])) if len(full.get((t, a), ())) == 1 else None
                           for t, a in zip(cov["Team"], cov["Defender Abbr"])]
        cov = cov[cov["Defender"].notna()]
    cov.to_csv(COVERAGE_FILE, index=False)
    print(f"Saved: {COVERAGE_FILE.name} ({len(cov)} credited plays)")


if __name__ == "__main__":
    main()

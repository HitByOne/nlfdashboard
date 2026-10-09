"""
15_pass_location.py

Where the ball goes: pass attempts split by field third (left / middle /
right, from the offense's point of view) and depth (short / deep), from
nflverse play-by-play (pass_location, pass_length). Same source as the other
nflverse steps.

Output (site/data): nfl_{SEASON}_pass_location.csv, long format, one row per
(entity, location, length):
  Kind  QB   -> a quarterback's own attempts           (Name = QB, Team = his team)
        OFF  -> every pass a team threw                 (Name = Team)
        DEF  -> every pass a defense faced              (Name = Team)
  Location  Left | Middle | Right
  Length    Short | Deep   (nflverse: Deep = 15+ air yards)
  Att, Comp, Yards, TD, INT, EPA, Air Yards

Known limits:
  * Sacks, spikes and throwaways without a charted location carry no
    pass_location, so they're excluded -- shares are shares of CHARTED attempts.
  * "Left"/"Right" are the offense's left and right, not the camera's.
  * This is where the pass was thrown, not who covered it; it can't separate a
    defense that takes away a side from an offense that simply prefers one.
    Compare the offense's share with the league average (the dashboard does).
Non-critical in run_daily.py.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, PLAYER_FILE, fix_nflverse_team_abbrs

_spec = importlib.util.spec_from_file_location("line_play", Path(__file__).with_name("13_line_play.py"))
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)

PBP_URL = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{SEASON}.parquet"
OUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_pass_location.csv"

COLS = ["Att", "Comp", "Yards", "TD", "INT", "EPA", "Air Yards"]


def qb_name_map(player_file):
    """(team, 'D.Maye') -> full name from the dashboard's own player file (unambiguous only)."""
    if not Path(player_file).exists():
        return {}
    df = pd.read_csv(player_file, usecols=["Player", "Team", "Position"])
    df = df[df["Position"] == "QB"].drop_duplicates(["Player", "Team"])
    seen = {}
    for _, r in df.iterrows():
        seen.setdefault((r["Team"], lp.pbp_style_name(r["Player"])), set()).add(r["Player"])
    return {k: next(iter(v)) for k, v in seen.items() if k[1] and len(v) == 1}


def summarize(df, keys):
    g = df.groupby(keys)
    out = pd.DataFrame({
        "Att": g.size(), "Comp": g["complete_pass"].sum(), "Yards": g["yards_gained"].sum(),
        "TD": g["pass_touchdown"].sum(), "INT": g["interception"].sum(),
        "EPA": g["epa"].sum(), "Air Yards": g["air_yards"].sum(),
    }).reset_index()
    return out


def build(pbp, names):
    p = pbp[(pbp["pass_attempt"] == 1) & pbp["pass_location"].notna() & pbp["pass_length"].notna()].copy()
    for c in ("complete_pass", "yards_gained", "pass_touchdown", "interception", "epa", "air_yards"):
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0)
    p["Location"] = p["pass_location"].str.capitalize()
    p["Length"] = p["pass_length"].str.capitalize()
    p = p[p["Location"].isin(["Left", "Middle", "Right"]) & p["Length"].isin(["Short", "Deep"])]

    off = summarize(p, ["posteam", "Location", "Length"]).rename(columns={"posteam": "Team"})
    off["Kind"], off["Name"] = "OFF", off["Team"]
    dfn = summarize(p, ["defteam", "Location", "Length"]).rename(columns={"defteam": "Team"})
    dfn["Kind"], dfn["Name"] = "DEF", dfn["Team"]

    q = p[p["passer_player_name"].notna()].copy()
    qb = summarize(q, ["posteam", "passer_player_name", "Location", "Length"]).rename(columns={"posteam": "Team"})
    qb["Name"] = [names.get((t, a), a) for t, a in zip(qb["Team"], qb["passer_player_name"])]
    qb["Kind"] = "QB"
    qb = qb.drop(columns=["passer_player_name"])
    # Drop QBs with a trivial sample (trick plays, WR passes).
    totals = qb.groupby(["Team", "Name"])["Att"].transform("sum")
    qb = qb[totals >= 10]

    out = pd.concat([qb, off, dfn], ignore_index=True)[["Kind", "Name", "Team", "Location", "Length"] + COLS]
    out["EPA"] = out["EPA"].round(2)
    out["Air Yards"] = out["Air Yards"].round(0)
    return out.sort_values(["Kind", "Team", "Name", "Location", "Length"])


def main():
    print(f"Downloading {SEASON} play-by-play...")
    try:
        pbp = pd.read_parquet(PBP_URL)
    except Exception as exc:
        print(f"Failed to download play-by-play: {exc}")
        sys.exit(1)
    for col in ("posteam", "defteam"):
        pbp[col] = fix_nflverse_team_abbrs(pbp[col])
    pbp = pbp[pbp["season_type"] == "REG"]
    out = build(pbp, qb_name_map(PLAYER_FILE))
    out.to_csv(OUT_FILE, index=False)
    print(f"Saved: {OUT_FILE.name} ({len(out)} rows; {out[out['Kind']=='QB']['Name'].nunique()} QBs)")


if __name__ == "__main__":
    main()

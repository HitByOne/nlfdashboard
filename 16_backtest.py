#!/usr/bin/env python3
"""
16_backtest.py

Replays the Best Picks page on every past week and grades it against what
actually happened.

For each completed week W (from week 3 on) it pretends it is the Tuesday
before that week and uses ONLY games from weeks before W:

  * Starters per team: QB1 by pass attempts, RB1 by rush attempts + targets,
    WR1-3 and TE1 by targets (the same usage proxy as the dashboard).
  * A pick needs the player to have cleared a floor in at least 75% of his own
    games AND starters to have cleared it in at least 60% of their games
    against that defense (at least 3 such games, the offense's own team left
    out). The highest floor that passes both is the pick; each player keeps his
    best stat. Same constants as the page.
  * The projection is the page's: 60% season average + 40% last 3, times the
    defense's factor (capped 0.6-1.6, shrunk until 6 games), no spread nudge
    (there are no historical spreads in the data).

Then the pick is graded against week W's real box score. Players with no row
in week W (inactive, or the team was on a bye) are skipped and counted.

It also writes an "own_only" comparison: the same players with the defense
check removed, so you can see whether the defense side actually adds anything.

Limits worth knowing: injury status is not available historically, so a pick
for a player who was ruled out still counts as skipped, not as a miss; early
weeks have few qualifying picks; and a few dozen picks is a small sample.

Output: site/data/nfl_{SEASON}_backtest.csv (one row per pick).
Non-critical in run_daily.py.
"""
import sys
from collections import defaultdict

import pandas as pd

from nfl_common import SEASON, PROCESSED_DIR, PLAYER_FILE

OUT_FILE = PROCESSED_DIR / f"nfl_{SEASON}_backtest.csv"

FLOORS = {
    "Pass Yards": [150, 175, 200, 225, 250, 275, 300],
    "Pass + Rush Yards": [200, 225, 250, 275, 300, 325, 350, 375, 400],
    "Rush Yards": [20, 30, 40, 50, 60, 70, 80, 90, 100],
    "Receiving Yards": [15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100],
    "Receptions": [2, 3, 4, 5, 6, 7, 8],
}
STATS = {"QB": ["Pass Yards", "Pass + Rush Yards"], "RB": ["Rush Yards", "Receiving Yards", "Receptions"],
         "WR": ["Receiving Yards", "Receptions"], "TE": ["Receiving Yards", "Receptions"]}
SLOTS = {"QB": 1, "RB": 1, "WR": 3, "TE": 1}
PLAYER_MIN_RATE, DEF_MIN_RATE, MIN_PLAYER_GAMES, MIN_DEF_SAMPLES = 0.75, 0.60, 2, 3


def usage(r):
    if r["Position"] == "QB":
        return r["Pass Attempts"]
    if r["Position"] == "RB":
        return r["Rush Attempts"] + r["Targets"]
    return r["Targets"]


def starters_as_of(hist):
    """{(team, player)} of starters using only the rows in `hist`."""
    h = hist[hist["Position"].isin(SLOTS)].copy()
    h["u"] = h.apply(usage, axis=1)
    agg = h.groupby(["Team", "Position", "Player"], as_index=False)["u"].sum()
    out = {}
    for (team, pos), g in agg.groupby(["Team", "Position"]):
        for i, (_, row) in enumerate(g.sort_values("u", ascending=False).head(SLOTS[pos]).iterrows()):
            out[(team, row["Player"])] = f"{pos}{i + 1}" if SLOTS[pos] > 1 else f"{pos}1"
    return out


def project(vals, l3, allowed, league, def_games):
    avg = sum(vals) / len(vals)
    baseline = avg * 0.6 + l3 * 0.4
    raw = allowed / league if league > 0 else 1.0
    raw = max(0.6, min(1.6, raw))
    matchup = 1 + (raw - 1) * min(1.0, def_games / 6)
    proj = baseline * matchup
    sd = max(pd.Series(vals).std(ddof=1) if len(vals) > 1 else 0.0, 0.30 * proj)
    sd = 0.30 * proj if pd.isna(sd) else max(sd, 0.30 * proj)
    return {"proj": proj, "base": baseline, "lo": max(0.0, proj - 0.67 * sd), "hi": proj + 0.67 * sd,
            "line": round(proj * 2) / 2}


def main():
    if not PLAYER_FILE.exists():
        print("No player stats file yet.")
        sys.exit(1)
    df = pd.read_csv(PLAYER_FILE)
    need = {"Week", "Team", "Opponent", "Player", "Position", "Pass Attempts", "Pass Yards", "Rush Attempts",
            "Rush Yards", "Receptions", "Receiving Yards", "Targets"}
    if not need.issubset(df.columns):
        print("Player file is missing columns:", need - set(df.columns))
        sys.exit(1)
    df = df[df["Position"].isin(SLOTS)].copy()
    for c in ["Pass Attempts", "Pass Yards", "Rush Attempts", "Rush Yards", "Receptions", "Receiving Yards", "Targets"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["Pass + Rush Yards"] = df["Pass Yards"] + df["Rush Yards"]
    df["Week"] = pd.to_numeric(df["Week"], errors="coerce")
    weeks = sorted(int(w) for w in df["Week"].dropna().unique())
    rows, skipped = [], 0

    for W in weeks:
        if W < 3:
            continue
        hist = df[df["Week"] < W]
        cur = df[df["Week"] == W]
        if hist.empty or cur.empty:
            continue
        starters = starters_as_of(hist)
        hist = hist.copy()
        hist["is_starter"] = [(t, p) in starters for t, p in zip(hist["Team"], hist["Player"])]
        by_player = {k: g for k, g in hist.groupby(["Team", "Player"])}
        actual = {(r["Team"], r["Player"]): r for r in cur.to_dict("records")}
        def_games = hist.groupby("Opponent")["Week"].nunique().to_dict()
        # league average per starter-game, per position and stat
        league = {}
        hs = hist[hist["is_starter"]]
        for pos, stats in STATS.items():
            sub = hs[hs["Position"] == pos]
            for s in stats:
                league[(pos, s)] = float(sub[s].mean()) if len(sub) else 0.0

        for off, d in {(r.Team, r.Opponent) for r in cur.itertuples(index=False)}:
            for (team, player), slot in starters.items():
                if team != off:
                    continue
                g = by_player.get((team, player))
                if g is None or len(g) < MIN_PLAYER_GAMES:
                    continue
                pos = g.iloc[0]["Position"]
                act = actual.get((team, player))
                if act is None:
                    skipped += 1
                    continue
                dsub = hist[(hist["Opponent"] == d) & (hist["Position"] == pos) & hist["is_starter"] & (hist["Team"] != off)]
                gsorted = g.sort_values("Week")
                best_bp, best_own = None, None
                for stat in STATS[pos]:
                    vals = list(g[stat])
                    avg = sum(vals) / len(vals)
                    if avg <= 0:
                        continue
                    l3 = list(gsorted[stat].tail(3))
                    l3avg = sum(l3) / len(l3)
                    own_found, found = None, None
                    for f in FLOORS[stat]:
                        pr = sum(v >= f for v in vals) / len(vals)
                        if pr >= PLAYER_MIN_RATE:
                            own_found = {"floor": f, "pr": pr}
                        if len(dsub) >= MIN_DEF_SAMPLES:
                            dr = float((dsub[stat] >= f).mean())
                            if pr >= PLAYER_MIN_RATE and dr >= DEF_MIN_RATE:
                                found = {"floor": f, "pr": pr, "dr": dr}
                    allowed = float(dsub[stat].mean()) if len(dsub) else league[(pos, stat)]
                    pj = project(vals, l3avg, allowed, league[(pos, stat)], def_games.get(d, 0))
                    result = float(act[stat])
                    if found:
                        entry = {"Model": "best_picks", "Week": W, "Player": player, "Slot": slot, "Position": pos, "Team": team,
                                 "Opponent": d, "Stat": stat, "Floor": found["floor"], "OwnRate": round(found["pr"], 3),
                                 "DefRate": round(found["dr"], 3), "Score": round((found["pr"] + found["dr"]) / 2, 3),
                                 "Projection": round(pj["proj"], 1), "ProjNoMatchup": round(pj["base"], 1),
                                 "Lo": round(pj["lo"]), "Hi": round(pj["hi"]), "Line": pj["line"], "Actual": result}
                        if not best_bp or entry["Score"] > best_bp["Score"] or (entry["Score"] == best_bp["Score"] and entry["Floor"] > best_bp["Floor"] and entry["Stat"] != "Pass + Rush Yards"):
                            best_bp = entry
                    if own_found:
                        entry = {"Model": "own_only", "Week": W, "Player": player, "Slot": slot, "Position": pos, "Team": team,
                                 "Opponent": d, "Stat": stat, "Floor": own_found["floor"], "OwnRate": round(own_found["pr"], 3),
                                 "DefRate": "", "Score": round(own_found["pr"], 3), "Projection": round(pj["proj"], 1),
                                 "ProjNoMatchup": round(pj["base"], 1), "Lo": round(pj["lo"]), "Hi": round(pj["hi"]),
                                 "Line": pj["line"], "Actual": result}
                        if not best_own or entry["Score"] > best_own["Score"] or (entry["Score"] == best_own["Score"] and entry["Floor"] > best_own["Floor"] and entry["Stat"] != "Pass + Rush Yards"):
                            best_own = entry
                for e in (best_bp, best_own):
                    if e:
                        e["Hit"] = int(e["Actual"] >= e["Floor"])
                        rows.append(e)

    out = pd.DataFrame(rows)
    if out.empty:
        print("No qualifying picks to backtest yet (need at least 3 weeks of games).")
        out = pd.DataFrame(columns=["Model", "Week", "Player", "Slot", "Position", "Team", "Opponent", "Stat", "Floor", "OwnRate",
                                    "DefRate", "Score", "Projection", "ProjNoMatchup", "Lo", "Hi", "Line", "Actual", "Hit"])
    out.to_csv(OUT_FILE, index=False)
    bp = out[out["Model"] == "best_picks"]
    ow = out[out["Model"] == "own_only"]
    print(f"Saved: {OUT_FILE.name} ({len(bp)} best-picks rows, {len(ow)} own-only rows, {skipped} skipped: no row that week)")
    if len(bp):
        print(f"Best Picks floors cleared: {bp['Hit'].mean():.1%} of {len(bp)}; own-only: {ow['Hit'].mean():.1%} of {len(ow)}")


if __name__ == "__main__":
    main()

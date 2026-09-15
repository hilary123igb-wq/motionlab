"""Build the MotionLab DuckDB warehouse from the immutable raw layer.

Reads every tournament folder under data/raw/ and produces five tables, each
keyed by tournament_id so tournaments never collide:

    tournaments   one row per ingested tournament
    rounds        preliminary rounds only
    motions       one row per motion that is attached to a preliminary round
    teams         team identity (no speaker names)
    debate_teams  THE GRAIN: one row per team per debate

Re-runnable: tables are replaced, the raw layer is never touched.
"""

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

RAW = Path("data/raw")
DB_PATH = Path("data/motionlab.duckdb")


def load(path: Path):
    """Read one raw file and unwrap the MotionLab envelope."""
    return json.loads(path.read_text())["data"]


def url_id(url: str) -> int:
    """Last path segment of a Tabbycat URL as an int.

    Note: Tabbycat's round URLs carry the round's *seq*, not its database id
    (round 1 has id 16695 but URL .../rounds/1). Verified against live data.
    """
    return int(url.rstrip("/").split("/")[-1])


def read_tournament(folder: Path) -> dict[str, pd.DataFrame]:
    tid = folder.name
    meta = load(folder / "tournament.json")
    meta = meta[0] if isinstance(meta, list) else meta

    rounds = pd.DataFrame(
        [
            {
                "tournament_id": tid,
                "round_seq": r["seq"],
                "round_name": r["name"],
                "stage": r["stage"],
            }
            for r in load(folder / "rounds.json")
            if r["stage"] == "P"
        ]
    )

    motions = pd.DataFrame(
        [
            {
                "tournament_id": tid,
                "motion_id": m["id"],
                "round_seq": url_id(m["rounds"][0]["round"]),
                "motion_text": (m.get("text") or "").strip(),
                "info_slide": (m.get("info_slide_plain") or m.get("info_slide") or "").strip(),
                "reference": m.get("reference"),
            }
            for m in load(folder / "motions.json")
            if m.get("rounds")  # unattached test motions are dropped here
        ]
    )

    teams = pd.DataFrame(
        [
            {
                "tournament_id": tid,
                "team_id": t["id"],
                "team_name": t.get("long_name") or t.get("short_name"),
                "institution_id": url_id(t["institution"]) if t.get("institution") else None,
            }
            for t in load(folder / "teams.json")
        ]
    )

    rows = []
    for ballot_file in sorted((folder / "ballots").glob("*/*.json")):
        ballots = load(ballot_file)
        if not ballots:
            continue
        sheets = ballots[0].get("result", {}).get("sheets", [])
        if not sheets:
            continue
        for team in sheets[0].get("teams", []):
            if team.get("points") is None:
                continue
            rows.append(
                {
                    "tournament_id": tid,
                    "debate_id": int(ballot_file.stem),
                    "round_seq": int(ballot_file.parent.name.removeprefix("round_")),
                    "team_id": url_id(team["team"]),
                    "side": team["side"],
                    "team_points": team["points"],
                }
            )
    debate_teams = pd.DataFrame(rows)

    tournaments = pd.DataFrame(
        [
            {
                "tournament_id": tid,
                "title": meta.get("short_name") or meta.get("name") or tid,
                "full_name": meta.get("name"),
                "slug": meta.get("slug"),
                "n_rounds": len(rounds),
                "n_teams": len(teams),
            }
        ]
    )

    return {
        "tournaments": tournaments,
        "rounds": rounds,
        "motions": motions,
        "teams": teams,
        "debate_teams": debate_teams,
    }


def build() -> None:
    folders = sorted(p for p in RAW.iterdir() if (p / "rounds.json").exists())
    if not folders:
        raise SystemExit("No ingested tournaments found in data/raw/")

    collected: dict[str, list[pd.DataFrame]] = {}
    for folder in folders:
        tables = read_tournament(folder)
        n = len(tables["debate_teams"])
        print(f"  {folder.name:<26}{n:>7} team-results")
        if n == 0:
            continue  # a tournament with no readable ballots contributes nothing
        for name, df in tables.items():
            collected.setdefault(name, []).append(df)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    print()
    for name, frames in collected.items():
        df = pd.concat(frames, ignore_index=True)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
        print(f"{name:<16}{len(df):>8} rows")
    con.close()


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()


if __name__ == "__main__":
    main()

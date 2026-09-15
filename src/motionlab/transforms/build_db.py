"""Build the MotionLab DuckDB warehouse from the immutable raw layer."""

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

RAW = Path("data/raw")
DB_PATH = Path("data/motionlab.duckdb")


def load(path: Path):
    return json.loads(path.read_text())["data"]


def url_id(url: str) -> int:
    """Last path segment of a Tabbycat URL, as an int."""
    return int(url.rstrip("/").split("/")[-1])


def build(tournament: str) -> None:
    root = RAW / tournament

    rounds = pd.DataFrame(
        [
            {"round_seq": r["seq"], "round_name": r["name"], "stage": r["stage"]}
            for r in load(root / "rounds.json")
            if r["stage"] == "P"
        ]
    )

    motions = pd.DataFrame(
        [
            {
                "motion_id": m["id"],
                "round_seq": url_id(m["rounds"][0]["round"]),
                "motion_text": m["text"],
                "info_slide": m.get("info_slide_plain") or "",
                "reference": m.get("reference"),
            }
            for m in load(root / "motions.json")
            if m["rounds"]
        ]
    )

    teams = pd.DataFrame(
        [
            {
                "team_id": t["id"],
                "team_name": t["long_name"],
                "institution_id": url_id(t["institution"]) if t.get("institution") else None,
            }
            for t in load(root / "teams.json")
        ]
    )

    rows = []
    for ballot_file in sorted((root / "ballots").glob("*/*.json")):
        ballots = load(ballot_file)
        if not ballots:
            continue
        sheets = ballots[0].get("result", {}).get("sheets", [])
        if not sheets:
            continue
        for team in sheets[0]["teams"]:
            rows.append(
                {
                    "debate_id": int(ballot_file.stem),
                    "round_seq": int(ballot_file.parent.name.removeprefix("round_")),
                    "team_id": url_id(team["team"]),
                    "side": team["side"],
                    "team_points": team["points"],
                }
            )
    debate_teams = pd.DataFrame(rows)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    for name, df in [
        ("rounds", rounds),
        ("motions", motions),
        ("teams", teams),
        ("debate_teams", debate_teams),
    ]:
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
        print(f"{name:<14} {len(df):>6} rows")
    con.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tournament", help="folder in data/raw, e.g. wudc_2022")
    build(parser.parse_args().tournament)


if __name__ == "__main__":
    main()
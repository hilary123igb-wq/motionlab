"""Build the MotionLab DuckDB warehouse from the immutable raw layer.

Reads every tournament folder under data/raw/ and produces five tables, each
keyed by tournament_id so tournaments never collide:

    tournaments   one row per ingested tournament
    rounds        preliminary rounds only
    motions       one motion per preliminary round
    teams         team identity (never speakers)
    debate_teams  THE GRAIN: one row per team per debate

Two ingestion adapters feed this, and it handles both:

    ballots/        the REST API adapter  (scripts/ingest_all.py)
    page_results/   the public-pages adapter (motionlab.ingestion.pages)

Everything downstream sees one schema and cannot tell which adapter a
tournament arrived through. team_id is a string in both paths -- the API gives
integer ids, the pages give team names -- so the two concatenate cleanly.

Re-runnable: tables are replaced, the raw layer is never touched.
"""

import argparse
import json
import re
from pathlib import Path

import duckdb
import pandas as pd

from motionlab.ingestion.raw import read_raw

RAW = Path("data/raw")
DB_PATH = Path("data/motionlab.duckdb")
SIDES = ("og", "oo", "cg", "co")


def url_id(url: str) -> str:
    """Last path segment of a Tabbycat URL.

    Note: Tabbycat's round URLs carry the round's *seq*, not its database id
    (round 1 has id 16695 but URL .../rounds/1). Verified against live data.
    """
    return url.rstrip("/").split("/")[-1]


def team_key(name: str) -> str:
    """Stable id for a page-sourced team, from its display name."""
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    """A DataFrame with fixed columns, so empty inputs still concatenate."""
    return pd.DataFrame(rows, columns=columns)


# --- adapter 1: the REST API -------------------------------------------------

def read_api_tournament(folder: Path, tid: str) -> dict[str, pd.DataFrame]:
    meta = read_raw(folder / "tournament.json")
    meta = meta[0] if isinstance(meta, list) else meta

    rounds = [
        {"tournament_id": tid, "round_seq": r["seq"], "round_name": r["name"], "stage": "P"}
        for r in read_raw(folder / "rounds.json")
        if r.get("stage") == "P"
    ]
    prelims = {r["round_seq"] for r in rounds}

    motions = [
        {
            "tournament_id": tid,
            "motion_id": str(m["id"]),
            "round_seq": int(url_id(m["rounds"][0]["round"])),
            "motion_text": (m.get("text") or "").strip(),
            "info_slide": (m.get("info_slide_plain") or m.get("info_slide") or "").strip(),
            "reference": (m.get("reference") or "").strip(),
        }
        for m in read_raw(folder / "motions.json")
        if m.get("rounds")  # unattached test motions are dropped here
    ]
    motions = [m for m in motions if m["round_seq"] in prelims]

    teams = [
        {
            "tournament_id": tid,
            "team_id": str(t["id"]),
            "team_name": t.get("long_name") or t.get("short_name") or str(t["id"]),
            "institution_id": url_id(t["institution"]) if t.get("institution") else None,
        }
        for t in read_raw(folder / "teams.json")
    ]

    results = []
    for ballot_file in sorted((folder / "ballots").glob("*/*.json")):
        ballots = read_raw(ballot_file)
        if not ballots:
            continue
        sheets = ballots[0].get("result", {}).get("sheets", [])
        if not sheets:
            continue
        seq = int(ballot_file.parent.name.removeprefix("round_"))
        if seq not in prelims:
            continue
        for team in sheets[0].get("teams", []):
            if team.get("points") is None or team.get("side") not in SIDES:
                continue
            results.append({
                "tournament_id": tid,
                "debate_id": ballot_file.stem,
                "round_seq": seq,
                "team_id": str(url_id(team["team"])),
                "side": team["side"],
                "team_points": int(team["points"]),
            })

    tournaments = [{
        "tournament_id": tid,
        "title": meta.get("short_name") or meta.get("name") or tid,
        "source": "api",
        "n_rounds": len(rounds),
        "n_teams": len(teams),
    }]
    return _assemble(tournaments, rounds, motions, teams, results)


# --- adapter 2: the public results pages -------------------------------------

def read_page_tournament(folder: Path, tid: str) -> dict[str, pd.DataFrame]:
    meta_rounds = read_raw(folder / "page_rounds.json")
    prelims = {r["seq"] for r in meta_rounds if r.get("stage") == "P"}

    rounds = [
        {"tournament_id": tid, "round_seq": r["seq"], "round_name": r["name"], "stage": "P"}
        for r in meta_rounds if r["seq"] in prelims
    ]

    results, names = [], set()
    for path in sorted((folder / "page_results").glob("round_*.json")):
        seq = int(path.stem.removeprefix("round_"))
        if seq not in prelims:
            continue
        for row in read_raw(path):
            key = team_key(row["team_name"])
            names.add((key, row["team_name"]))
            results.append({
                "tournament_id": tid,
                "debate_id": str(row["debate_key"]),
                "round_seq": seq,
                "team_id": key,
                "side": row["side"],
                "team_points": int(row["team_points"]),
            })

    teams = [
        {"tournament_id": tid, "team_id": key, "team_name": name, "institution_id": None}
        for key, name in sorted(names)
    ]

    # Motions may be missing entirely if the motions page could not be read;
    # every prelim round still gets a row so joins downstream never drop rooms.
    parsed = {m["round_seq"]: m for m in read_raw(folder / "page_motions.json")
              if m.get("round_seq") is not None}
    motions = [
        {
            "tournament_id": tid,
            "motion_id": f"r{seq}",
            "round_seq": seq,
            "motion_text": (parsed.get(seq, {}).get("motion_text") or "").strip(),
            "info_slide": (parsed.get(seq, {}).get("info_slide") or "").strip(),
            "reference": "",
        }
        for seq in sorted(prelims)
    ]

    title = ""
    title_path = folder / "page_tournament.json"
    if title_path.exists():
        title = (read_raw(title_path) or {}).get("title") or ""

    tournaments = [{
        "tournament_id": tid,
        "title": title or tid,
        "source": "pages",
        "n_rounds": len(rounds),
        "n_teams": len(teams),
    }]
    return _assemble(tournaments, rounds, motions, teams, results)


def _assemble(tournaments, rounds, motions, teams, results) -> dict[str, pd.DataFrame]:
    return {
        "tournaments": _frame(tournaments,
                              ["tournament_id", "title", "source", "n_rounds", "n_teams"]),
        "rounds": _frame(rounds, ["tournament_id", "round_seq", "round_name", "stage"]),
        "motions": _frame(motions, ["tournament_id", "motion_id", "round_seq",
                                    "motion_text", "info_slide", "reference"]),
        "teams": _frame(teams, ["tournament_id", "team_id", "team_name", "institution_id"]),
        "debate_teams": _frame(results, ["tournament_id", "debate_id", "round_seq",
                                         "team_id", "side", "team_points"]),
    }


def read_tournament(folder: Path) -> dict[str, pd.DataFrame] | None:
    """Dispatch to whichever adapter produced this folder."""
    tid = folder.name
    if (folder / "ballots").exists() and (folder / "rounds.json").exists():
        return read_api_tournament(folder, tid)
    if (folder / "page_results").exists() and (folder / "page_rounds.json").exists():
        return read_page_tournament(folder, tid)
    return None


def build() -> None:
    folders = sorted(p for p in RAW.iterdir() if p.is_dir())
    collected: dict[str, list[pd.DataFrame]] = {}

    for folder in folders:
        try:
            tables = read_tournament(folder)
        except Exception as exc:
            print(f"  {folder.name:<28} SKIPPED ({exc.__class__.__name__}: {exc})")
            continue
        if tables is None:
            print(f"  {folder.name:<28} SKIPPED (no recognised adapter output)")
            continue

        n = len(tables["debate_teams"])
        source = tables["tournaments"].iloc[0]["source"]
        print(f"  {folder.name:<28}{source:>7}{n:>8} team-results")
        if n == 0:
            continue
        for name, df in tables.items():
            collected.setdefault(name, []).append(df)

    if not collected:
        raise SystemExit("Nothing to build. Run an ingestion script first.")

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

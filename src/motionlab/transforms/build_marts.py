"""Run the SQL layers in order, then export the JSON the website reads.

The layer list is explicit rather than a glob over sql/. Order matters --
marts read intermediate tables -- and an explicit list also means a stale or
half-written .sql file sitting in the folder cannot silently join the build.

The website never touches DuckDB. It reads the files written here. That
separation is what lets the warehouse move to BigQuery later without the
front end changing at all.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from motionlab.features.extract import PROMPT_VERSION, cache_key, load_cache

DB_PATH = Path("data/motionlab.duckdb")
SITE_DATA = Path("docs/data")

LAYERS = [
    Path("sql/intermediate/int_team_strength.sql"),
    Path("sql/intermediate/int_debate_residuals.sql"),
    Path("sql/marts/mart_motion_balance.sql"),
    Path("sql/marts/mart_tournament_balance.sql"),
]


def records(con, table: str) -> list[dict]:
    """A DuckDB table as plain JSON-safe dicts (via pandas, to drop numpy types)."""
    return json.loads(con.sql(f"SELECT * FROM {table}").df().to_json(orient="records"))


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()

    con = duckdb.connect(str(DB_PATH))
    for path in LAYERS:
        con.execute(path.read_text())
        print(f"ran {path}")

    motions = records(con, "mart_motion_balance")
    tournaments = records(con, "mart_tournament_balance")

    # attach LLM-derived features where we have them; missing is allowed and
    # the site degrades to "unclassified" rather than breaking.
    cache = load_cache()
    hits = 0
    for row in motions:
        entry = cache.get(cache_key(row["motion_text"] or "", row["info_slide"] or ""))
        row["features"] = entry["features"] if entry else None
        hits += entry is not None

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_tournaments": len(tournaments),
        "n_motions": len(motions),
        "n_rooms": int(sum(t["n_rooms"] for t in tournaments)),
        "n_team_results": int(sum(t["n_team_results"] for t in tournaments)),
        "feature_coverage": round(hits / len(motions), 3) if motions else 0,
        "prompt_version": PROMPT_VERSION,
    }

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "motions.json").write_text(json.dumps(motions, indent=1))
    (SITE_DATA / "tournaments.json").write_text(json.dumps(tournaments, indent=1))
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, indent=1))
    con.close()

    print()
    print(f"{meta['n_tournaments']} tournaments · {meta['n_motions']} motions · "
          f"{meta['n_rooms']} rooms · features on {hits}/{len(motions)} motions")
    print(f"exported -> {SITE_DATA}/")


if __name__ == "__main__":
    main()

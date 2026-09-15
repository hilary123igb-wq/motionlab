"""Run SQL mart files against the warehouse and export JSON for the website."""

import json
from pathlib import Path

import duckdb

DB_PATH = Path("data/motionlab.duckdb")
SQL_DIR = Path("sql/marts")
SITE_DATA = Path("site/data")


def main() -> None:
    con = duckdb.connect(str(DB_PATH))

    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        con.execute(sql_file.read_text())
        print(f"ran {sql_file.name}")

    rows = con.sql("SELECT * FROM mart_motion_position_stats").df().to_dict("records")
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "motions.json").write_text(json.dumps(rows, indent=2, default=str))
    print(f"exported {len(rows)} motions -> {SITE_DATA / 'motions.json'}")

    con.close()


if __name__ == "__main__":
    main()
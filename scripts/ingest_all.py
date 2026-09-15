"""Ingest every tournament in the registry into the raw layer.

    python scripts/ingest_all.py                       # everything in config
    python scripts/ingest_all.py --only wudc_2022      # one entry

Safe to re-run: responses already on disk are never re-fetched.
"""

import argparse
from concurrent import futures
from pathlib import Path

import yaml

from motionlab.ingestion.raw import fetch_and_cache, fetch_json

RAW_ROOT = Path("data/raw")
CONFIG = Path("config/tournaments.yaml")


def scrub_teams(teams):
    """Drop speaker records. MotionLab never persists personal data."""
    for team in teams:
        team.pop("speakers", None)
    return teams


def scrub_ballots(ballots):
    """Drop per-speaker speech scores; team points are all we need."""
    for ballot in ballots:
        for sheet in ballot.get("result", {}).get("sheets", []):
            for team in sheet.get("teams", []):
                team.pop("speeches", None)
    return ballots


def discover_slugs(base_url: str) -> list[str]:
    root = fetch_json(f"{base_url.rstrip('/')}/api")
    v1 = root.get("_links", {}).get("v1")
    return [t["slug"] for t in fetch_json(f"{v1}/tournaments") if t.get("slug")]


def ingest_one(entry: dict, slug: str, name: str, workers: int) -> dict:
    """Ingest one tournament's preliminary rounds. Returns a summary."""
    api = f"{entry['base_url'].rstrip('/')}/api/v1/tournaments/{slug}"
    out = RAW_ROOT / name

    fetch_and_cache(api, out / "tournament.json")
    rounds = fetch_and_cache(f"{api}/rounds", out / "rounds.json")
    fetch_and_cache(f"{api}/motions", out / "motions.json")
    fetch_and_cache(f"{api}/institutions", out / "institutions.json")
    fetch_and_cache(f"{api}/teams", out / "teams.json", scrub_teams)

    prelims = [r for r in rounds if r.get("stage") == "P"]

    jobs = []
    for rnd in prelims:
        seq = rnd["seq"]
        pairings = fetch_and_cache(
            rnd["_links"]["pairing"], out / "pairings" / f"round_{seq}.json"
        )
        for debate in pairings:
            jobs.append(
                (
                    debate["_links"]["ballots"],
                    out / "ballots" / f"round_{seq}" / f"{debate['id']}.json",
                )
            )

    done = 0
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = [
            pool.submit(fetch_and_cache, url, path, scrub_ballots) for url, path in jobs
        ]
        for task in futures.as_completed(tasks):
            task.result()
            done += 1
            if done % 100 == 0:
                print(f"      {done}/{len(jobs)} ballots")

    return {"name": name, "slug": slug, "prelims": len(prelims), "ballots": done}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--only", help="ingest just this registry entry")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    entries = yaml.safe_load(args.config.read_text())["tournaments"]
    if args.only:
        entries = [e for e in entries if e["name"] == args.only]

    summaries = []
    for entry in entries:
        slugs = [entry["slug"]] if entry.get("slug") else discover_slugs(entry["base_url"])
        for slug in slugs:
            # one host can run several tournaments; keep them in separate folders
            name = entry["name"] if len(slugs) == 1 else f"{entry['name']}__{slug}"
            print(f"  {name}  ({entry['base_url']} / {slug})")
            try:
                summaries.append(ingest_one(entry, slug, name, args.workers))
            except Exception as exc:  # a dead tournament must not kill the batch
                print(f"      FAILED: {exc.__class__.__name__}: {exc}")

    print("\n" + "-" * 52)
    for s in summaries:
        print(f"{s['name']:<26}{s['prelims']:>3} rounds{s['ballots']:>7} ballots")
    print(f"{len(summaries)} tournaments in data/raw/")


if __name__ == "__main__":
    main()

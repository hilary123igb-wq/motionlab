"""Ingest one Tabbycat tournament's preliminary rounds into the raw layer."""

import argparse
from concurrent import futures
from pathlib import Path

from motionlab.ingestion.raw import fetch_and_cache

RAW_ROOT = Path("data/raw")


def scrub_teams(teams):
    """Drop speaker names/ids — MotionLab does not persist personal data."""
    for team in teams:
        team.pop("speakers", None)
    return teams


def scrub_ballots(ballots):
    """Drop per-speaker speech scores — we only need team points."""
    for ballot in ballots:
        for sheet in ballot.get("result", {}).get("sheets", []):
            for team in sheet.get("teams", []):
                team.pop("speeches", None)
    return ballots


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="e.g. https://wudc2022.calicotab.com")
    parser.add_argument("slug", help="e.g. wudc")
    parser.add_argument("--name", required=True, help="folder name, e.g. wudc_2022")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    api = f"{args.base_url.rstrip('/')}/api/v1/tournaments/{args.slug}"
    out = RAW_ROOT / args.name

    fetch_and_cache(api, out / "tournament.json")
    rounds = fetch_and_cache(f"{api}/rounds", out / "rounds.json")
    fetch_and_cache(f"{api}/motions", out / "motions.json")
    fetch_and_cache(f"{api}/institutions", out / "institutions.json")
    fetch_and_cache(f"{api}/teams", out / "teams.json", scrub_teams)

    prelims = [r for r in rounds if r.get("stage") == "P"]
    print(f"{len(prelims)} preliminary rounds")

    jobs = []
    for rnd in prelims:
        seq = rnd["seq"]
        pairings = fetch_and_cache(
            rnd["_links"]["pairing"], out / "pairings" / f"round_{seq}.json"
        )
        print(f"  R{seq}: {len(pairings)} debates")
        for debate in pairings:
            jobs.append(
                (
                    debate["_links"]["ballots"],
                    out / "ballots" / f"round_{seq}" / f"{debate['id']}.json",
                )
            )

    print(f"fetching {len(jobs)} ballots ({args.workers} workers)...")
    done = 0
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = [pool.submit(fetch_and_cache, url, path, scrub_ballots) for url, path in jobs]
        for task in futures.as_completed(tasks):
            task.result()
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)}")
    print(f"done: {done} ballots")


if __name__ == "__main__":
    main()
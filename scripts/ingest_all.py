"""Ingest every tournament in the registry into the raw layer.

    python scripts/ingest_all.py                    # everything in config
    python scripts/ingest_all.py --only wudc_2022   # one entry
    python scripts/ingest_all.py --source pages     # force the page adapter

Two adapters, tried in order:

  api    the REST API. Rich, but needs one request per debate for results
         (~830 for a WUDC-sized tournament) and is a per-tournament setting
         most organisers never switch on.
  pages  the public results pages. One request per ROUND instead of per
         debate, and available on nearly every tab site.

Default is `auto`: use the API when a tournament exposes ballots, otherwise
fall back to pages. Both write the same raw layer, so nothing downstream
changes. Safe to re-run -- anything already on disk is never re-fetched.
"""

import argparse
from concurrent import futures
from pathlib import Path
from urllib.parse import urlparse

import yaml

from motionlab.ingestion import pages
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


def split_entry(entry: dict) -> tuple[str, str | None]:
    """A registry row may give base_url + slug, or a single tab-site url."""
    if entry.get("url"):
        parsed = urlparse(entry["url"])
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.strip("/").split("/")
        return base, (path[0] if path and path[0] else None)
    return entry["base_url"].rstrip("/"), entry.get("slug")


def discover_slugs(base_url: str) -> list[str]:
    root = fetch_json(f"{base_url}/api")
    v1 = root.get("_links", {}).get("v1")
    return [t["slug"] for t in fetch_json(f"{v1}/tournaments") if t.get("slug")]


def api_has_ballots(base: str, slug: str) -> bool:
    """Cheap check: can we actually read a result through the API?"""
    try:
        api = f"{base}/api/v1/tournaments/{slug}"
        prelims = [r for r in fetch_json(f"{api}/rounds") if r.get("stage") == "P"]
        if not prelims:
            return False
        pairings = fetch_json(prelims[0]["_links"]["pairing"])
        if not pairings:
            return False
        ballots = fetch_json(pairings[0]["_links"]["ballots"])
        return bool(ballots and ballots[0].get("result", {}).get("sheets"))
    except Exception:
        return False


def ingest_api(base: str, slug: str, out: Path, workers: int) -> dict:
    api = f"{base}/api/v1/tournaments/{slug}"

    fetch_and_cache(api, out / "tournament.json")
    rounds = fetch_and_cache(f"{api}/rounds", out / "rounds.json")
    fetch_and_cache(f"{api}/motions", out / "motions.json")
    fetch_and_cache(f"{api}/teams", out / "teams.json", scrub_teams)

    prelims = [r for r in rounds if r.get("stage") == "P"]
    jobs = []
    for rnd in prelims:
        seq = rnd["seq"]
        pairings = fetch_and_cache(rnd["_links"]["pairing"],
                                   out / "pairings" / f"round_{seq}.json")
        for debate in pairings:
            jobs.append((debate["_links"]["ballots"],
                         out / "ballots" / f"round_{seq}" / f"{debate['id']}.json"))

    done = 0
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = [pool.submit(fetch_and_cache, url, path, scrub_ballots) for url, path in jobs]
        for task in futures.as_completed(tasks):
            task.result()
            done += 1
            if done % 200 == 0:
                print(f"        {done}/{len(jobs)} ballots")
    return {"source": "api", "rounds": len(prelims), "requests": done + len(prelims) + 4}


def ingest_page(base: str, slug: str, out: Path) -> dict:
    root = f"{base}/{slug}" if slug else base
    meta = pages.fetch_metadata(root, out)
    prelims = [r["seq"] for r in meta["rounds"] if r["stage"] == "P"]
    summaries = pages.ingest_pages(root, out, prelims)
    rows = sum(s.get("rows", 0) for s in summaries)
    failed = [s for s in summaries if s.get("error")]
    for s in failed:
        print(f"        round {s['seq']}: {s['error']}")
    return {"source": "pages", "rounds": len(prelims),
            "requests": len(prelims) + 3, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--only", help="ingest just this registry entry")
    parser.add_argument("--source", choices=["auto", "api", "pages"], default="auto")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    entries = yaml.safe_load(args.config.read_text())["tournaments"]
    if args.only:
        entries = [e for e in entries if e["name"] == args.only]

    summaries = []
    for entry in entries:
        base, slug = split_entry(entry)
        try:
            slugs = [slug] if slug else discover_slugs(base)
        except Exception as exc:
            print(f"  {entry['name']:<26} no slug ({exc.__class__.__name__})")
            continue

        for s in slugs:
            name = entry["name"] if len(slugs) == 1 else f"{entry['name']}__{s}"
            out = RAW_ROOT / name

            if args.source == "api":
                use_api = True
            elif args.source == "pages":
                use_api = False
            else:
                use_api = api_has_ballots(base, s)

            print(f"  {name:<26} via {'api' if use_api else 'pages'}")
            try:
                result = ingest_api(base, s, out, args.workers) if use_api \
                    else ingest_page(base, s, out)
                summaries.append({"name": name, **result})
            except Exception as exc:
                print(f"        FAILED: {exc.__class__.__name__}: {exc}")

    print("\n" + "-" * 62)
    for s in summaries:
        print(f"{s['name']:<26}{s['source']:>7}{s['rounds']:>4} rounds"
              f"{s['requests']:>7} requests")
    print(f"{len(summaries)} tournaments in data/raw/")


if __name__ == "__main__":
    main()

"""Probe Tabbycat instances to see which expose everything MotionLab needs.

Single instance:
    python scripts/probe_api.py https://wudc2022.calicotab.com

Whole registry:
    python scripts/probe_api.py --config config/tournaments.yaml
"""

import argparse
import sys
from pathlib import Path

import httpx
import yaml

TIMEOUT_SECONDS = 15.0
USER_AGENT = "MotionLab/0.1 (debate analytics research; +github.com/hilary123igb-wq/motionlab)"


def get_json(url: str, quiet: bool = False) -> dict | list | None:
    """GET a URL and return parsed JSON, or None if it isn't publicly readable."""
    try:
        response = httpx.get(
            url,
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    except httpx.RequestError as exc:
        if not quiet:
            print(f"  ! unreachable ({exc.__class__.__name__})")
        return None

    if response.status_code != 200:
        if not quiet:
            print(f"  ! HTTP {response.status_code}")
        return None

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        if not quiet:
            print(f"  ! not JSON ({content_type})")
        return None

    return response.json()


def check_tournament(v1: str, slug: str, quiet: bool = False) -> dict:
    """Walk down to a single ballot. Returns a verdict dict."""
    out = {"slug": slug, "usable": False, "reason": "", "prelims": 0, "rooms": 0, "motions": 0}

    rounds = get_json(f"{v1}/tournaments/{slug}/rounds", quiet)
    if rounds is None:
        out["reason"] = "rounds not readable"
        return out

    prelims = [r for r in rounds if r.get("stage") == "P"]
    out["prelims"] = len(prelims)
    if not prelims:
        out["reason"] = "no preliminary rounds"
        return out

    motions = get_json(f"{v1}/tournaments/{slug}/motions", quiet)
    out["motions"] = len(motions) if motions is not None else 0

    seq = prelims[0].get("seq")
    pairings = get_json(f"{v1}/tournaments/{slug}/rounds/{seq}/pairings", quiet)
    if not pairings:
        out["reason"] = "pairings not readable"
        return out
    out["rooms"] = len(pairings)

    ballots_url = pairings[0].get("_links", {}).get("ballots")
    if ballots_url is None:
        out["reason"] = "no ballots link"
        return out

    ballots = get_json(ballots_url, quiet)
    if not ballots:
        out["reason"] = "ballots not readable"
        return out

    sheets = ballots[0].get("result", {}).get("sheets", [])
    if not sheets:
        out["reason"] = "ballots have no result sheets"
        return out

    points = [t.get("points") for t in sheets[0].get("teams", [])]
    if sorted(p for p in points if p is not None) != [0, 1, 2, 3]:
        out["reason"] = f"unexpected points {points}"
        return out

    out["usable"] = True
    return out


def probe(base_url: str, slug: str | None = None, quiet: bool = False) -> list[dict]:
    """Probe one instance. Returns one verdict per tournament found."""
    base = base_url.rstrip("/")

    root = get_json(f"{base}/api", quiet)
    if root is None:
        return [{"slug": "-", "usable": False, "reason": "no API at this address",
                 "prelims": 0, "rooms": 0, "motions": 0}]

    v1 = root.get("_links", {}).get("v1")
    if v1 is None:
        return [{"slug": "-", "usable": False, "reason": "no v1 link",
                 "prelims": 0, "rooms": 0, "motions": 0}]

    if slug:
        return [check_tournament(v1, slug, quiet)]

    tournaments = get_json(f"{v1}/tournaments", quiet)
    if tournaments is None:
        return [{"slug": "-", "usable": False, "reason": "tournament list not public",
                 "prelims": 0, "rooms": 0, "motions": 0}]

    return [check_tournament(v1, t["slug"], quiet)
            for t in tournaments if t.get("slug")]


def run_registry(config_path: Path) -> None:
    entries = yaml.safe_load(config_path.read_text())["tournaments"]
    print(f"{'name':<18}{'slug':<14}{'prelims':>8}{'rooms':>7}{'motions':>9}  verdict")
    print("-" * 78)

    usable = 0
    for entry in entries:
        for v in probe(entry["base_url"], entry.get("slug"), quiet=True):
            verdict = "USABLE" if v["usable"] else f"skip — {v['reason']}"
            usable += v["usable"]
            print(f"{entry['name']:<18}{v['slug']:<14}"
                  f"{v['prelims']:>8}{v['rooms']:>7}{v['motions']:>9}  {verdict}")

    print("-" * 78)
    print(f"{usable} usable of {len(entries)} configured")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Tabbycat public APIs.")
    parser.add_argument("base_url", nargs="?", help="e.g. https://wudc2022.calicotab.com")
    parser.add_argument("--slug", help="skip discovery and check this slug only")
    parser.add_argument("--config", type=Path, help="probe every entry in a registry YAML")
    args = parser.parse_args()

    if args.config:
        run_registry(args.config)
        return

    if not args.base_url:
        parser.error("give a base_url or --config")

    print(f"=== {args.base_url} ===")
    results = probe(args.base_url, args.slug)
    for v in results:
        print(f"  {v['slug']:<14} prelims={v['prelims']:<3} rooms={v['rooms']:<4} "
              f"motions={v['motions']:<4} "
              f"{'USABLE' if v['usable'] else 'skip — ' + v['reason']}")
    sys.exit(0 if any(v["usable"] for v in results) else 1)


if __name__ == "__main__":
    main()

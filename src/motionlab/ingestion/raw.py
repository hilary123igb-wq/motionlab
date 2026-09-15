"""The raw layer: fetch once, store exactly what came back, never overwrite.

Every response is wrapped in a small envelope recording where it came from and
when. That is data lineage: six months from now, looking at a file, you can say
which URL produced it and on what date.

Re-running is free. `fetch_and_cache` checks the filesystem before the network,
so an interrupted run resumes instead of starting over, and a rebuild of the
warehouse never touches a volunteer-run server again.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

TIMEOUT_SECONDS = 30.0
USER_AGENT = "MotionLab/0.1 (debate analytics research; +github.com/hilary123igb-wq/motionlab)"


def fetch_json(url: str) -> dict | list:
    """GET a URL and return parsed JSON. Raises on any failure.

    Deliberately the opposite contract to scripts/probe_api.py, which returns
    None: there, a missing endpoint is the answer to a question; here it is a
    failure that must stop the run.
    """
    response = httpx.get(
        url,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def save_raw(path: Path, url: str, payload) -> None:
    """Write a payload into the raw layer with its lineage envelope."""
    document = {
        "_motionlab": {
            "source_url": url,
            "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "data": payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2))


def read_raw(path: Path):
    """Unwrap a raw file, returning just the payload."""
    return json.loads(path.read_text())["data"]


def fetch_and_cache(url: str, path: Path, scrub=None) -> dict | list:
    """Return the payload at `url`, fetching it only if not already on disk.

    `scrub` runs before anything is written, which is how personal data is kept
    out of the raw layer entirely rather than merely ignored later.
    """
    if path.exists():
        return read_raw(path)

    payload = fetch_json(url)
    if scrub is not None:
        payload = scrub(payload)
    save_raw(path, url, payload)
    return payload

"""Raw layer: fetch once, store exactly as received, never overwrite."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

TIMEOUT_SECONDS = 30.0
USER_AGENT = "MotionLab/0.1 (debate analytics research; +github.com/hilary123igb-wq/motionlab)"


def fetch_json(url: str) -> dict | list:
    """GET a URL and return parsed JSON. Raises on any failure."""
    response = httpx.get(
        url,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def fetch_and_cache(url: str, path: Path, scrub=None) -> dict | list:
    """Return the payload at `url`, fetching it only if not already on disk."""
    if path.exists():
        return json.loads(path.read_text())["data"]

    payload = fetch_json(url)
    if scrub is not None:
        payload = scrub(payload)

    document = {
        "_motionlab": {
            "source_url": url,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        },
        "data": payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2))
    return payload
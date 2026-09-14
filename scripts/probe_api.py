"""Probe a Tabbycat instance to see what its public API exposes."""

import argparse
import sys

import httpx

TIMEOUT_SECONDS = 10.0
USER_AGENT = "MotionLab/0.1 (debate analytics research; +github.com/hilary123igb-wq/motionlab)"


def probe(base_url: str) -> None:
    url = base_url.rstrip("/") + "/api"
    print(f"GET {url}")

    try:
        response = httpx.get(
            url,
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    except httpx.RequestError as exc:
        print(f"FAILED: could not reach the server ({exc.__class__.__name__}: {exc})")
        sys.exit(1)

    print(f"Status:       {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")

    if response.status_code != 200:
        print("FAILED: no usable API at this address.")
        sys.exit(1)

    payload = response.json()
    print(f"Version:      {payload.get('version')} ({payload.get('version_name')})")
    print(f"Timezone:     {payload.get('timezone')}")
    print(f"Links:        {payload.get('_links')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a Tabbycat public API.")
    parser.add_argument("base_url", help="e.g. https://wudc2022.calicotab.com")
    probe(parser.parse_args().base_url)


if __name__ == "__main__":
    main()
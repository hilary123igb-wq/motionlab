"""Turn motion text into structured features, once, and remember the answer.

Design notes worth knowing before you change anything here:

* Provider-neutral. The call sits behind one function, chosen by the
  MOTIONLAB_LLM_PROVIDER environment variable. Nothing else in MotionLab knows
  or cares which model produced a feature row.

* Cached on disk, keyed by (prompt version, hash of the text). An LLM is a
  non-deterministic, paid, rate-limited dependency; calling it twice for the
  same motion is waste, and calling it twice for the same motion during an
  analysis run makes results irreproducible. The cache is the fix for both.

* Validated. Whatever comes back is parsed into MotionFeatures or rejected.
  A model that returns prose, or invents a topic, fails loudly here rather
  than putting a null into a feature column three layers downstream.

Usage:
    python -m motionlab.features.extract          # fill gaps for every motion
    python -m motionlab.features.extract --dry    # just report what is missing
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

import duckdb

from motionlab.features.schema import PROMPT_VERSION, SYSTEM_PROMPT, MotionFeatures

CACHE_DIR = Path("llm_cache/motions")
DB_PATH = Path("data/motionlab.duckdb")


def cache_key(text: str, info_slide: str = "") -> str:
    """Stable id for a motion's text. Identical motions across tournaments
    share one cache entry, which is free deduplication."""
    blob = f"{PROMPT_VERSION}\n{text.strip()}\n{info_slide.strip()}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def load_cache() -> dict[str, dict]:
    """Every cached extraction, keyed by cache_key."""
    if not CACHE_DIR.exists():
        return {}
    out = {}
    for path in CACHE_DIR.glob("*.json"):
        record = json.loads(path.read_text())
        out[path.stem] = record
    return out


def _call_llm(text: str, info_slide: str) -> dict:
    """The one place a provider is named. Everything else is provider-neutral."""
    provider = os.environ.get("MOTIONLAB_LLM_PROVIDER", "anthropic")
    user = f"MOTION:\n{text}"
    if info_slide:
        user += f"\n\nINFO SLIDE:\n{info_slide}"

    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        message = client.messages.create(
            model=os.environ.get("MOTIONLAB_LLM_MODEL", "claude-sonnet-4-5"),
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        raw = message.content[0].text
    elif provider == "openai":
        from openai import OpenAI

        client = OpenAI()  # reads OPENAI_API_KEY
        response = client.chat.completions.create(
            model=os.environ.get("MOTIONLAB_LLM_MODEL", "gpt-4o-mini"),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        )
        raw = response.choices[0].message.content
    else:
        raise ValueError(f"unknown MOTIONLAB_LLM_PROVIDER: {provider}")

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {raw[:200]}")
    return json.loads(raw[start : end + 1])


def extract(text: str, info_slide: str = "") -> MotionFeatures:
    """Features for one motion, from cache when possible."""
    key = cache_key(text, info_slide)
    path = cache_path(key)
    if path.exists():
        return MotionFeatures(**json.loads(path.read_text())["features"])

    features = MotionFeatures(**_call_llm(text, info_slide))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "prompt_version": PROMPT_VERSION,
                "provider": os.environ.get("MOTIONLAB_LLM_PROVIDER", "anthropic"),
                "model": os.environ.get("MOTIONLAB_LLM_MODEL", "claude-sonnet-4-5"),
                "motion_text": text,
                "features": features.model_dump(),
            },
            indent=2,
        )
    )
    return features


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry", action="store_true", help="report gaps, call nothing")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    motions = con.sql(
        "SELECT DISTINCT motion_text, info_slide FROM motions WHERE motion_text <> ''"
    ).fetchall()
    con.close()

    missing = [(t, i) for t, i in motions if not cache_path(cache_key(t, i)).exists()]
    print(f"{len(motions)} distinct motions, {len(missing)} not yet extracted")

    if args.dry or not missing:
        return

    for i, (text, info) in enumerate(missing, 1):
        try:
            extract(text, info)
            print(f"  [{i}/{len(missing)}] ok  {text[:60]}…")
        except Exception as exc:
            print(f"  [{i}/{len(missing)}] FAIL {exc.__class__.__name__}: {exc}")


if __name__ == "__main__":
    main()

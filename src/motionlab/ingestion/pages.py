"""Second ingestion adapter: Tabbycat's public results PAGES.

Why this exists
---------------
Tabbycat's REST API is a per-tournament setting that most organisers never
switch on, so the API adapter reaches only a small slice of historical
tournaments. The public results pages, by contrast, are the entire point of a
tab site and are almost always up.

Tabbycat renders those pages with a Vue table whose data is embedded in the
page as a JSON literal after `tablesData:`. Reading that is far more stable
than scraping rendered HTML, because it is the same structured payload the
table itself binds to.

It is also much cheaper. The API needs one request per debate to get results
(~830 for a tournament the size of WUDC); a results page carries a whole
round at once, so the same tournament costs about ten requests.

What this adapter emits
-----------------------
The same standard shape the API adapter produces, written into the same raw
layer, so everything downstream is unchanged:

    data/raw/<name>/page_results/round_<seq>.json

Both adapters land on one schema; nothing after ingestion knows or cares which
one a tournament came through. That is the whole point of an adapter.

No personal data is read or stored: team identity only, never speakers.
"""

import json
import re
from pathlib import Path

import httpx

from motionlab.ingestion.raw import USER_AGENT, save_raw

TIMEOUT_SECONDS = 40.0
SIDES = {"og": "og", "oo": "oo", "cg": "cg", "co": "co"}
SIDE_WORDS = {
    "opening government": "og", "opening gov": "og",
    "opening opposition": "oo", "opening opp": "oo",
    "closing government": "cg", "closing gov": "cg",
    "closing opposition": "co", "closing opp": "co",
}
TAG = re.compile(r"<[^>]+>")
DEBATE_LINK = re.compile(r"/debate/(\d+)/")
ROUND_LINK = re.compile(r"/results/round/(\d+)/")


def get_page(url: str) -> str:
    """One GET, with a single retry -- some tab sites are old and flaky."""
    headers = {"User-Agent": USER_AGENT}
    try:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS, headers=headers,
                             follow_redirects=True)
    except httpx.TransportError:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS, headers=headers,
                             follow_redirects=True)
    response.raise_for_status()
    return response.text


def strip_tags(value) -> str:
    return TAG.sub("", str(value or "")).strip()


def tables_data(page: str) -> list | None:
    """Pull the embedded `tablesData: [...]` literal out of a Tabbycat page.

    raw_decode reads one JSON value and stops, which is what we need: the
    literal is followed by more JavaScript, so json.loads on the rest fails.
    """
    marker = page.find("tablesData:")
    if marker < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(page[marker + len("tablesData:"):].lstrip())
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def _column_index(headers: list[str], *names: str) -> int | None:
    for name in names:
        if name in headers:
            return headers.index(name)
    return None


def _cell_text(cell) -> str:
    return strip_tags(cell.get("text") if isinstance(cell, dict) else cell)


def _normalise_side(raw: str) -> str | None:
    value = raw.strip().lower()
    if value in SIDES:
        return SIDES[value]
    return SIDE_WORDS.get(value)


def parse_results_page(page: str) -> list[dict]:
    """One round's results page -> one row per team per debate.

    Returns rows of {debate_key, team_name, side, rank, team_points}. Rows we
    cannot read are skipped rather than guessed at; the caller checks whether
    enough survived to trust the round.
    """
    tables = tables_data(page)
    if not tables:
        return []

    rows: list[dict] = []
    for table in tables:
        headers = [str(h.get("key") or h.get("title") or "").lower()
                   for h in table.get("head", [])]
        i_team = _column_index(headers, "team")
        i_result = _column_index(headers, "result")
        i_side = _column_index(headers, "side")
        i_ballot = _column_index(headers, "ballot")
        if i_team is None or i_result is None or i_side is None:
            continue  # not a results table

        for row in table.get("data", []):
            try:
                result_cell = row[i_result] if isinstance(row[i_result], dict) else {}
                rank = result_cell.get("sort")
                side = _normalise_side(_cell_text(row[i_side]))
                if rank is None or side is None:
                    continue

                debate_key = None
                if i_ballot is not None and isinstance(row[i_ballot], dict):
                    match = DEBATE_LINK.search(row[i_ballot].get("link") or "")
                    if match:
                        debate_key = int(match.group(1))
                if debate_key is None:
                    continue  # without a room key we cannot group; drop the row

                rank = int(rank)
                if not 1 <= rank <= 4:
                    continue

                rows.append({
                    "debate_key": debate_key,
                    "team_name": _cell_text(row[i_team]),
                    "side": side,
                    "rank": rank,
                    # BP: 1st = 3 points ... 4th = 0
                    "team_points": 4 - rank,
                })
            except (IndexError, TypeError, ValueError):
                continue
    return rows


def complete_rooms(rows: list[dict]) -> list[dict]:
    """Keep only rooms with exactly four teams, one per side.

    A half-parsed room would quietly skew every average built on it, so a room
    is either whole or excluded. build_db and the data-quality tests then never
    have to cope with partial rooms.
    """
    by_room: dict[int, list[dict]] = {}
    for row in rows:
        by_room.setdefault(row["debate_key"], []).append(row)

    keep = []
    for room in by_room.values():
        if len(room) != 4:
            continue
        if {r["side"] for r in room} != {"og", "oo", "cg", "co"}:
            continue
        if sorted(r["rank"] for r in room) != [1, 2, 3, 4]:
            continue
        keep.extend(room)
    return keep


def discover_round_seqs(root: str) -> list[int]:
    """Round numbers, read from the links on the results index page."""
    page = get_page(f"{root}/results/")
    return sorted({int(s) for s in ROUND_LINK.findall(page)})


def fetch_round(root: str, seq: int, out_dir: Path) -> dict:
    """Fetch and normalise one round; cached, so re-runs cost nothing."""
    path = out_dir / "page_results" / f"round_{seq}.json"
    if path.exists():
        payload = json.loads(path.read_text())["data"]
        return {"seq": seq, "rows": len(payload), "cached": True}

    url = f"{root}/results/round/{seq}/"
    rows = complete_rooms(parse_results_page(get_page(url)))
    save_raw(path, url, rows)
    return {"seq": seq, "rows": len(rows), "cached": False}


def ingest_pages(root: str, out_dir: Path, seqs: list[int] | None = None) -> list[dict]:
    """Ingest every readable round of a tournament from its public pages."""
    seqs = seqs or discover_round_seqs(root)
    summaries = []
    for seq in seqs:
        try:
            summaries.append(fetch_round(root, seq, out_dir))
        except Exception as exc:
            summaries.append({"seq": seq, "rows": 0, "error": f"{exc.__class__.__name__}: {exc}"})
    return summaries

# --- tournament metadata from public pages -----------------------------------
# Used when the REST API is switched off, which is the usual case.

ROUND_LINK_NAMED = re.compile(
    r'href="[^"]*?/results/round/(\d+)/"[^>]*>\s*([^<]{1,60}?)\s*<', re.IGNORECASE
)
TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
PRELIM_NAME = re.compile(r"^(round|r)\s*\d+$", re.IGNORECASE)


def discover_rounds(root: str) -> list[dict]:
    """Rounds from the results index: seq, name, and a guess at the stage.

    Without the API there is no authoritative stage flag, so the stage is
    inferred from the round's name: "Round 7" is preliminary, "Quarterfinals"
    is not. Anything unrecognised is treated as elimination and excluded --
    wrongly dropping a round costs us data, wrongly keeping an elimination
    round corrupts every average built on it.
    """
    page = get_page(f"{root}/results/")
    seen: dict[int, str] = {}
    for seq, name in ROUND_LINK_NAMED.findall(page):
        seen.setdefault(int(seq), strip_tags(name))
    for seq in ROUND_LINK.findall(page):
        seen.setdefault(int(seq), f"Round {seq}")
    return [
        {"seq": seq, "name": name,
         "stage": "P" if PRELIM_NAME.match(name or "") else "E"}
        for seq, name in sorted(seen.items())
    ]


def tournament_title(root: str) -> str:
    """Best-effort human name, from the results index page title."""
    try:
        match = TITLE.search(get_page(f"{root}/results/"))
    except Exception:
        return ""
    if not match:
        return ""
    text = strip_tags(match.group(1))
    # Tabbycat titles look like "WUDC Belgrade 2022 | Results"
    return text.split("|")[0].strip()


def parse_motions_page(page: str) -> list[dict]:
    """The public motions page -> one row per round with its motion text."""
    tables = tables_data(page)
    if not tables:
        return []
    out = []
    for table in tables:
        headers = [str(h.get("key") or h.get("title") or "").lower()
                   for h in table.get("head", [])]
        i_round = _column_index(headers, "round", "rd")
        i_motion = _column_index(headers, "motion", "text")
        i_info = _column_index(headers, "info slide", "info_slide", "infoslide")
        if i_motion is None:
            continue
        for row in table.get("data", []):
            try:
                text = _cell_text(row[i_motion])
                if not text:
                    continue
                seq = None
                if i_round is not None:
                    digits = re.search(r"(\d+)", _cell_text(row[i_round]))
                    if digits:
                        seq = int(digits.group(1))
                out.append({
                    "round_seq": seq,
                    "motion_text": text,
                    "info_slide": _cell_text(row[i_info]) if i_info is not None else "",
                })
            except (IndexError, TypeError):
                continue
    return out


def fetch_metadata(root: str, out_dir: Path) -> dict:
    """Rounds, motions and a title, from pages. Cached like everything else."""
    rounds_path = out_dir / "page_rounds.json"
    if rounds_path.exists():
        rounds = json.loads(rounds_path.read_text())["data"]
    else:
        rounds = discover_rounds(root)
        save_raw(rounds_path, f"{root}/results/", rounds)

    title_path = out_dir / "page_tournament.json"
    if title_path.exists():
        title = json.loads(title_path.read_text())["data"].get("title", "")
    else:
        title = tournament_title(root)
        save_raw(title_path, f"{root}/results/", {"title": title})

    motions_path = out_dir / "page_motions.json"
    if motions_path.exists():
        motions = json.loads(motions_path.read_text())["data"]
    else:
        try:
            motions = parse_motions_page(get_page(f"{root}/motions/"))
        except Exception:
            motions = []          # motion text is optional; balance still works
        save_raw(motions_path, f"{root}/motions/", motions)

    return {"title": title, "rounds": rounds, "motions": motions}

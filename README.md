# MotionLab

**Does a debate motion favour a position — or did stronger teams just happen to sit there?**

MotionLab ingests results from public [Tabbycat](https://github.com/TabbycatDebate/tabbycat)
tournament sites, models them into an analytical warehouse, and measures how far each
British Parliamentary position drifted from a balanced result on every motion — before and
after accounting for how strong the teams in each room already were.

**Live site:** https://hilary123igb-wq.github.io/motionlab/

---

## The problem

British Parliamentary debating puts four teams in a room:

| | Government | Opposition |
|---|---|---|
| **Opening** | OG | OO |
| **Closing** | CG | CO |

Each debate awards exactly 3 / 2 / 1 / 0 team points, so a position that is neither helped
nor hindered averages **1.50**. Anything else looks like the motion favoured someone.

The trap is that it might not be the motion at all. If three of the four strongest teams in
the room were drawn on Government, Government will out-score Opposition regardless of what
the motion said. Separating those two explanations is the entire project — everything else
is plumbing built to make that question answerable.

## What it does

```
config/tournaments.yaml       registry of public Tabbycat instances
  └─ probe                    does this one actually expose ballots?
      ├─ API adapter          REST: rich, but ~1 request per DEBATE
      └─ page adapter         public results pages: ~1 request per ROUND
           └─ raw layer       JSON to disk, one file per response, never re-fetched
                └─ DuckDB     tournaments · rounds · motions · teams · debate_teams
                     ├─ int_team_strength     points a team held BEFORE each round
                     ├─ int_debate_residuals  result vs what that ranking implied
                     └─ mart_motion_balance   one row per motion, raw and adjusted
                          ├─ LLM feature layer  motion text → structured columns
                          └─ JSON export        what the static site reads
```

## Quick start

```bash
git clone https://github.com/hilary123igb-wq/motionlab
cd motionlab

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

python scripts/probe_api.py --config config/tournaments.yaml   # which are usable?
python scripts/ingest_all.py                                   # API where possible, pages otherwise
python -m motionlab.transforms.build_db                        # raw JSON → DuckDB
python -m motionlab.transforms.build_marts                     # SQL layers → JSON export
pytest -q                                                      # data quality checks

python -m http.server 8000 --directory docs                    # http://localhost:8000
```

Re-running is safe at every stage. Ingestion skips anything already on disk; the warehouse
and marts are rebuilt from scratch each time and never modify the raw layer.

---

## How the measurement works

### Two ingestion adapters

Tabbycat's REST API is a per-tournament setting that most organisers never switch on, so an
API-only pipeline reaches a small slice of historical tournaments. The public **results
pages** are on almost everywhere, because publishing results is what a tab site is for.

MotionLab therefore has two adapters behind one schema:

| | Reads | Cost for a WUDC-sized tournament | Coverage |
|---|---|---|---|
| **API** | `/api/v1/…/ballots` per debate | ~830 requests | low — opt-in per tournament |
| **Pages** | `/{slug}/results/round/{n}/` per round | **~10 requests** | high |

The page adapter reads the JSON Tabbycat embeds in its results pages after `tablesData:`,
which is the same structured payload the on-page table binds to — far more stable than
scraping rendered HTML. Each row yields team, side, result rank and a ballot link containing
the debate id, which is the room key.

`ingest_all.py` defaults to `--source auto`: it uses the API where ballots are actually
readable and falls back to pages otherwise. Both write the same raw layer and produce the
same five warehouse tables, so nothing downstream knows which adapter a tournament came
through. `team_id` is a string in both paths — the API gives integer ids, the pages give
team names — so the two concatenate cleanly.

Two deliberate conservatisms in the page adapter:

- **A room is whole or excluded.** Only rooms with exactly four teams, one per side, and
  ranks 1–4 survive parsing. A half-parsed room would quietly skew every average built on it.
- **Unrecognised rounds are treated as eliminations and dropped.** Without the API there is
  no authoritative stage flag, so it is inferred from the round's name. Wrongly dropping a
  round costs data; wrongly keeping an elimination round corrupts the results.

### Raw balance

For each debate, `Gov − Opp = (OG + CG)/2 − (OO + CO)/2`, averaged over every room that ran
the motion. Computing it **per room and then averaging**, rather than averaging positions
first, matters: it keeps the comparison inside a single room, where the four teams were
matched against each other.

### Strength adjustment

Before each round, rank the four teams in a room by the points they brought *into* it and
assign expected scores of 3 / 2 / 1 / 0. Teams on equal prior points share the places
between them — two tied for 2nd–3rd each get 1.5. Each team's **residual** is what it
actually scored minus what that ranking expected.

| | Strength rank | Expected | Position | Actual | Residual |
|---|---|---|---|---|---|
| A (9 pts) | 1st | 3.0 | OG | 1 | **−2.0** |
| B (7 pts) | =2nd | 1.5 | OO | 3 | **+1.5** |
| C (7 pts) | =2nd | 1.5 | CG | 2 | **+0.5** |
| D (4 pts) | 4th | 0.0 | CO | 0 | **0.0** |

A residual of +0.5 reads as *"finished half a place better than its record predicted"*.
Averaging residuals by position gives the adjusted figure.

### Why this doesn't leak

Two guards, and they are the reason the number means anything:

1. **Only rounds before the one being measured** contribute to strength. The SQL frame
   clause `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` enforces it. Widening it to
   `CURRENT ROW` would fold the result being explained into its own control, making every
   downstream figure circular. Using *final* standings — the obvious shortcut — is worse
   still, since they contain the outcome of every round.
2. **Ranking happens inside one room**, so bracket differences cancel. The question is only
   who was strongest of these four.

Round 1 has no prior information, so all four teams tie, every expectation is 1.5, and the
adjusted figure collapses to the raw one. That is also the only randomly drawn round, so it
needs no adjustment — the rule degrades gracefully rather than needing a special case.

### Evidence, not significance

No p-values or confidence intervals are reported. What *is* reported is sample size,
because a motion debated in 6 rooms cannot be measured as well as one debated in 91:

| Dot | Rooms |
|---|---|
| ● filled | 40 or more |
| ● grey | 15–39 |
| ○ hollow | fewer than 15 |

Hollow dots are suggestive at best.

### The motion feature layer

Motion text is unstructured prose; to be usable as model input it has to become columns. A
language model classifies each motion into topic, motion type, actor, geographic scope, how
much outside knowledge it demands and how much mechanism a team must build. Design points:

- **Validated.** Output is parsed into a Pydantic `MotionFeatures` or rejected. An invented
  topic fails at the boundary rather than becoming a null three layers downstream.
- **Cached.** Keyed by `(prompt version, hash of motion text + info slide)`. An LLM is a
  paid, rate-limited, non-deterministic dependency; calling it twice for the same motion is
  both wasteful and makes results irreproducible. Identical motions across tournaments share
  one entry, which is free deduplication.
- **Provider-neutral.** One function chooses between providers from an environment variable.
  Nothing else in the codebase knows which model produced a feature row.
- **Deliberately blind to the outcome.** The model is never asked which side it thinks is
  favoured. Doing so would smuggle the target variable into the features.

The cached extractions committed to `llm_cache/` were generated during development; to
regenerate them against a live API, set `ANTHROPIC_API_KEY` (or `MOTIONLAB_LLM_PROVIDER=openai`
and `OPENAI_API_KEY`) and run `python -m motionlab.features.extract`.

---

## Data quality

`pytest -q` asserts things about the data, not about Python functions — which is where
pipelines like this actually break. A parser that silently mismaps a position passes every
unit test and fails these:

- every debate has exactly four teams, one per position
- team points are 0–3 and sum to 6 in every room
- no team appears twice in a debate
- every foreign key resolves
- **round 1 carries no prior-round strength** — the leakage guard
- **residuals sum to zero in every room** — the expectation is well-formed

That second-to-last test is the one worth reading. If someone later widens the window frame
for convenience, this fails loudly instead of the results quietly becoming circular.

## Privacy

Speaker names and individual speech scores are stripped **at the ingestion boundary**,
before anything is written to disk — not ignored later in the analysis. MotionLab stores
team, position and result only. The research question does not need personal data, so none
is collected.

## Known limits

- **Preliminary rounds only.** Elimination rounds draw from a pre-filtered field under
  different rules; including them would distort the averages.
- **Strength is accumulated team points**, which treats all wins as equal regardless of
  opponent. A sequential rating updated round by round would be better and is the obvious
  next step.
- **Position allocation is not random.** Tabbycat applies position-balance constraints
  across rounds, which this does not model. The within-room design handles most of it, not
  all of it.
- **Small motion counts.** A tournament contributes only as many motions as it has
  preliminary rounds, so comparisons *between* motions are far less certain than the figure
  for any single one. This is why the registry should favour large tournaments.

## Stack

Python · SQL · DuckDB · pandas · httpx · Pydantic · pytest · static HTML/CSS/JS ·
GitHub Pages

DuckDB was chosen over Postgres to get a working analytical pipeline with no
infrastructure; the SQL layers are written to port to BigQuery, and the site consumes a
JSON export rather than talking to the database, so the warehouse can be swapped without
touching the front end.

## Repository

```
config/tournaments.yaml     which tournaments to ingest
scripts/probe_api.py        is an instance usable?
scripts/ingest_all.py       fetch every configured tournament
src/motionlab/
  ingestion/raw.py          fetch-once, cache-on-disk raw layer
  ingestion/pages.py        adapter 2: public results pages
  transforms/build_db.py    raw JSON → DuckDB tables
  transforms/build_marts.py run SQL layers, export JSON
  features/schema.py        Pydantic feature schema + prompt
  features/extract.py       provider-neutral, cached LLM extraction
sql/intermediate/           team strength, residuals
sql/marts/                  per-motion and per-tournament balance
llm_cache/motions/          committed feature extractions
tests/                      data quality assertions
docs/                       the static site (GitHub Pages)
```

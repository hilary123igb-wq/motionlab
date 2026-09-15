"""Data quality checks against the built warehouse.

These are not unit tests of Python functions -- they are assertions about the
DATA, which is where this kind of pipeline actually goes wrong. A parser that
silently mismaps a position will pass every unit test and fail every one of
these.

    pytest -q

Requires the warehouse to exist:
    python -m motionlab.transforms.build_db
    python -m motionlab.transforms.build_marts
"""

from pathlib import Path

import duckdb
import pytest

DB_PATH = Path("data/motionlab.duckdb")


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("warehouse not built yet — run build_db then build_marts")
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    yield connection
    connection.close()


def failures(con, sql: str):
    """Rows returned by a query that should return none."""
    return con.sql(sql).fetchall()


def test_every_debate_has_four_teams(con):
    bad = failures(con, """
        SELECT tournament_id, debate_id, COUNT(*) n
        FROM debate_teams GROUP BY 1, 2 HAVING COUNT(*) <> 4
    """)
    assert not bad, f"{len(bad)} debates without exactly four teams: {bad[:5]}"


def test_every_debate_has_one_of_each_side(con):
    bad = failures(con, """
        SELECT tournament_id, debate_id, COUNT(DISTINCT side) n
        FROM debate_teams GROUP BY 1, 2 HAVING COUNT(DISTINCT side) <> 4
    """)
    assert not bad, f"{len(bad)} debates with a duplicate or missing side: {bad[:5]}"


def test_sides_use_the_expected_vocabulary(con):
    bad = failures(con, "SELECT DISTINCT side FROM debate_teams WHERE side NOT IN ('og','oo','cg','co')")
    assert not bad, f"unexpected side values: {bad}"


def test_points_are_zero_to_three_and_sum_to_six(con):
    bad = failures(con, "SELECT DISTINCT team_points FROM debate_teams WHERE team_points NOT IN (0,1,2,3)")
    assert not bad, f"impossible team points: {bad}"

    bad = failures(con, """
        SELECT tournament_id, debate_id, SUM(team_points) s
        FROM debate_teams GROUP BY 1, 2 HAVING SUM(team_points) <> 6
    """)
    assert not bad, f"{len(bad)} debates whose points do not sum to 6: {bad[:5]}"


def test_no_team_debates_itself(con):
    bad = failures(con, """
        SELECT tournament_id, debate_id, team_id, COUNT(*) n
        FROM debate_teams GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
    """)
    assert not bad, f"{len(bad)} debates with the same team twice: {bad[:5]}"


def test_foreign_keys_resolve(con):
    orphan_teams = failures(con, """
        SELECT DISTINCT dt.tournament_id, dt.team_id
        FROM debate_teams dt
        LEFT JOIN teams t ON t.tournament_id = dt.tournament_id AND t.team_id = dt.team_id
        WHERE t.team_id IS NULL
    """)
    assert not orphan_teams, f"{len(orphan_teams)} results reference an unknown team"

    orphan_rounds = failures(con, """
        SELECT DISTINCT m.tournament_id, m.round_seq
        FROM motions m
        LEFT JOIN rounds r ON r.tournament_id = m.tournament_id AND r.round_seq = m.round_seq
        WHERE r.round_seq IS NULL
    """)
    assert not orphan_rounds, f"{len(orphan_rounds)} motions point at a round we did not ingest"


def test_one_motion_per_round(con):
    bad = failures(con, """
        SELECT tournament_id, round_seq, COUNT(*) n
        FROM motions GROUP BY 1, 2 HAVING COUNT(*) > 1
    """)
    assert not bad, f"{len(bad)} rounds carry more than one motion: {bad[:5]}"


# --- the two that guard the strength adjustment -------------------------------

def test_round_one_has_no_prior_strength(con):
    """Round 1 cannot have a 'points before this round' value. If it does,
    the window frame has been widened and the measure now leaks."""
    bad = failures(con, "SELECT * FROM int_team_strength WHERE round_seq = 1 AND pre_points IS NOT NULL")
    assert not bad, "round 1 has prior points — the window frame is leaking"


def test_residuals_sum_to_zero_in_every_room(con):
    """Expected points total 6 in a room and actual points total 6, so the
    residuals must cancel. Anything else means the expectation is malformed."""
    bad = failures(con, """
        SELECT tournament_id, debate_id, ROUND(SUM(residual), 6) s
        FROM int_debate_residuals GROUP BY 1, 2 HAVING ABS(SUM(residual)) > 1e-6
    """)
    assert not bad, f"{len(bad)} rooms whose residuals do not cancel: {bad[:5]}"


def test_expected_points_total_six(con):
    bad = failures(con, """
        SELECT tournament_id, debate_id, SUM(expected_points) s
        FROM int_debate_residuals GROUP BY 1, 2 HAVING ABS(SUM(expected_points) - 6) > 1e-6
    """)
    assert not bad, f"{len(bad)} rooms whose expected points do not total 6: {bad[:5]}"


def test_marts_cover_every_motion(con):
    bad = failures(con, """
        SELECT m.tournament_id, m.motion_id
        FROM motions m
        LEFT JOIN mart_motion_balance b
               ON b.tournament_id = m.tournament_id AND b.motion_id = m.motion_id
        WHERE b.motion_id IS NULL
    """)
    assert not bad, f"{len(bad)} motions missing from the mart"

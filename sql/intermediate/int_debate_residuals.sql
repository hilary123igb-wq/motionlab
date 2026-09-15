-- What each team was "expected" to score in its room, and how far off it was.
--
-- Rule:
--   1. rank the four teams in a room by the points they brought INTO the round
--   2. assign expected points 3 / 2 / 1 / 0 by that rank
--   3. teams on equal prior points share the places between them
--      (two teams tied for 2nd-3rd each get (2 + 1) / 2 = 1.5)
--   4. residual = actual points - expected points
--
-- A residual of +1 means "finished one place better than its record implied".
--
-- Because ranking happens INSIDE the room, differences in bracket strength
-- cancel: we ask who was strongest of these four, never who was strong overall.
--
-- Round 1 has no prior points, so all four teams tie, every expectation is 1.5,
-- and the residual collapses to (points - 1.5): the unadjusted measure. The
-- rule degrades gracefully instead of needing a special case.
--
-- Invariant: expected sums to 6 in every room and actual sums to 6, so
-- residuals sum to zero. tests/test_data_quality.py asserts exactly that.

CREATE OR REPLACE TABLE int_debate_residuals AS
WITH joined AS (
    SELECT
        dt.*,
        COALESCE(s.pre_points, 0) AS pre_points,
        COALESCE(s.pre_rounds, 0) AS pre_rounds
    FROM debate_teams dt
    LEFT JOIN int_team_strength s
           ON s.tournament_id = dt.tournament_id
          AND s.team_id       = dt.team_id
          AND s.round_seq     = dt.round_seq
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY tournament_id, debate_id
            ORDER BY pre_points DESC
        ) AS place
    FROM joined
)
SELECT
    *,
    -- 4 - place gives 3/2/1/0; averaging it over teams on equal prior points
    -- splits the shared places fairly and keeps the room total at 6.
    AVG(4 - place) OVER (
        PARTITION BY tournament_id, debate_id, pre_points
    ) AS expected_points,
    team_points - AVG(4 - place) OVER (
        PARTITION BY tournament_id, debate_id, pre_points
    ) AS residual
FROM ranked;

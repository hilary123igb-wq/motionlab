-- Pre-round team strength.
--
-- For each team in each round, how many team points had it already accumulated
-- BEFORE that round started.
--
-- The frame clause below is the single most important line in this project:
--
--     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
--
-- "1 PRECEDING" stops the window one row short of the current round. Change it
-- to CURRENT ROW and the strength measure would contain the result we are
-- trying to explain -- textbook data leakage, and every downstream number
-- would be quietly circular.
--
-- Round 1 therefore has pre_points = NULL. That is correct and wanted: round 1
-- is the only randomly drawn round, so it needs no adjustment.

CREATE OR REPLACE TABLE int_team_strength AS
SELECT
    tournament_id,
    team_id,
    round_seq,
    SUM(team_points) OVER w AS pre_points,
    COUNT(*)         OVER w AS pre_rounds
FROM debate_teams
WINDOW w AS (
    PARTITION BY tournament_id, team_id
    ORDER BY round_seq
    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
);

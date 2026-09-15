CREATE OR REPLACE TABLE mart_motion_position_stats AS
WITH per_debate AS (
    SELECT
        round_seq,
        debate_id,
        SUM(CASE WHEN side = 'og' THEN team_points ELSE 0 END) AS og,
        SUM(CASE WHEN side = 'oo' THEN team_points ELSE 0 END) AS oo,
        SUM(CASE WHEN side = 'cg' THEN team_points ELSE 0 END) AS cg,
        SUM(CASE WHEN side = 'co' THEN team_points ELSE 0 END) AS co
    FROM debate_teams
    GROUP BY round_seq, debate_id
),
per_debate_effects AS (
    SELECT
        *,
        (og + cg) / 2.0 - (oo + co) / 2.0 AS gov_opp,
        (og + oo) / 2.0 - (cg + co) / 2.0 AS open_close
    FROM per_debate
)
SELECT
    m.motion_id,
    m.round_seq,
    r.round_name,
    m.reference,
    m.motion_text,
    LENGTH(m.info_slide) > 0                                AS has_info_slide,
    COUNT(*)                                                AS n_rooms,
    ROUND(AVG(d.og), 3)                                     AS og_avg,
    ROUND(AVG(d.oo), 3)                                     AS oo_avg,
    ROUND(AVG(d.cg), 3)                                     AS cg_avg,
    ROUND(AVG(d.co), 3)                                     AS co_avg,
    ROUND(AVG(d.gov_opp), 3)                                AS gov_opp_advantage,
    ROUND(STDDEV_SAMP(d.gov_opp) / SQRT(COUNT(*)), 3)       AS gov_opp_se,
    ROUND(AVG(d.open_close), 3)                             AS open_close_advantage,
    ROUND(STDDEV_SAMP(d.open_close) / SQRT(COUNT(*)), 3)    AS open_close_se
FROM motions m
JOIN rounds r  ON r.round_seq = m.round_seq
JOIN per_debate_effects d ON d.round_seq = m.round_seq
GROUP BY m.motion_id, m.round_seq, r.round_name, m.reference, m.motion_text, m.info_slide
ORDER BY m.round_seq;
-- One row per motion: how the four positions actually did, raw and adjusted.
--
-- raw  columns compare each position to the neutral 1.50 baseline.
-- adj  columns compare each position to what the teams' prior records implied,
--      so a motion does not look biased merely because strong teams sat there.
--
-- evidence is a plain-English read on sample size, not a statistical test:
-- a motion run in 6 rooms simply cannot be measured as well as one run in 91.

CREATE OR REPLACE TABLE mart_motion_balance AS
WITH per_debate AS (
    SELECT
        tournament_id,
        round_seq,
        debate_id,
        SUM(CASE WHEN side = 'og' THEN team_points ELSE 0 END) AS og_p,
        SUM(CASE WHEN side = 'oo' THEN team_points ELSE 0 END) AS oo_p,
        SUM(CASE WHEN side = 'cg' THEN team_points ELSE 0 END) AS cg_p,
        SUM(CASE WHEN side = 'co' THEN team_points ELSE 0 END) AS co_p,
        SUM(CASE WHEN side = 'og' THEN residual    ELSE 0 END) AS og_r,
        SUM(CASE WHEN side = 'oo' THEN residual    ELSE 0 END) AS oo_r,
        SUM(CASE WHEN side = 'cg' THEN residual    ELSE 0 END) AS cg_r,
        SUM(CASE WHEN side = 'co' THEN residual    ELSE 0 END) AS co_r
    FROM int_debate_residuals
    GROUP BY tournament_id, round_seq, debate_id
)
SELECT
    m.tournament_id,
    t.title                                   AS tournament_title,
    m.motion_id,
    m.round_seq,
    r.round_name,
    m.reference,
    m.motion_text,
    m.info_slide,
    LENGTH(m.info_slide) > 0                  AS has_info_slide,
    COUNT(*)                                  AS n_rooms,

    ROUND(AVG(d.og_p), 3)                     AS og_avg,
    ROUND(AVG(d.oo_p), 3)                     AS oo_avg,
    ROUND(AVG(d.cg_p), 3)                     AS cg_avg,
    ROUND(AVG(d.co_p), 3)                     AS co_avg,
    ROUND(AVG((d.og_p + d.cg_p) / 2.0 - (d.oo_p + d.co_p) / 2.0), 3) AS gov_opp_raw,
    ROUND(AVG((d.og_p + d.oo_p) / 2.0 - (d.cg_p + d.co_p) / 2.0), 3) AS open_close_raw,

    ROUND(AVG(d.og_r), 3)                     AS og_adj,
    ROUND(AVG(d.oo_r), 3)                     AS oo_adj,
    ROUND(AVG(d.cg_r), 3)                     AS cg_adj,
    ROUND(AVG(d.co_r), 3)                     AS co_adj,
    ROUND(AVG((d.og_r + d.cg_r) / 2.0 - (d.oo_r + d.co_r) / 2.0), 3) AS gov_opp_adj,
    ROUND(AVG((d.og_r + d.oo_r) / 2.0 - (d.cg_r + d.co_r) / 2.0), 3) AS open_close_adj,

    CASE WHEN COUNT(*) >= 40 THEN 'strong'
         WHEN COUNT(*) >= 15 THEN 'moderate'
         ELSE 'limited' END                   AS evidence
FROM motions m
JOIN rounds      r ON r.tournament_id = m.tournament_id AND r.round_seq = m.round_seq
JOIN tournaments t ON t.tournament_id = m.tournament_id
JOIN per_debate  d ON d.tournament_id = m.tournament_id AND d.round_seq = m.round_seq
GROUP BY ALL
ORDER BY m.tournament_id, m.round_seq;

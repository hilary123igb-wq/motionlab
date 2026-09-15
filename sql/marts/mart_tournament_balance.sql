-- One row per tournament: overall positional picture across all its motions.

CREATE OR REPLACE TABLE mart_tournament_balance AS
SELECT
    tournament_id,
    tournament_title,
    COUNT(*)                                  AS n_motions,
    SUM(n_rooms)                              AS n_rooms,
    SUM(n_rooms) * 4                          AS n_team_results,

    ROUND(SUM(og_avg * n_rooms) / SUM(n_rooms), 3) AS og_avg,
    ROUND(SUM(oo_avg * n_rooms) / SUM(n_rooms), 3) AS oo_avg,
    ROUND(SUM(cg_avg * n_rooms) / SUM(n_rooms), 3) AS cg_avg,
    ROUND(SUM(co_avg * n_rooms) / SUM(n_rooms), 3) AS co_avg,

    ROUND(SUM(gov_opp_raw    * n_rooms) / SUM(n_rooms), 3) AS gov_opp_raw,
    ROUND(SUM(gov_opp_adj    * n_rooms) / SUM(n_rooms), 3) AS gov_opp_adj,
    ROUND(SUM(open_close_raw * n_rooms) / SUM(n_rooms), 3) AS open_close_raw,
    ROUND(SUM(open_close_adj * n_rooms) / SUM(n_rooms), 3) AS open_close_adj,

    -- how far the most and least balanced motion sat apart
    ROUND(MAX(gov_opp_adj) - MIN(gov_opp_adj), 3) AS gov_opp_spread,

    CASE WHEN SUM(n_rooms) >= 300 THEN 'strong'
         WHEN SUM(n_rooms) >= 100 THEN 'moderate'
         ELSE 'limited' END                   AS evidence
FROM mart_motion_balance
GROUP BY tournament_id, tournament_title
ORDER BY n_rooms DESC;

{{
    config(
        materialized='table'
    )
}}

-- int_conversation_type
-- Rule-based conversation type classification.
-- One row per conversation. Computes boolean signals first,
-- then derives a single mutually exclusive type label from combinations.
-- Excluded from classifiable corpus: ghost, anomalous, unknown (306 of 735 conversations).

SELECT
    conversation_id,

    -- boolean signals
    total_turns = 0                                         AS is_ghost,
    total_turns > 75                                        AS is_anomalous,
    (
        corpus_era = 'pre_prefetch'
        AND prefetch_call_count = 0
        AND sql_write_count = 0
        AND non_sql_write_count = 0
        AND total_turns > 0
    )                                                       AS is_unknown,
    (
        corpus_era = 'post_prefetch'
        AND prefetch_call_count > 0
    )                                                       AS has_generation_signal,
    sql_write_count > 0                                     AS has_sql_write,
    non_sql_write_count > 0                                 AS has_non_sql_write,
    execute_sql_count > 0                                   AS has_execute_sql,
    user_rejected_tool_count > 0                            AS has_user_interrupt,

    -- type classification derived from boolean combinations
    -- priority: ghost > anomalous > unknown > generation > complex > modification > diagnostic > consultation
    CASE
        WHEN total_turns = 0
            THEN 'ghost'
        WHEN total_turns > 75
            THEN 'anomalous'
        WHEN corpus_era = 'pre_prefetch'
            AND prefetch_call_count = 0
            AND sql_write_count = 0
            AND non_sql_write_count = 0
            AND total_turns > 0
            THEN 'unknown'
        WHEN corpus_era = 'post_prefetch'
            AND prefetch_call_count > 0
            AND sql_write_count > 0
            AND non_sql_write_count = 0
            THEN 'generation'
        WHEN corpus_era = 'post_prefetch'
            AND prefetch_call_count > 0
            AND non_sql_write_count > 0
            THEN 'complex'
        WHEN sql_write_count > 0
            AND non_sql_write_count = 0
            THEN 'modification'
        WHEN non_sql_write_count > 0
            AND sql_write_count = 0
            THEN 'diagnostic'
        ELSE
            'consultation'
    END                                                     AS conversation_type

FROM {{ ref('int_conversation_metrics') }}
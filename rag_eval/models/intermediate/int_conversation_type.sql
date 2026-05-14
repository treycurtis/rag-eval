
{{
    config(
        materialized='table'
    )
}}

-- int_conversation_type
-- Rule-based conversation type classification.
-- One row per conversation. Types are mutually exclusive, applied in priority order.
-- Priority: generation > modification > diagnostic > lookup > consultation

SELECT
    conversation_id,
    CASE
        WHEN prefetch_call_count > 0
            THEN 'generation'
        WHEN prefetch_call_count = 0
            AND sql_write_count > 0
            THEN 'modification'
        WHEN prefetch_call_count = 0
            AND sql_write_count = 0
            AND non_sql_write_count > 0
            THEN 'diagnostic'
        WHEN prefetch_call_count = 0
            AND sql_write_count = 0
            AND non_sql_write_count = 0
            AND execute_sql_count > 0
            THEN 'lookup'
        ELSE
            'consultation'
    END                                                    AS conversation_type

FROM {{ ref('int_conversation_metrics') }}
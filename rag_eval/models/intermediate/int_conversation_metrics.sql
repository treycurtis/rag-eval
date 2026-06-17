
-- int_conversation_metrics
-- One row per conversation. Aggregates behavioral signals from stg_conversation_messages
-- to support outcome classification in fct_conversation_outcomes.
-- CTEs grouped by signal domain; final SELECT joins all to stg_conversations.

WITH schema_prefetch_signals AS (
    SELECT
        conversation_id,
        COUNT(*)                                           AS prefetch_call_count,
        MIN(sequence_number)                               AS first_prefetch_sequence
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type = 'tool_call'
      AND content ILIKE '%schema_prefetch_tool%'
    GROUP BY conversation_id
),

sql_write_signals AS (
    SELECT
        conversation_id,
        SUM(CASE WHEN content ILIKE '%Write(file_path=%'
                  AND content ILIKE '%/sql/%'
                  AND content NOT ILIKE '%TodoWrite%'
                  THEN 1 ELSE 0 END)                      AS sql_write_count,
        SUM(CASE WHEN content ILIKE '%Write(file_path=%'
                  AND content NOT ILIKE '%/sql/%'
                  AND content NOT ILIKE '%TodoWrite%'
                  THEN 1 ELSE 0 END)                      AS non_sql_write_count,
        MIN(CASE WHEN content ILIKE '%Write(file_path=%'
                  AND content ILIKE '%/sql/%'
                  AND content NOT ILIKE '%TodoWrite%'
                  THEN sequence_number END)                AS first_write_sequence
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type = 'tool_call'
      AND content ILIKE '%Write(%'
      AND content NOT ILIKE '%TodoWrite%'
    GROUP BY conversation_id
),

execute_sql_signals AS (
    SELECT
        conversation_id,
        COUNT(*)                                           AS execute_sql_count,
        SUM(CASE WHEN message_type = 'tool_result'
                  AND content ILIKE '%preview%'
                  THEN 1 ELSE 0 END)                       AS execute_sql_success_count,
        SUM(CASE WHEN message_type = 'tool_result'
                  AND content ILIKE '%OperationalError%'
                  THEN 1 ELSE 0 END)                       AS permission_error_count
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type IN ('tool_call', 'tool_result')
      AND (
          content ILIKE '%execute_sql%'
          OR content ILIKE '%OperationalError%'
          OR content ILIKE '%preview%'
      )
    GROUP BY conversation_id
),

code_review_signals AS (
    SELECT
        conversation_id,
        COUNT(*)                                           AS code_review_count,
        MAX(CASE WHEN seq_rank_asc = 1
                 THEN score END)                           AS code_review_score_first,
        MAX(CASE WHEN seq_rank_desc = 1
                 THEN score END)                           AS code_review_score_last
    FROM (
        SELECT
            conversation_id,
            REGEXP_SUBSTR(
                content,
                'Quality Score[^0-9]*([0-9]+)',
                1, 1, 'e', 1
            )::INT                                         AS score,
            ROW_NUMBER() OVER (
                PARTITION BY conversation_id
                ORDER BY sequence_number ASC
            )                                              AS seq_rank_asc,
            ROW_NUMBER() OVER (
                PARTITION BY conversation_id
                ORDER BY sequence_number DESC
            )                                              AS seq_rank_desc
        FROM {{ ref('stg_conversation_messages') }}
        WHERE message_type = 'tool_result'
          AND content ILIKE '%Quality Score%'
    )
    GROUP BY conversation_id
),

user_correction_signals AS (
    -- Proxy for human intervention / course correction.
    -- Captures amendments to the user's own ask as well as corrections to querybot.
    -- Interpret as efficiency signal, not quality signal in isolation.
    -- TODO: replace with memory API enrichment in P4 (user_correction field on ExtractedLearning)
    SELECT
        conversation_id,
        COUNT(*)                                            AS user_correction_count
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type = 'user'
      AND (
          content ILIKE '%should be%'
          OR content ILIKE '%should actually%'
          OR content ILIKE '%instead of%'
          OR content ILIKE '%wrong%'
          OR content ILIKE '%incorrect%'
          OR content ILIKE '%look in%'
          OR content ILIKE '%use%table%'
          OR content ILIKE '%not%right%'
      )
    GROUP BY conversation_id
),

stale_doc_signals AS (
    SELECT
        conversation_id,
        COUNT(*)                                            AS stale_doc_warning_count
    FROM {{ ref('stg_conversation_messages') }}
    WHERE content ILIKE '%Documentation sync last performed%'
    GROUP BY conversation_id
),

tool_error_signals AS (
    SELECT
        conversation_id,
        SUM(CASE WHEN content ILIKE '%tool_use_error%'
                  THEN 1 ELSE 0 END)                       AS tool_use_error_count,
        SUM(CASE WHEN content ILIKE '%ImportError%'
                  OR content ILIKE '%ModuleNotFoundError%'
                  THEN 1 ELSE 0 END)                       AS codebase_error_count
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type = 'tool_result'
    GROUP BY conversation_id
),

user_message_signals AS (
    SELECT
        conversation_id,
        COUNT(*)                                           AS total_user_messages
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type = 'user'
    GROUP BY conversation_id
),

user_interrupt_signals AS (
    SELECT
        conversation_id,
        COUNT(*)                                            AS user_rejected_tool_count
    FROM {{ ref('stg_conversation_messages') }}
    WHERE message_type = 'tool_result'
      AND content ILIKE '%tool use was rejected%'
    GROUP BY conversation_id
),

run_cost_signals AS (
    SELECT
        conversation_id,
        SUM(cost_usd)                          AS total_cost_usd,
        COUNT(*)                               AS run_count,
        AVG(duration_ms)                       AS avg_run_duration_ms
    FROM {{ ref('stg_conversation_runs') }}
    GROUP BY conversation_id
)

SELECT
    c.id                                                   AS conversation_id,
    c.total_turns,
    c.total_duration_ms,
    c.learning_extracted_at IS NOT NULL                    AS learning_extracted,

    COALESCE(um.total_user_messages, 0)                    AS total_user_messages,

    -- schema prefetch
    COALESCE(sp.prefetch_call_count, 0)                    AS prefetch_call_count,
    sp.first_prefetch_sequence,

    -- sql writes
    COALESCE(sw.sql_write_count, 0)                        AS sql_write_count,
    
       -- non-sql writes
    COALESCE(sw.non_sql_write_count, 0)                    AS non_sql_write_count,

    sw.first_write_sequence,

    -- time to first sql write (proxy for schema discovery cost)
    sw.first_write_sequence - sp.first_prefetch_sequence   AS prefetch_to_write_gap,

    -- execute sql
    COALESCE(es.execute_sql_count, 0)                      AS execute_sql_count,
    COALESCE(es.execute_sql_success_count, 0)              AS execute_sql_success_count,
    COALESCE(es.permission_error_count, 0)                 AS permission_error_count,

    -- code review
    COALESCE(cr.code_review_count, 0)                      AS code_review_count,
    cr.code_review_score_first,
    cr.code_review_score_last,
    cr.code_review_score_last
        - cr.code_review_score_first                       AS code_review_score_delta,

    -- user corrections (proxy)
    COALESCE(uc.user_correction_count, 0)                  AS user_correction_count,

    -- stale docs
    COALESCE(sd.stale_doc_warning_count, 0)                AS stale_doc_warning_count,

    -- tool errors
    COALESCE(te.tool_use_error_count, 0)                   AS tool_use_error_count,
    COALESCE(te.codebase_error_count, 0)                   AS codebase_error_count,
    c.created_at                                           AS conversation_created_at,

    CASE
        WHEN c.created_at < '2026-03-01' THEN 'pre_prefetch'
        ELSE 'post_prefetch'
    END                                                    AS corpus_era,

    -- user interrupts
    COALESCE(ui.user_rejected_tool_count, 0)               AS user_rejected_tool_count, 

    -- turn cost, duration, and run count
    COALESCE(rc.total_cost_usd, 0)                         AS total_cost_usd,
    COALESCE(rc.run_count, 0)                              AS run_count,
    COALESCE(rc.avg_run_duration_ms, 0)                    AS avg_run_duration_ms

FROM {{ ref('stg_conversations') }} c
LEFT JOIN schema_prefetch_signals  sp ON c.id = sp.conversation_id
LEFT JOIN sql_write_signals        sw ON c.id = sw.conversation_id
LEFT JOIN execute_sql_signals      es ON c.id = es.conversation_id
LEFT JOIN code_review_signals      cr ON c.id = cr.conversation_id
LEFT JOIN user_correction_signals  uc ON c.id = uc.conversation_id
LEFT JOIN stale_doc_signals        sd ON c.id = sd.conversation_id
LEFT JOIN tool_error_signals       te ON c.id = te.conversation_id
LEFT JOIN user_message_signals     um ON c.id = um.conversation_id
LEFT JOIN user_interrupt_signals   ui ON c.id = ui.conversation_id
LEFT JOIN run_cost_signals         rc ON c.id = rc.conversation_id
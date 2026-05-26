{{
    config(
        materialized='table'
    )
}}

-- fct_conversation_outcomes
-- One row per classifiable conversation (generation, modification, diagnostic, consultation).
-- Ghost, anomalous, and unknown conversations are excluded.
-- Classifier result fields are NULL for conversations not yet classified (is_classified = FALSE).
-- Rubric scores, reasoning, and flag_dev_acknowledged are NULL for inconclusive outcomes.

WITH classifiable AS (
    SELECT
        conversation_id,
        conversation_type,
        has_generation_signal,
        has_sql_write,
        has_non_sql_write,
        has_execute_sql,
        has_user_interrupt
    FROM {{ ref('int_conversation_type') }}
    WHERE conversation_type IN ('generation', 'modification', 'diagnostic', 'consultation')
),

latest_outcomes AS (
    -- Deduplicate on conversation_id, keeping the most recent classification run.
    SELECT
        conversation_id,
        conversation_type                   AS classified_as_type,
        outcome,
        question_understanding,
        resource_exhaustion,
        answer_grounding,
        actionability,
        flag_dev_acknowledged,
        reasoning,
        char_count,
        classified_at
    FROM (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY conversation_id
                ORDER BY classified_at DESC
            )                               AS rn
        FROM {{ source('classifier_output', 'int_conversation_outcomes_raw') }}
    )
    WHERE rn = 1
)

SELECT
    c.conversation_id,
    c.conversation_type,

    -- classifier status
    lo.outcome IS NOT NULL                  AS is_classified,
    lo.outcome,
    lo.classified_as_type,
    lo.classified_at,

    -- rubric scores (NULL if not classified or inconclusive)
    CASE WHEN lo.outcome IS NULL
          OR lo.outcome = 'inconclusive'
         THEN NULL
         ELSE lo.question_understanding END AS question_understanding,
    CASE WHEN lo.outcome IS NULL
          OR lo.outcome = 'inconclusive'
         THEN NULL
         ELSE lo.resource_exhaustion END    AS resource_exhaustion,
    CASE WHEN lo.outcome IS NULL
          OR lo.outcome = 'inconclusive'
         THEN NULL
         ELSE lo.answer_grounding END       AS answer_grounding,
    CASE WHEN lo.outcome IS NULL
          OR lo.outcome = 'inconclusive'
         THEN NULL
         ELSE lo.actionability END          AS actionability,

    -- classifier narrative fields (NULL if not classified or inconclusive)
    CASE WHEN lo.outcome IS NULL
          OR lo.outcome = 'inconclusive'
         THEN NULL
         ELSE lo.flag_dev_acknowledged END  AS flag_dev_acknowledged,
    CASE WHEN lo.outcome IS NULL
          OR lo.outcome = 'inconclusive'
         THEN NULL
         ELSE lo.reasoning END              AS reasoning,

    -- char_count is a property of the content fed to the classifier, not a classifier output
    lo.char_count,

    -- volume signals
    m.total_turns,
    m.total_user_messages,
    m.run_count,

    -- cost & duration
    m.total_cost_usd,
    m.total_duration_ms,
    m.avg_run_duration_ms,

    -- corpus era
    m.corpus_era,

    -- schema discovery
    m.prefetch_call_count,
    m.first_prefetch_sequence,

    -- file writes
    m.sql_write_count,
    m.non_sql_write_count,
    m.first_write_sequence,
    m.prefetch_to_write_gap,

    -- sql execution
    m.execute_sql_count,
    m.execute_sql_success_count,
    m.permission_error_count,

    -- code review trajectory
    m.code_review_count,
    m.code_review_score_first,
    m.code_review_score_last,
    m.code_review_score_delta,

    -- user behavior
    m.user_correction_count,
    m.user_rejected_tool_count,

    -- doc quality
    m.stale_doc_warning_count,

    -- error signals
    m.tool_use_error_count,
    m.codebase_error_count,

    -- type boolean flags (pass-through from int_conversation_type)
    c.has_generation_signal,
    c.has_sql_write,
    c.has_non_sql_write                     AS has_non_sql_write,
    c.has_non_sql_write                     AS has_non_sql_deliverable,
    c.has_execute_sql,
    c.has_user_interrupt,

    -- learning pipeline status
    m.learning_extracted

FROM classifiable                           c
LEFT JOIN {{ ref('int_conversation_metrics') }} m
    ON c.conversation_id = m.conversation_id
LEFT JOIN latest_outcomes                   lo
    ON c.conversation_id = lo.conversation_id
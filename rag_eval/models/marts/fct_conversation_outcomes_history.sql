-- fct_conversation_outcomes_history
-- One row per (conversation_id, prompt_version) — the cross-version companion
-- to fct_conversation_outcomes (which keeps only the latest version per
-- conversation). Use this model to compare scoring across classifier versions:
-- outcome drift, rubric-score shifts, and how a prompt/model change moved labels.
--
-- Within a single prompt_version there can still be multiple runs (patch reruns);
-- this keeps the most recent run per (conversation_id, prompt_version).

WITH versioned_outcomes AS (
    SELECT
        conversation_id,
        prompt_version,
        model                               AS model_version,
        prompt_hash,
        conversation_type                   AS classified_as_type,
        outcome,
        question_understanding,
        resource_exhaustion,
        answer_grounding,
        actionability,
        flag_dev_acknowledged,
        reasoning,
        char_count,
        error,
        classified_at,
        ROW_NUMBER() OVER (
            PARTITION BY conversation_id, prompt_version
            ORDER BY classified_at DESC
        )                                   AS rn
    FROM {{ source('classifier_output', 'int_conversation_outcomes_raw') }}
    WHERE prompt_version IS NOT NULL
)

SELECT
    MD5(conversation_id || '-' || prompt_version)
                                            AS conversation_version_key,
    conversation_id,
    prompt_version,
    model_version,
    prompt_hash,
    classified_as_type,
    classified_at,

    outcome,

    -- rubric scores (NULL for inconclusive, mirroring fct_conversation_outcomes)
    CASE WHEN outcome = 'inconclusive' THEN NULL
         ELSE question_understanding END    AS question_understanding,
    CASE WHEN outcome = 'inconclusive' THEN NULL
         ELSE resource_exhaustion END       AS resource_exhaustion,
    CASE WHEN outcome = 'inconclusive' THEN NULL
         ELSE answer_grounding END          AS answer_grounding,
    CASE WHEN outcome = 'inconclusive' THEN NULL
         ELSE actionability END             AS actionability,
    CASE WHEN outcome = 'inconclusive' THEN NULL
         ELSE flag_dev_acknowledged END     AS flag_dev_acknowledged,
    CASE WHEN outcome = 'inconclusive' THEN NULL
         ELSE reasoning END                 AS reasoning,

    char_count,
    error

FROM versioned_outcomes
WHERE rn = 1

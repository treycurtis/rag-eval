-- fct_validation_runs
-- One row per (validation_run_id, conversation_id) — the cleaned, typed view of the
-- append-only validation ledger INT_VALIDATION_RUNS_RAW. Each row is one judge-vs-human
-- comparison for a golden-set case at the prompt_version that was current when the run executed.
-- Keeping every run (not collapsing to latest) is intentional: the cross-run history IS the
-- drift signal. The ROW_NUMBER guard only protects against accidental duplicate inserts
-- within a single validation_run_id.

WITH ledger AS (
    SELECT
        validation_run_id,
        run_at,
        conversation_id,
        label_cohort,
        is_blind,
        prompt_version,
        model                                       AS model_version,
        prompt_hash,
        expected_outcome,
        actual_outcome,
        outcome_passed,
        expected_question_understanding,
        actual_question_understanding,
        question_understanding_abs_error,
        expected_resource_exhaustion,
        actual_resource_exhaustion,
        resource_exhaustion_abs_error,
        expected_answer_grounding,
        actual_answer_grounding,
        answer_grounding_abs_error,
        expected_actionability,
        actual_actionability,
        actionability_abs_error,
        char_count,
        error,
        ROW_NUMBER() OVER (
            PARTITION BY validation_run_id, conversation_id
            ORDER BY run_at DESC
        )                                           AS rn
    FROM {{ source('validation_output', 'int_validation_runs_raw') }}
)

SELECT
    MD5(validation_run_id || '-' || conversation_id)    AS validation_case_key,
    validation_run_id,
    run_at,
    conversation_id,
    label_cohort,
    is_blind,
    prompt_version,
    model_version,
    prompt_hash,

    -- judge-vs-human outcome comparison
    expected_outcome,
    actual_outcome,
    outcome_passed,

    -- a row is "scored" only when the judge returned a parseable result;
    -- API/parse failures are errors, NOT disagreements, and must not count against agreement.
    error IS NULL                                       AS is_scored,
    error IS NOT NULL                                   AS is_error,
    error,

    -- per-dimension rubric comparison (expected_* are NULL until humans label rubric scores)
    expected_question_understanding,
    actual_question_understanding,
    question_understanding_abs_error,
    expected_resource_exhaustion,
    actual_resource_exhaustion,
    resource_exhaustion_abs_error,
    expected_answer_grounding,
    actual_answer_grounding,
    answer_grounding_abs_error,
    expected_actionability,
    actual_actionability,
    actionability_abs_error,

    char_count

FROM ledger
WHERE rn = 1

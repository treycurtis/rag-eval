-- fct_concept_drift
-- One row per (validation_run_id, prompt_version) — the judge-vs-human agreement time series.
-- This is the concept-drift signal: holding the golden set and prompt_version fixed, a drop in
-- agreement over time means the judge moved (e.g. the model behind the claude-sonnet-4-6 alias
-- changed) even though our prompt_version hash did not rotate. Baseline = the FIRST run observed
-- at each prompt_version, so drift is measured within a version (a new prompt_version starts a new
-- baseline by design). Errors (unparseable/API-failed classifications) are excluded from agreement
-- denominators — they are not disagreements — and surfaced separately as error_count.

WITH runs AS (
    SELECT *
    FROM {{ ref('fct_validation_runs') }}
),

per_run AS (
    SELECT
        validation_run_id,
        prompt_version,
        model_version,
        prompt_hash,
        MIN(run_at)                                                             AS run_at,

        -- headline: non-blind, scored (error-free) cases only
        COUNT_IF(NOT is_blind AND is_scored)                                    AS n_cases,
        COUNT_IF(NOT is_blind AND is_scored AND outcome_passed)                 AS n_passed,
        COUNT_IF(is_error)                                                      AS error_count,

        -- blind cases tracked separately (held out of the headline metric)
        COUNT_IF(is_blind AND is_scored)                                        AS n_blind_cases,
        COUNT_IF(is_blind AND is_scored AND outcome_passed)                     AS n_blind_passed,

        -- per-dimension rubric agreement over scored rows where BOTH sides have a score
        -- (NULL until humans add independent rubric labels — schema is ready for v2)
        AVG(CASE WHEN is_scored AND question_understanding_abs_error IS NOT NULL
                 THEN IFF(question_understanding_abs_error = 0, 1, 0) END)      AS question_understanding_match_rate,
        AVG(question_understanding_abs_error)                                   AS question_understanding_mae,
        AVG(CASE WHEN is_scored AND resource_exhaustion_abs_error IS NOT NULL
                 THEN IFF(resource_exhaustion_abs_error = 0, 1, 0) END)         AS resource_exhaustion_match_rate,
        AVG(resource_exhaustion_abs_error)                                      AS resource_exhaustion_mae,
        AVG(CASE WHEN is_scored AND answer_grounding_abs_error IS NOT NULL
                 THEN IFF(answer_grounding_abs_error = 0, 1, 0) END)            AS answer_grounding_match_rate,
        AVG(answer_grounding_abs_error)                                         AS answer_grounding_mae,
        AVG(CASE WHEN is_scored AND actionability_abs_error IS NOT NULL
                 THEN IFF(actionability_abs_error = 0, 1, 0) END)               AS actionability_match_rate,
        AVG(actionability_abs_error)                                            AS actionability_mae
    FROM runs
    GROUP BY validation_run_id, prompt_version, model_version, prompt_hash
),

scored AS (
    SELECT
        *,
        n_passed::FLOAT / NULLIF(n_cases, 0)                AS outcome_agreement_rate,
        n_blind_passed::FLOAT / NULLIF(n_blind_cases, 0)    AS blind_agreement_rate
    FROM per_run
),

with_baseline AS (
    SELECT
        *,
        -- baseline = agreement of the earliest run at this prompt_version
        FIRST_VALUE(outcome_agreement_rate) OVER (
            PARTITION BY prompt_version
            ORDER BY run_at ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )                                                   AS baseline_agreement_rate
    FROM scored
)

SELECT
    validation_run_id,
    prompt_version,
    model_version,
    prompt_hash,
    run_at,
    n_cases,
    n_passed,
    error_count,
    n_blind_cases,
    n_blind_passed,
    outcome_agreement_rate,
    blind_agreement_rate,
    baseline_agreement_rate,
    outcome_agreement_rate - baseline_agreement_rate        AS agreement_delta_vs_baseline,

    -- drift bands echo the PSI convention used elsewhere in this project (0.1 / 0.25 thresholds)
    CASE
        WHEN outcome_agreement_rate IS NULL OR baseline_agreement_rate IS NULL THEN 'unknown'
        WHEN ABS(outcome_agreement_rate - baseline_agreement_rate) < 0.10 THEN 'stable'
        WHEN ABS(outcome_agreement_rate - baseline_agreement_rate) < 0.25 THEN 'moderate'
        ELSE 'significant'
    END                                                     AS drift_band,

    question_understanding_match_rate,
    question_understanding_mae,
    resource_exhaustion_match_rate,
    resource_exhaustion_mae,
    answer_grounding_match_rate,
    answer_grounding_mae,
    actionability_match_rate,
    actionability_mae

FROM with_baseline

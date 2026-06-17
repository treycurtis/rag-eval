-- Sanity gate: outcome_agreement_rate must always be a proportion in [0, 1].
-- (Native replacement for dbt_utils.accepted_range, which is not installed in this project.)
-- Returns rows (= test failure) if any run produces an out-of-range agreement rate.

SELECT
    validation_run_id,
    prompt_version,
    outcome_agreement_rate
FROM {{ ref('fct_concept_drift') }}
WHERE outcome_agreement_rate IS NOT NULL
  AND (outcome_agreement_rate < 0 OR outcome_agreement_rate > 1)

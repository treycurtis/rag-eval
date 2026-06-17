-- Drift gate: fail the build when the most recent validation run at any prompt_version has
-- dropped more than 10 points of judge-vs-human agreement below that version's baseline.
-- This is the in-pipeline concept-drift alarm — no external services required.
-- Returns rows (= test failure) only when drift breaches the threshold.

{% set drift_threshold = -0.10 %}

WITH latest_run_per_version AS (
    SELECT
        prompt_version,
        run_at,
        outcome_agreement_rate,
        baseline_agreement_rate,
        agreement_delta_vs_baseline,
        ROW_NUMBER() OVER (
            PARTITION BY prompt_version
            ORDER BY run_at DESC
        ) AS rn
    FROM {{ ref('fct_concept_drift') }}
)

SELECT *
FROM latest_run_per_version
WHERE rn = 1
  AND agreement_delta_vs_baseline < {{ drift_threshold }}

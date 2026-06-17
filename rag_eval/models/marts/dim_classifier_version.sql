-- dim_classifier_version
-- Warehouse-native "model registry" for the LLM-as-judge classifiers.
-- One row per distinct (prompt_version, model, prompt_hash) seen in the raw
-- classifier ledger. Lets you answer "which judge produced these scores",
-- "when did version X first/last run", and "how many conversations did it cover".
-- prompt_version is a fingerprint derived in the Python classifiers from a hash
-- of (model + prompt text), so a new row here means the scoring function changed.

WITH runs AS (
    SELECT
        prompt_version,
        model                                       AS model_version,
        prompt_hash,
        conversation_type,
        conversation_id,
        outcome,
        error,
        classified_at
    FROM {{ source('classifier_output', 'int_conversation_outcomes_raw') }}
    WHERE prompt_version IS NOT NULL
)

SELECT
    prompt_version,
    model_version,
    prompt_hash,

    -- which corpus this judge scored (consultation vs sql output prompts emit
    -- different conversation_type values and live under different prompt_version prefixes)
    MIN(conversation_type)                          AS first_conversation_type,
    COUNT(DISTINCT conversation_type)               AS distinct_conversation_types,

    -- run lineage
    MIN(classified_at)                              AS first_classified_at,
    MAX(classified_at)                              AS last_classified_at,

    -- coverage / volume for this version
    COUNT(*)                                        AS total_classifications,
    COUNT(DISTINCT conversation_id)                 AS distinct_conversations,
    COUNT_IF(error IS NOT NULL)                     AS error_count

FROM runs
GROUP BY prompt_version, model_version, prompt_hash

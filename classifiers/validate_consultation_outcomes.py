"""
validate_consultation_outcomes.py — standing validation layer for the consultation classifier.

Re-scores a human-labeled golden set with the current judge and appends every run to an
append-only Snowflake ledger (INT_VALIDATION_RUNS_RAW). Re-scoring the same fixed cohort over
time surfaces JUDGE drift — the model behind the `claude-sonnet-4-6` alias changing — which the
prompt_version hash cannot see (it only fingerprints OUR prompt + model string, not Anthropic's
weights). Adding new label cohorts of fresh conversations surfaces DOMAIN concept drift.

Ground truth comes from the dbt seed RAG_EVAL.SEEDS.VALIDATION_LABELS — never from classifier
output — so judge-vs-human agreement is a real, independent signal. The judge call (prompt,
model, prompt_version, prompt caching) is imported from run_consultation_classifier so there is
exactly one source of truth for the scoring function.

Snowflake is the source of truth; a local JSON file is written as a convenience backup.

Usage (from repo root, with the project venv active):
    python classifiers/validate_consultation_outcomes.py
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Make the classifier module importable regardless of how this script is invoked ──
# repo root → so `metrics` (imported transitively by the classifier) resolves;
# this dir → so `run_consultation_classifier` resolves.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_REPO_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Reuse the exact scoring function (prompt, model, version, prompt caching, retries) and the
# key-pair Snowflake connection. Do NOT duplicate the prompt here — a divergent copy would make
# this validate a different judge than the one in production.
from run_consultation_classifier import (  # noqa: E402
    MODEL,
    PROMPT_HASH,
    PROMPT_VERSION,
    classify_with_retry,
    fetch_conversation,
    get_snowflake_connection,
    truncate_conversation,
)

# Rubric dimensions scored 1-3 by the judge and (eventually) by humans.
RUBRIC_DIMENSIONS = (
    "question_understanding",
    "resource_exhaustion",
    "answer_grounding",
    "actionability",
)

SEED_LABELS_TABLE = "RAG_EVAL.SEEDS.VALIDATION_LABELS"
LEDGER_TABLE = "INT_VALIDATION_RUNS_RAW"  # written to the connection's default schema (STAGING)


# ── Ground truth: read the human-labeled golden set from the dbt seed ─────────
def fetch_validation_labels(conn) -> list[dict]:
    """Load human ground-truth labels. Independent of the judge by construction."""
    query = f"""
        SELECT
            conversation_id,
            expected_outcome,
            expected_question_understanding,
            expected_resource_exhaustion,
            expected_answer_grounding,
            expected_actionability,
            label_cohort,
            is_blind
        FROM {SEED_LABELS_TABLE}
        ORDER BY conversation_id
    """
    cursor = conn.cursor()
    cursor.execute(query)
    cols = [c[0].lower() for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ── Append-only ledger ────────────────────────────────────────────────────────
def ensure_validation_table(conn):
    """Create the append-only validation ledger if it does not exist.

    Mirrors the INT_CONVERSATION_OUTCOMES_RAW pattern: one row per (validation_run_id,
    conversation_id). Never updated in place — history is what makes drift observable.
    """
    cursor = conn.cursor()
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
            validation_run_id                       VARCHAR,
            run_at                                  TIMESTAMP_TZ,
            conversation_id                         INTEGER,
            label_cohort                            VARCHAR,
            is_blind                                BOOLEAN,
            prompt_version                          VARCHAR,
            model                                   VARCHAR,
            prompt_hash                             VARCHAR,
            expected_outcome                        VARCHAR,
            actual_outcome                          VARCHAR,
            outcome_passed                          BOOLEAN,
            expected_question_understanding         INTEGER,
            actual_question_understanding           INTEGER,
            question_understanding_abs_error        INTEGER,
            expected_resource_exhaustion            INTEGER,
            actual_resource_exhaustion              INTEGER,
            resource_exhaustion_abs_error           INTEGER,
            expected_answer_grounding               INTEGER,
            actual_answer_grounding                 INTEGER,
            answer_grounding_abs_error              INTEGER,
            expected_actionability                  INTEGER,
            actual_actionability                    INTEGER,
            actionability_abs_error                 INTEGER,
            char_count                              INTEGER,
            error                                   VARCHAR
        )
    """)
    conn.commit()


def write_validation_results(conn, results: list[dict]):
    """Append one run's results to the ledger. Mirrors write_results() in the classifier."""
    ensure_validation_table(conn)
    cursor = conn.cursor()

    insert_sql = f"""
        INSERT INTO {LEDGER_TABLE} (
            validation_run_id, run_at, conversation_id, label_cohort, is_blind,
            prompt_version, model, prompt_hash,
            expected_outcome, actual_outcome, outcome_passed,
            expected_question_understanding, actual_question_understanding, question_understanding_abs_error,
            expected_resource_exhaustion, actual_resource_exhaustion, resource_exhaustion_abs_error,
            expected_answer_grounding, actual_answer_grounding, answer_grounding_abs_error,
            expected_actionability, actual_actionability, actionability_abs_error,
            char_count, error
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s
        )
    """

    rows = []
    for r in results:
        rows.append((
            r["validation_run_id"], r["run_at"], r["conversation_id"],
            r["label_cohort"], r["is_blind"],
            PROMPT_VERSION, MODEL, PROMPT_HASH,
            r["expected_outcome"], r["actual_outcome"], r["outcome_passed"],
            r["expected_question_understanding"], r["actual_question_understanding"], r["question_understanding_abs_error"],
            r["expected_resource_exhaustion"], r["actual_resource_exhaustion"], r["resource_exhaustion_abs_error"],
            r["expected_answer_grounding"], r["actual_answer_grounding"], r["answer_grounding_abs_error"],
            r["expected_actionability"], r["actual_actionability"], r["actionability_abs_error"],
            r["char_count"], r["error"],
        ))

    cursor.executemany(insert_sql, rows)
    conn.commit()
    print(f"Wrote {len(rows)} rows to {LEDGER_TABLE}")


def _abs_error(expected, actual):
    """Absolute rubric error, or None if either side is missing (no independent label yet)."""
    if expected is None or actual is None:
        return None
    return abs(int(expected) - int(actual))


# ── Validation run ────────────────────────────────────────────────────────────
def run_validation(output_path: str = "validation_results.json"):
    validation_run_id = uuid.uuid4().hex
    run_at = datetime.now(timezone.utc)

    conn = get_snowflake_connection()
    try:
        labels = fetch_validation_labels(conn)
        print(f"Validation run {validation_run_id} — {len(labels)} golden cases "
              f"(prompt_version={PROMPT_VERSION})\n")

        results = []
        for case in labels:
            conv_id = case["conversation_id"]
            expected = case["expected_outcome"]
            is_blind = bool(case["is_blind"])
            tag = " [BLIND]" if is_blind else ""
            print(f"Classifying conversation {conv_id}{tag}...")

            base = {
                "validation_run_id": validation_run_id,
                "run_at": run_at,
                "conversation_id": conv_id,
                "label_cohort": case["label_cohort"],
                "is_blind": is_blind,
                "expected_outcome": expected,
            }
            for dim in RUBRIC_DIMENSIONS:
                base[f"expected_{dim}"] = case[f"expected_{dim}"]

            try:
                content = fetch_conversation(conn, conv_id)
                classification = classify_with_retry(truncate_conversation(content))

                actual = classification.get("outcome")
                result = {
                    **base,
                    "actual_outcome": actual,
                    "outcome_passed": actual == expected,
                    "char_count": len(content),
                    "error": None,
                }
                for dim in RUBRIC_DIMENSIONS:
                    actual_score = classification.get(dim)
                    result[f"actual_{dim}"] = actual_score
                    result[f"{dim}_abs_error"] = _abs_error(case[f"expected_{dim}"], actual_score)

                status = "✅ PASS" if result["outcome_passed"] else "❌ FAIL"
                print(f"  {status} — expected: {expected}, got: {actual}")

            except Exception as e:  # noqa: BLE001 — record failures in the ledger, don't abort the run
                print(f"  ⚠️  Error: {e}")
                result = {
                    **base,
                    "actual_outcome": None,
                    "outcome_passed": False,
                    "char_count": None,
                    "error": str(e),
                }
                for dim in RUBRIC_DIMENSIONS:
                    result[f"actual_{dim}"] = None
                    result[f"{dim}_abs_error"] = None

            results.append(result)

        write_validation_results(conn, results)
    finally:
        conn.close()

    # Local JSON backup (convenience only — Snowflake is the source of truth).
    serializable = [{**r, "run_at": r["run_at"].isoformat()} for r in results]
    Path(output_path).write_text(json.dumps(serializable, indent=2))
    print(f"\nLocal backup written to {output_path}")

    # Headline agreement excludes blind cases; blind cases reported separately.
    scored = [r for r in results if not r["is_blind"]]
    passed = sum(1 for r in scored if r["outcome_passed"])
    print(f"\nOutcome agreement (non-blind): {passed}/{len(scored)}")
    for b in [r for r in results if r["is_blind"]]:
        mark = "✅" if b["outcome_passed"] else "❌"
        print(f"Blind {b['conversation_id']}: {mark} expected {b['expected_outcome']}, got {b['actual_outcome']}")

    return results


if __name__ == "__main__":
    run_validation()

"""
data_loader.py — Snowflake connection and data loading for the eval dashboard.

Pulls from RAG_EVAL.MARTS.FCT_CONVERSATION_OUTCOMES — the final joined quality
layer with outcomes, rubric scores, behavioral/cost signals, and (pending dbt
verification) conversation-level timestamps.
"""

import os

import pandas as pd
import snowflake.connector
import streamlit as st
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv

load_dotenv()


def get_snowflake_connection():
    key_path = os.path.expanduser("~/.snowflake/rsa_key.pem")
    with open(key_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(), password=None, backend=default_backend()
        )
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        account="ZADUWZC-QRC41354",
        user="BUBCHAMPAGNE",
        private_key=private_key_bytes,
        warehouse="DEV_WH",
        database="RAG_EVAL",
        schema="MARTS",
    )


@st.cache_data(ttl=600)
def load_outcomes() -> pd.DataFrame:
    """
    Load all classified conversations from FCT_CONVERSATION_OUTCOMES.
    Cached for 10 minutes so repeated dashboard interactions don't re-query.

    Pulls everything with SELECT * — see TIME_AXIS_CANDIDATES and
    COST_COLUMN_CANDIDATES below for how we adapt to whatever's actually
    present without requiring a schema change before this dashboard runs.
    """
    conn = get_snowflake_connection()
    try:
        query = """
            SELECT *
            FROM RAG_EVAL.MARTS.FCT_CONVERSATION_OUTCOMES
            WHERE is_classified = TRUE
        """
        df = pd.read_sql(query, conn)
    finally:
        conn.close()

    # Snowflake connector returns uppercase column names by default
    df.columns = [c.lower() for c in df.columns]
    return df


# ── Derived / normalized fields ───────────────────────────────────────────────

SUCCESS_OUTCOMES = {
    "success_clean",
    "success_with_correction",
    "success_iterative",
}

# Per methodology §11.3 — the proposed gate, label by label.
GATE_ALLOW = {
    "success_clean",
    "success_with_correction",
    "success_iterative",
}
GATE_BLOCK = {
    "failure_wrong_direction",
    "failure_knowledge_gap",
    "failure_environment",
    "failure_abandoned",
    "inconclusive",
}
# failure_wrong_direction gets its own bucket downstream — it's "block AND flag for
# human review" per §11.3, not just "block."
GATE_REVIEW = {"failure_wrong_direction"}

RUBRIC_DIMENSIONS = [
    "question_understanding",
    "resource_exhaustion",
    "answer_grounding",
    "actionability",
]

# Candidate column names for the conversation-level time axis, in preference
# order. created_at (from STG_CONVERSATIONS, joined in) is the correct axis
# for trend analysis per the methodology — classified_at is batch-run noise.
# If neither created_at nor conversation_created_at exists on the fact table
# yet, we fall back to classified_at and surface a warning in the UI rather
# than failing outright.
TIME_AXIS_CANDIDATES = ["created_at", "conversation_created_at", "classified_at"]

# Candidate cost columns from int_conversation_metrics. total_cost_usd is the
# headline figure from methodology §2; run_count and avg_run_duration_ms
# support the outlier panel.
COST_COLUMN_CANDIDATES = ["total_cost_usd", "run_count", "avg_run_duration_ms"]


def resolve_time_axis(df: pd.DataFrame) -> tuple[str | None, bool]:
    """
    Pick the best available conversation-time column.

    Returns:
        (column_name, is_fallback) — column_name is None if nothing usable
        was found at all. is_fallback is True if we had to drop to
        classified_at, which the caller should warn about in the UI.
    """
    for i, col in enumerate(TIME_AXIS_CANDIDATES):
        if col in df.columns:
            is_fallback = TIME_AXIS_CANDIDATES[i] == "classified_at"
            return col, is_fallback
    return None, False


def has_cost_columns(df: pd.DataFrame) -> bool:
    """Whether the cost/behavioral columns from int_conversation_metrics made it through the join."""
    return "total_cost_usd" in df.columns


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add normalized success/failure tier and gate-decision columns."""
    df = df.copy()

    df["outcome_tier"] = df["outcome"].apply(
        lambda o: "success" if o in SUCCESS_OUTCOMES
        else ("inconclusive" if o == "inconclusive" else "failure")
    )

    def _gate_decision(outcome: str) -> str:
        if outcome in GATE_ALLOW:
            return "allow"
        if outcome in GATE_REVIEW:
            return "block_review"
        return "block"

    df["gate_decision"] = df["outcome"].apply(_gate_decision)

    return df


# ── Concept-drift / validation layer ──────────────────────────────────────────
# Mirrors the Grafana validation panels in Streamlit. The marts may not exist yet,
# or may be too sparse to demo (a single validation run = a flat one-point line), so
# these loaders auto-fall back to a synthetic series and the page can also force it.

CONCEPT_DRIFT_TABLE = "RAG_EVAL.MARTS.FCT_CONCEPT_DRIFT"
VALIDATION_RUNS_TABLE = "RAG_EVAL.MARTS.FCT_VALIDATION_RUNS"

# Force demo data regardless of what's in Snowflake (handy for offline demos).
USE_MOCK_DATA = os.environ.get("USE_MOCK_DATA", "").strip().lower() in ("1", "true", "yes")


def _drift_band(delta: float) -> str:
    """Same banding as fct_concept_drift (PSI 0.1/0.25 convention)."""
    if pd.isna(delta):
        return "unknown"
    a = abs(delta)
    if a < 0.10:
        return "stable"
    if a < 0.25:
        return "moderate"
    return "significant"


def mock_concept_drift() -> pd.DataFrame:
    """Synthetic agreement time series: a stable v1 with an injected dip, then a v2 rotation."""
    base = pd.Timestamp.utcnow().normalize() - pd.Timedelta(weeks=11)

    # (prompt_version, model, agreement) — v1 dips mid-series; v2 starts a fresh baseline.
    series = [
        ("consultation_3f302a86", "claude-sonnet-4-6", 1.00),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.95),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.90),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.92),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.80),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.70),  # significant drift dip
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.85),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.92),
        ("consultation_3f302a86", "claude-sonnet-4-6", 0.95),
        ("consultation_9bf21a04", "claude-sonnet-4-6", 0.83),  # version rotation → new baseline
        ("consultation_9bf21a04", "claude-sonnet-4-6", 0.90),
        ("consultation_9bf21a04", "claude-sonnet-4-6", 0.95),
    ]

    rows = []
    baseline_by_version: dict[str, float] = {}
    for i, (pv, model, agree) in enumerate(series):
        baseline_by_version.setdefault(pv, agree)
        baseline = baseline_by_version[pv]
        delta = agree - baseline
        n_cases = 5
        n_passed = round(agree * n_cases)
        # deterministic-ish rubric match rates that track agreement
        rubric_match = min(1.0, agree + 0.05)
        rubric_mae = round((1 - rubric_match) * 2, 3)
        rows.append({
            "validation_run_id": f"mock_run_{i:02d}",
            "prompt_version": pv,
            "model_version": model,
            "prompt_hash": pv.split("_")[-1],
            "run_at": base + pd.Timedelta(weeks=i),
            "n_cases": n_cases,
            "n_passed": n_passed,
            "error_count": 1 if i == 5 else 0,
            "n_blind_cases": 1,
            "n_blind_passed": 0 if agree < 0.85 else 1,
            "outcome_agreement_rate": agree,
            "blind_agreement_rate": 0.0 if agree < 0.85 else 1.0,
            "baseline_agreement_rate": baseline,
            "agreement_delta_vs_baseline": round(delta, 3),
            "drift_band": _drift_band(delta),
            "question_understanding_match_rate": rubric_match,
            "question_understanding_mae": rubric_mae,
            "resource_exhaustion_match_rate": rubric_match,
            "resource_exhaustion_mae": rubric_mae,
            "answer_grounding_match_rate": rubric_match,
            "answer_grounding_mae": rubric_mae,
            "actionability_match_rate": rubric_match,
            "actionability_mae": rubric_mae,
        })
    return pd.DataFrame(rows)


def mock_validation_runs() -> pd.DataFrame:
    """Synthetic latest-run case table for the pass/fail panel."""
    cases = [
        (530, "success_clean", "success_clean", False, False),
        (643, "inconclusive", "inconclusive", False, False),
        (691, "success_clean", "success_clean", False, False),
        (701, "failure_abandoned", "failure_wrong_direction", False, False),
        (225, "success_with_correction", None, False, True),   # error row
        (724, "failure_knowledge_gap", "success_clean", True, False),  # blind
    ]
    run_at = pd.Timestamp.utcnow().normalize()
    rows = []
    for cid, exp, act, is_blind, is_err in cases:
        rows.append({
            "validation_run_id": "mock_run_11",
            "run_at": run_at,
            "conversation_id": cid,
            "label_cohort": "cohort_2026_06",
            "is_blind": is_blind,
            "prompt_version": "consultation_9bf21a04",
            "expected_outcome": exp,
            "actual_outcome": act,
            "outcome_passed": (act == exp),
            "is_scored": not is_err,
            "is_error": is_err,
        })
    return pd.DataFrame(rows)


def _load_mart(table: str, order_by: str):
    """Load a mart table lowercased, or raise to trigger mock fallback."""
    conn = get_snowflake_connection()
    try:
        df = pd.read_sql(f"SELECT * FROM {table} ORDER BY {order_by}", conn)
    finally:
        conn.close()
    df.columns = [c.lower() for c in df.columns]
    return df


@st.cache_data(ttl=600)
def load_concept_drift() -> tuple[pd.DataFrame, bool]:
    """Return (df, is_mock). Falls back to synthetic data if the mart is missing/empty."""
    if USE_MOCK_DATA:
        return mock_concept_drift(), True
    try:
        df = _load_mart(CONCEPT_DRIFT_TABLE, "run_at")
        if df.empty:
            return mock_concept_drift(), True
        return df, False
    except Exception:
        return mock_concept_drift(), True


@st.cache_data(ttl=600)
def load_validation_runs() -> tuple[pd.DataFrame, bool]:
    """Return (df, is_mock). Falls back to synthetic data if the mart is missing/empty."""
    if USE_MOCK_DATA:
        return mock_validation_runs(), True
    try:
        df = _load_mart(VALIDATION_RUNS_TABLE, "run_at")
        if df.empty:
            return mock_validation_runs(), True
        return df, False
    except Exception:
        return mock_validation_runs(), True
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
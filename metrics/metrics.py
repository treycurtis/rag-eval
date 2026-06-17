"""
metrics.py — Prometheus metrics for the querybot eval pipeline.

All metric definitions live here. Import from this module in classifiers
and pipeline code. The metrics server (server.py) exposes these on /metrics.

Usage:
    from metrics.metrics import (
        CLASSIFIER_LATENCY,
        CLASSIFIER_OUTCOME_TOTAL,
        CLASSIFIER_ERRORS_TOTAL,
        CLASSIFIER_RETRIES_TOTAL,
        PIPELINE_ROWS_FETCHED,
        PIPELINE_INSERT_LATENCY,
        PIPELINE_WATERMARK_AGE_SECONDS,
        QUALITY_SCORE,
        QUALITY_FLAG_DEV_ACKNOWLEDGED,
    )
"""

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry

# ── Registry ──────────────────────────────────────────────────────────────────
# Using default registry so the HTTP server picks everything up automatically.

# ── Classifier metrics ────────────────────────────────────────────────────────

CLASSIFIER_LATENCY = Histogram(
    "querybot_classifier_latency_seconds",
    "Time spent on a single Claude API classification call",
    labelnames=["conversation_type"],  # consultation | generation | modification
    buckets=[1, 2, 5, 10, 20, 30, 60, 120],
)

CLASSIFIER_OUTCOME_TOTAL = Counter(
    "querybot_classifier_outcome_total",
    "Count of classified conversations by type and outcome label",
    labelnames=["conversation_type", "outcome"],
)

CLASSIFIER_ERRORS_TOTAL = Counter(
    "querybot_classifier_errors_total",
    "Count of classification errors by conversation type",
    labelnames=["conversation_type", "error_type"],  # rate_limit | parse_error | timeout | unknown
)

CLASSIFIER_RETRIES_TOTAL = Counter(
    "querybot_classifier_retries_total",
    "Count of retry attempts triggered by rate limiting",
    labelnames=["conversation_type"],
)

# ── Quality signal metrics ────────────────────────────────────────────────────

QUALITY_SCORE = Histogram(
    "querybot_quality_score",
    "Distribution of rubric dimension scores (1-3) per conversation type",
    labelnames=["conversation_type", "dimension"],  # question_understanding | resource_exhaustion | answer_grounding | actionability
    buckets=[1, 1.5, 2, 2.5, 3],
)

QUALITY_FLAG_DEV_ACKNOWLEDGED = Counter(
    "querybot_flag_dev_acknowledged_total",
    "Count of SQL output conversations where querybot proactively flagged dev/prod differences",
    labelnames=["outcome"],
)

# ── Pipeline metrics (Airflow DAG) ────────────────────────────────────────────

PIPELINE_ROWS_FETCHED = Counter(
    "querybot_pipeline_rows_fetched_total",
    "Total rows fetched from Postgres per DAG task",
    labelnames=["table"],  # conversations | conversation_runs | conversation_messages
)

PIPELINE_INSERT_LATENCY = Histogram(
    "querybot_pipeline_insert_latency_seconds",
    "Time spent inserting a chunk into Snowflake",
    labelnames=["table"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

PIPELINE_WATERMARK_AGE_SECONDS = Gauge(
    "querybot_pipeline_watermark_age_seconds",
    "Age of the current pipeline watermark in seconds — how far behind real-time the pipeline is",
)
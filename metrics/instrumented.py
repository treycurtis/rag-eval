"""
instrumented.py — Metrics-wrapped classifier execution.

Drop-in replacement for classify_with_retry in both classifier scripts.
Handles latency tracking, outcome counting, retry counting, and error
classification without touching any prompt or Snowflake logic.

Usage:
    from metrics.instrumented import instrumented_classify

    # Replace:
    classification = classify_with_retry(truncate_conversation(conversations[conv_id]))

    # With:
    classification = instrumented_classify(
        content=truncate_conversation(conversations[conv_id]),
        conversation_type="consultation",   # or "generation" / "modification"
        classify_fn=classify_with_retry,
    )
"""

import logging
import time
from typing import Callable

from metrics.metrics import (
    CLASSIFIER_LATENCY,
    CLASSIFIER_OUTCOME_TOTAL,
    CLASSIFIER_ERRORS_TOTAL,
    CLASSIFIER_RETRIES_TOTAL,
    QUALITY_SCORE,
    QUALITY_FLAG_DEV_ACKNOWLEDGED,
)

logger = logging.getLogger(__name__)

RUBRIC_DIMENSIONS = [
    "question_understanding",
    "resource_exhaustion",
    "answer_grounding",
    "actionability",
]


def instrumented_classify(
    content: str,
    conversation_type: str,
    classify_fn: Callable[[str], dict],
) -> dict:
    
    
    """
    Wrap a classifier call with Prometheus instrumentation.

    Args:
        content:           Truncated conversation string ready for the Claude API.
        conversation_type: One of 'consultation', 'generation', 'modification'.
        classify_fn:       The classify_with_retry function from the caller's module.

    Returns:
        The classification dict returned by classify_fn, unmodified.

    Raises:
        Re-raises any exception from classify_fn after recording the error metric.
    """
    VALID_TYPES = {"consultation", "generation", "modification"}

    if conversation_type not in VALID_TYPES:
        logger.warning(
            f"Unexpected conversation_type '{conversation_type}' — "
            f"expected one of {VALID_TYPES}. Metrics may be unreliable for this call."
        )

    t_start = time.perf_counter()

    try:
        result = classify_fn(content, conversation_type)
    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        CLASSIFIER_LATENCY.labels(conversation_type=conversation_type).observe(elapsed)

        error_type = _classify_error(exc)
        CLASSIFIER_ERRORS_TOTAL.labels(
            conversation_type=conversation_type,
            error_type=error_type,
        ).inc()

        logger.warning(
            f"Classifier error [{error_type}] for {conversation_type} "
            f"after {elapsed:.2f}s: {exc}"
        )
        raise

    elapsed = time.perf_counter() - t_start
    CLASSIFIER_LATENCY.labels(conversation_type=conversation_type).observe(elapsed)

    outcome = result.get("outcome", "unknown")
    CLASSIFIER_OUTCOME_TOTAL.labels(
        conversation_type=conversation_type,
        outcome=outcome,
    ).inc()

    # Rubric dimension scores (1-3)
    for dim in RUBRIC_DIMENSIONS:
        score = result.get(dim)
        if score is not None:
            QUALITY_SCORE.labels(
                conversation_type=conversation_type,
                dimension=dim,
            ).observe(score)

    # flag_dev_acknowledged is SQL output only — skip for consultation
    if "flag_dev_acknowledged" in result and result["flag_dev_acknowledged"] is True:
        QUALITY_FLAG_DEV_ACKNOWLEDGED.labels(outcome=outcome).inc()

    logger.debug(
        f"Classified [{conversation_type}] → {outcome} in {elapsed:.2f}s"
    )

    return result


def _classify_error(exc: Exception) -> str:
    """Map an exception to a coarse error_type label for Prometheus."""
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return "rate_limit"
    if "json" in name or "value" in name:
        return "parse_error"
    if "timeout" in name:
        return "timeout"
    return "unknown"
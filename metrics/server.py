"""
server.py — Lightweight Prometheus metrics server.

Starts a background HTTP server exposing /metrics on the configured port.
Call start_metrics_server() once at the top of a classifier run or DAG task.

Usage:
    from metrics.server import start_metrics_server
    start_metrics_server()  # defaults to port 8000
"""

import logging
import os
from prometheus_client import start_http_server

logger = logging.getLogger(__name__)

_server_started = False


def start_metrics_server(port: int | None = None) -> None:
    """
    Start the Prometheus metrics HTTP server in a background thread.
    Safe to call multiple times — subsequent calls are no-ops.
    Port defaults to METRICS_PORT env var, then 8000.
    """
    global _server_started
    if _server_started:
        return

    port = port or int(os.getenv("METRICS_PORT", "8000"))
    start_http_server(port)
    _server_started = True
    logger.info(f"Prometheus metrics server started on :{port}/metrics")
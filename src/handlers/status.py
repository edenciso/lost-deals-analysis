"""
Lambda Handler: Status & Health Check
======================================
GET /health                   - Service health check (no auth)
GET /status/{session_id}      - Get analysis pipeline status
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

from common import (
    success, not_found, server_error,
    get_table, from_dynamo_item
)

DEALS_TABLE = os.environ.get("DEALS_TABLE", "lostbot-deals-dev")
ANALYSES_TABLE = os.environ.get("ANALYSES_TABLE", "lostbot-analyses-dev")
REPORTS_TABLE = os.environ.get("REPORTS_TABLE", "lostbot-reports-dev")


def handler(event, context):
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    if "/health" in path:
        return _health_check()
    elif "session_id" in path_params:
        return _get_status(path_params["session_id"])
    else:
        return success({"service": "lostbot", "status": "running"})


def _health_check() -> dict:
    """Basic health check."""
    checks = {}
    try:
        get_table(DEALS_TABLE).table_status
        checks["dynamodb"] = "healthy"
    except Exception as e:
        checks["dynamodb"] = f"error: {str(e)}"

    all_healthy = all(v == "healthy" for v in checks.values())
    return success({
        "service": "lostbot-pilot",
        "status": "healthy" if all_healthy else "degraded",
        "checks": checks,
        "version": "1.0.0",
    })


def _get_status(session_id: str) -> dict:
    """Get pipeline status for a session."""
    deals_table = get_table(DEALS_TABLE)
    meta_result = deals_table.get_item(
        Key={"session_id": session_id, "deal_id": "__SESSION_META__"}
    )
    meta = meta_result.get("Item")
    if not meta:
        return not_found(f"Session {session_id} not found")

    meta = from_dynamo_item(meta)

    # Use worker-tracked progress from meta instead of raw table counts
    status = meta.get("status", "unknown")
    deals_analyzed = meta.get("deals_analyzed", 0)
    total_to_analyze = meta.get("total_to_analyze", meta.get("total_deals", 0))

    # Count deals (excluding meta)
    deals_response = deals_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
        Select="COUNT",
    )
    total_items = deals_response.get("Count", 0) - 1  # subtract meta

    # Check for reports
    reports_table = get_table(REPORTS_TABLE)
    reports_response = reports_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
        Select="COUNT",
    )

    return success({
        "session_id": session_id,
        "company_name": meta.get("company_name"),
        "status": status,
        "pipeline": {
            "deals_ingested": total_items,
            "analyses_completed": deals_analyzed,
            "total_to_analyze": total_to_analyze,
            "reports_generated": reports_response.get("Count", 0),
        },
        "ingested_at": meta.get("ingested_at"),
        "analyzed_at": meta.get("analyzed_at"),
        "report_id": meta.get("report_id"),
        "next_step": _suggest_next_step(status, session_id),
    })


def _suggest_next_step(status: str, session_id: str) -> str:
    steps = {
        "ready_for_analysis": f"POST /analyze/batch with session_id: {session_id}",
        "analyzed": f"POST /reports/generate with session_id: {session_id}",
        "report_generated": f"GET /reports/{{report_id}}?session_id={session_id}",
    }
    return steps.get(status, "Check session status")

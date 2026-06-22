"""
Lambda Handler: GTM Bugs
=========================
POST /gtm/bugs               - Identify GTM bugs from analyzed deals
GET  /gtm/playbook/{bug_id}  - Get battle card for specific bug
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

from common import (
    success, bad_request, not_found, server_error,
    get_table, generate_id, utc_now_iso, to_dynamo_item, from_dynamo_item
)
from agents.orchestrator import LostbotOrchestrator

ANALYSES_TABLE = os.environ.get("ANALYSES_TABLE", "lostbot-analyses-dev")
REPORTS_TABLE = os.environ.get("REPORTS_TABLE", "lostbot-reports-dev")


def handler(event, context):
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return success({"message": "OK"})

    if method == "GET" and "bug_id" in path_params:
        return _get_playbook(path_params["bug_id"], event)
    elif method == "POST":
        try:
            body = json.loads(event.get("body", "{}"))
        except json.JSONDecodeError:
            return bad_request("Invalid JSON")
        return _identify_bugs(body)
    else:
        return bad_request("Invalid request")


def _identify_bugs(body: dict) -> dict:
    """Identify GTM bugs from session analyses."""
    session_id = body.get("session_id")
    if not session_id:
        return bad_request("session_id required")

    # Fetch analyses
    analyses_table = get_table(ANALYSES_TABLE)
    response = analyses_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )

    analyses = [
        from_dynamo_item(i).get("result")
        for i in response.get("Items", [])
        if from_dynamo_item(i).get("type") == "single_deal" and "result" in from_dynamo_item(i)
    ]

    if not analyses:
        return not_found(f"No analyses found for session {session_id}. Run /analyze/batch first.")

    orchestrator = LostbotOrchestrator()

    # Identify GTM bugs
    gtm_bugs = orchestrator.identify_gtm_bugs(analyses)

    # Generate playbook
    playbook = orchestrator.generate_playbook(gtm_bugs, analyses)

    # Store results
    reports_table = get_table(REPORTS_TABLE)
    bug_report_id = generate_id("bugs-")
    reports_table.put_item(Item=to_dynamo_item({
        "session_id": session_id,
        "report_id": bug_report_id,
        "type": "gtm_bugs",
        "generated_at": utc_now_iso(),
        "gtm_bugs": gtm_bugs,
        "playbook": playbook,
        "execution_log": orchestrator.execution_log,
    }))

    return success({
        "session_id": session_id,
        "bug_report_id": bug_report_id,
        "gtm_bugs": gtm_bugs,
        "playbook": playbook,
    })


def _get_playbook(bug_id: str, event: dict) -> dict:
    """Get battle card for a specific bug."""
    query_params = event.get("queryStringParameters") or {}
    session_id = query_params.get("session_id")

    if not session_id:
        return bad_request("session_id query parameter required")

    reports_table = get_table(REPORTS_TABLE)
    response = reports_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )

    for item in response.get("Items", []):
        record = from_dynamo_item(item)
        if record.get("type") == "gtm_bugs":
            playbook = record.get("playbook", {})
            for card in playbook.get("battle_cards", []):
                if card.get("bug_id") == bug_id:
                    return success({"bug_id": bug_id, "battle_card": card})

    return not_found(f"Battle card for bug {bug_id} not found")

"""
Lambda Handler: Analyze Deals
==============================
POST /analyze/deal/{deal_id}  - Analyze single deal (sync — fits in 29s)
POST /analyze/batch           - Kick off async batch analysis
Worker mode: invoked async by self, processes all deals with 15min Lambda timeout
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

from common import (
    success, bad_request, not_found, server_error,
    get_table, generate_id, utc_now_iso, to_dynamo_item, from_dynamo_item
)
from agents.orchestrator import LostbotOrchestrator

DEALS_TABLE = os.environ.get("DEALS_TABLE", "lostbot-deals-dev")
ANALYSES_TABLE = os.environ.get("ANALYSES_TABLE", "lostbot-analyses-dev")
WORKER_FUNCTION = os.environ.get("WORKER_FUNCTION", "")


def handler(event, context):
    """Route to single, batch, or worker invocation."""
    # Worker invocation (from async invoke — no API Gateway wrapper)
    if "worker_action" in event:
        return _worker_handler(event)

    path = event.get("path", "")
    method = event.get("httpMethod", "")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return success({"message": "OK"})

    try:
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}
    except json.JSONDecodeError:
        return bad_request("Invalid JSON")

    if "/batch" in path:
        return _start_batch(body, query_params)
    elif "deal_id" in path_params:
        return _analyze_single(path_params["deal_id"], body, query_params)
    else:
        return bad_request("Invalid path")


def _analyze_single(deal_id: str, body: dict, query_params: dict) -> dict:
    """Analyze a single deal (sync — typically fits in 29s)."""
    session_id = body.get("session_id") or query_params.get("session_id")
    if not session_id:
        return bad_request("session_id required (in body or query param)")

    deals_table = get_table(DEALS_TABLE)
    result = deals_table.get_item(Key={"session_id": session_id, "deal_id": deal_id})
    item = result.get("Item")
    if not item:
        return not_found(f"Deal {deal_id} not found in session {session_id}")

    deal_data = from_dynamo_item(item).get("data", {})

    orchestrator = LostbotOrchestrator()
    analysis = orchestrator.analyze_single_deal(deal_data)

    analyses_table = get_table(ANALYSES_TABLE)
    analysis_id = generate_id("ana-")
    analyses_table.put_item(Item=to_dynamo_item({
        "session_id": session_id,
        "analysis_id": analysis_id,
        "deal_id": deal_id,
        "type": "single_deal",
        "analyzed_at": utc_now_iso(),
        "result": analysis,
        "execution_log": orchestrator.execution_log,
    }))

    deals_table.update_item(
        Key={"session_id": session_id, "deal_id": deal_id},
        UpdateExpression="SET #s = :s, analysis_id = :aid",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "analyzed", ":aid": analysis_id},
    )

    return success({
        "session_id": session_id,
        "analysis_id": analysis_id,
        "deal_id": deal_id,
        "analysis": analysis,
    })


def _start_batch(body: dict, query_params: dict) -> dict:
    """Kick off async batch analysis — returns immediately."""
    session_id = body.get("session_id") or query_params.get("session_id")
    if not session_id:
        return bad_request("session_id required")

    # Verify deals exist
    deals_table = get_table(DEALS_TABLE)
    response = deals_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
        Select="COUNT",
    )
    deal_count = response.get("Count", 0) - 1  # subtract meta
    if deal_count <= 0:
        return not_found(f"No deals found for session {session_id}")

    # Update status to processing
    deals_table.update_item(
        Key={"session_id": session_id, "deal_id": "__SESSION_META__"},
        UpdateExpression="SET #s = :s, analysis_started_at = :at, total_to_analyze = :cnt, deals_analyzed = :zero",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "analyzing",
            ":at": utc_now_iso(),
            ":cnt": deal_count,
            ":zero": 0,
        },
    )

    # Invoke self asynchronously as the worker
    lambda_client = boto3.client("lambda")
    function_name = WORKER_FUNCTION or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",  # async — returns 202 immediately
        Payload=json.dumps({
            "worker_action": "batch_analyze",
            "session_id": session_id,
        }),
    )

    return success({
        "session_id": session_id,
        "status": "analyzing",
        "deals_queued": deal_count,
        "message": "Batch analysis started. Poll GET /status/{session_id} for progress.",
        "poll_url": f"/status/{session_id}",
    })


# ============================================
# Worker (runs async, no API Gateway timeout)
# ============================================
def _worker_handler(event):
    """Process batch analysis asynchronously (up to 15 min Lambda timeout)."""
    action = event.get("worker_action")
    session_id = event.get("session_id")

    logger.info(f"Worker started: action={action}, session={session_id}")

    if action != "batch_analyze" or not session_id:
        logger.error(f"Invalid worker event: {event}")
        return

    deals_table = get_table(DEALS_TABLE)
    analyses_table = get_table(ANALYSES_TABLE)

    # Fetch all deals
    response = deals_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )
    items = [from_dynamo_item(i) for i in response.get("Items", [])]
    deals = [i["data"] for i in items if i.get("deal_id") != "__SESSION_META__" and "data" in i]

    logger.info(f"Worker processing {len(deals)} deals")

    orchestrator = LostbotOrchestrator()
    all_analyses = []
    analyzed_count = 0

    for deal in deals:
        try:
            analysis = orchestrator.analyze_single_deal(deal)
            all_analyses.append(analysis)

            analysis_id = generate_id("ana-")
            analyses_table.put_item(Item=to_dynamo_item({
                "session_id": session_id,
                "analysis_id": analysis_id,
                "deal_id": deal["deal_id"],
                "type": "single_deal",
                "analyzed_at": utc_now_iso(),
                "result": analysis,
            }))

            analyzed_count += 1
            logger.info(f"Worker: analyzed {analyzed_count}/{len(deals)} - {deal['deal_id']}")

            # Update progress in session meta
            deals_table.update_item(
                Key={"session_id": session_id, "deal_id": "__SESSION_META__"},
                UpdateExpression="SET deals_analyzed = :cnt",
                ExpressionAttributeValues={":cnt": analyzed_count},
            )

        except Exception as e:
            logger.error(f"Worker error on deal {deal.get('deal_id')}: {e}")
            continue

    # Store batch summary
    batch_id = generate_id("batch-")
    preventable_count = sum(
        1 for a in all_analyses
        if a.get("root_cause_analysis", {}).get("preventable")
    )
    total_arr = sum(a.get("arr_value", 0) for a in all_analyses)
    preventable_arr = sum(
        a.get("arr_value", 0) for a in all_analyses
        if a.get("root_cause_analysis", {}).get("preventable")
    )

    analyses_table.put_item(Item=to_dynamo_item({
        "session_id": session_id,
        "analysis_id": batch_id,
        "type": "batch_analysis",
        "analyzed_at": utc_now_iso(),
        "total_deals": len(deals),
        "deals_analyzed": len(all_analyses),
        "summary": {
            "preventable_count": preventable_count,
            "total_arr_analyzed": total_arr,
            "preventable_arr": preventable_arr,
        },
        "execution_log": orchestrator.execution_log,
    }))

    # Mark session complete
    deals_table.update_item(
        Key={"session_id": session_id, "deal_id": "__SESSION_META__"},
        UpdateExpression="SET #s = :s, batch_analysis_id = :bid, analyzed_at = :at, deals_analyzed = :cnt",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "analyzed",
            ":bid": batch_id,
            ":at": utc_now_iso(),
            ":cnt": analyzed_count,
        },
    )

    logger.info(f"Worker completed: {analyzed_count} deals, {preventable_count} preventable, ${preventable_arr:,.0f} ARR")

"""
Lambda Handler: Ingest Deals
=============================
POST /deals/ingest      - Ingest single deal or small batch
POST /deals/ingest/batch - Ingest full JSON file with multiple deals
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

from common import (
    success, created, bad_request, server_error,
    get_table, generate_id, utc_now_iso, to_dynamo_item
)

DEALS_TABLE = os.environ.get("DEALS_TABLE", "lostbot-deals-dev")


def validate_deal(deal: dict) -> list:
    """Validate a single deal record. Returns list of errors."""
    errors = []
    required = ["deal_id", "company", "arr_value", "stage", "ae_stated_reason"]
    for field in required:
        if field not in deal:
            errors.append(f"Missing required field: {field}")
    if deal.get("stage") not in ("closedlost", "closedwon", None):
        errors.append(f"Invalid stage: {deal.get('stage')}. Must be closedlost or closedwon")
    if "arr_value" in deal and not isinstance(deal["arr_value"], (int, float)):
        errors.append("arr_value must be a number")
    return errors


def handler(event, context):
    """Handle deal ingestion requests."""
    path = event.get("path", "")
    method = event.get("httpMethod", "")

    if method == "OPTIONS":
        return success({"message": "OK"})

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return bad_request("Invalid JSON in request body")

    if "/batch" in path:
        return _ingest_batch(body)
    else:
        return _ingest_single_or_array(body)


def _ingest_batch(body: dict) -> dict:
    """Ingest a full session with multiple deals."""
    session_id = body.get("session_id") or generate_id("sess-")
    company_name = body.get("company_name", "Unknown")
    deals = body.get("deals", [])

    if not deals:
        return bad_request("No deals provided. Include 'deals' array in request body.")

    if len(deals) > 50:
        return bad_request("Maximum 50 deals per batch. Split into multiple requests.")

    table = get_table(DEALS_TABLE)
    ingested = []
    errors = []

    for i, deal in enumerate(deals):
        validation_errors = validate_deal(deal)
        if validation_errors:
            errors.append({"index": i, "deal_id": deal.get("deal_id", "unknown"), "errors": validation_errors})
            continue

        item = {
            "session_id": session_id,
            "deal_id": deal["deal_id"],
            "company_name": company_name,
            "ingested_at": utc_now_iso(),
            "status": "ingested",
            "data": deal,
        }

        table.put_item(Item=to_dynamo_item(item))
        ingested.append(deal["deal_id"])

    # Store session metadata
    table.put_item(Item=to_dynamo_item({
        "session_id": session_id,
        "deal_id": "__SESSION_META__",
        "company_name": company_name,
        "analyst_name": body.get("analyst_name", ""),
        "industry": body.get("industry", ""),
        "total_deals": len(ingested),
        "ingested_at": utc_now_iso(),
        "status": "ready_for_analysis",
    }))

    return created({
        "session_id": session_id,
        "company_name": company_name,
        "deals_ingested": len(ingested),
        "deals_failed": len(errors),
        "ingested_deal_ids": ingested,
        "errors": errors if errors else None,
        "next_step": f"POST /analyze/batch with session_id: {session_id}",
    })


def _ingest_single_or_array(body: dict) -> dict:
    """Ingest a single deal or small array."""
    if "deals" in body:
        return _ingest_batch(body)

    # Single deal
    session_id = body.get("session_id") or generate_id("sess-")
    deal = body.get("deal", body)

    validation_errors = validate_deal(deal)
    if validation_errors:
        return bad_request(f"Validation errors: {', '.join(validation_errors)}")

    table = get_table(DEALS_TABLE)
    item = {
        "session_id": session_id,
        "deal_id": deal["deal_id"],
        "company_name": body.get("company_name", deal.get("company", "Unknown")),
        "ingested_at": utc_now_iso(),
        "status": "ingested",
        "data": deal,
    }
    table.put_item(Item=to_dynamo_item(item))

    return created({
        "session_id": session_id,
        "deal_id": deal["deal_id"],
        "status": "ingested",
        "next_step": f"POST /analyze/deal/{deal['deal_id']}?session_id={session_id}",
    })

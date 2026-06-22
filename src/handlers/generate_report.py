"""
Lambda Handler: Generate Report
================================
POST /reports/generate     - Kick off async report generation
GET  /reports/{report_id}  - Retrieve generated report
Worker mode: invoked async, runs GTM bugs + playbook + synthesis
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
REPORTS_TABLE = os.environ.get("REPORTS_TABLE", "lostbot-reports-dev")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
WORKER_FUNCTION = os.environ.get("WORKER_FUNCTION", "")


def handler(event, context):
    # Worker invocation (async, no API Gateway)
    if "worker_action" in event:
        return _worker_handler(event)

    method = event.get("httpMethod", "")
    path_params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return success({"message": "OK"})

    if method == "GET" and "report_id" in path_params:
        return _get_report(path_params["report_id"], event)
    elif method == "POST":
        try:
            body = json.loads(event.get("body", "{}"))
        except json.JSONDecodeError:
            return bad_request("Invalid JSON")
        return _start_report(body)
    else:
        return bad_request("Invalid request")


def _start_report(body: dict) -> dict:
    """Kick off async report generation — returns immediately."""
    session_id = body.get("session_id")
    if not session_id:
        return bad_request("session_id required")

    # Verify analyses exist
    analyses_table = get_table(ANALYSES_TABLE)
    response = analyses_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
        Select="COUNT",
    )
    if response.get("Count", 0) == 0:
        return not_found(f"No analyses found for session {session_id}. Run /analyze/batch first.")

    report_id = generate_id("rpt-")

    # Update session status
    deals_table = get_table(DEALS_TABLE)
    deals_table.update_item(
        Key={"session_id": session_id, "deal_id": "__SESSION_META__"},
        UpdateExpression="SET #s = :s, report_id = :rid, report_started_at = :at",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "generating_report",
            ":rid": report_id,
            ":at": utc_now_iso(),
        },
    )

    # Store placeholder report
    reports_table = get_table(REPORTS_TABLE)
    reports_table.put_item(Item=to_dynamo_item({
        "session_id": session_id,
        "report_id": report_id,
        "status": "generating",
        "started_at": utc_now_iso(),
    }))

    # Invoke self async
    lambda_client = boto3.client("lambda")
    function_name = WORKER_FUNCTION or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps({
            "worker_action": "generate_report",
            "session_id": session_id,
            "report_id": report_id,
            "company_name": body.get("company_name", ""),
        }),
    )

    return success({
        "session_id": session_id,
        "report_id": report_id,
        "status": "generating",
        "message": "Report generation started. Poll GET /reports/{report_id}?session_id={session_id} for result.",
        "poll_url": f"/reports/{report_id}?session_id={session_id}",
    })


def _get_report(report_id: str, event: dict) -> dict:
    """Retrieve a generated report."""
    query_params = event.get("queryStringParameters") or {}
    session_id = query_params.get("session_id")
    if not session_id:
        return bad_request("session_id query parameter required")

    reports_table = get_table(REPORTS_TABLE)
    result = reports_table.get_item(Key={"session_id": session_id, "report_id": report_id})
    item = result.get("Item")
    if not item:
        return not_found(f"Report {report_id} not found")

    report = from_dynamo_item(item)

    # Generate fresh presigned URL
    if REPORTS_BUCKET and report.get("status") == "completed":
        try:
            s3 = boto3.client("s3")
            s3_key = f"reports/{session_id}/{report_id}.json"
            presigned_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": REPORTS_BUCKET, "Key": s3_key},
                ExpiresIn=3600,
            )
            report["download_url"] = presigned_url
        except Exception:
            pass

    return success(report)


# ============================================
# Worker (runs async, 15 min timeout)
# ============================================
def _worker_handler(event):
    action = event.get("worker_action")
    session_id = event.get("session_id")
    report_id = event.get("report_id")

    logger.info(f"Report worker started: session={session_id}, report={report_id}")

    if action != "generate_report" or not session_id:
        logger.error(f"Invalid worker event: {event}")
        return

    deals_table = get_table(DEALS_TABLE)
    analyses_table = get_table(ANALYSES_TABLE)
    reports_table = get_table(REPORTS_TABLE)

    # Fetch deals + meta
    deals_response = deals_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )
    items = [from_dynamo_item(i) for i in deals_response.get("Items", [])]
    deals = [i["data"] for i in items if i.get("deal_id") != "__SESSION_META__" and "data" in i]
    meta = next((i for i in items if i.get("deal_id") == "__SESSION_META__"), {})
    company_name = event.get("company_name") or meta.get("company_name", "Unknown")

    # Fetch existing analyses
    analyses_response = analyses_table.query(
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )
    existing_analyses = [
        from_dynamo_item(i).get("result")
        for i in analyses_response.get("Items", [])
        if from_dynamo_item(i).get("type") == "single_deal" and "result" in from_dynamo_item(i)
    ]

    orchestrator = LostbotOrchestrator()

    # Run analyses if none exist
    if not existing_analyses:
        logger.info(f"No existing analyses. Running for {len(deals)} deals.")
        for deal in deals:
            analysis = orchestrator.analyze_single_deal(deal)
            existing_analyses.append(analysis)
    else:
        logger.info(f"Using {len(existing_analyses)} existing analyses.")

    # GTM bugs
    gtm_bugs = orchestrator.identify_gtm_bugs(existing_analyses)

    # Playbook
    playbook = orchestrator.generate_playbook(gtm_bugs, existing_analyses)

    # Executive report
    executive_report = orchestrator.synthesize_report(
        company_name, existing_analyses, gtm_bugs, playbook
    )

    # Assemble complete report
    complete_report = {
        "report_id": report_id,
        "session_id": session_id,
        "company_name": company_name,
        "status": "completed",
        "generated_at": utc_now_iso(),
        "executive_report": executive_report,
        "deal_analyses": existing_analyses,
        "gtm_bugs": gtm_bugs,
        "gtm_bugs_count": len(gtm_bugs.get("bugs", [])),
        "playbook": playbook,
        "metadata": {
            "total_deals": len(deals),
            "deals_analyzed": len(existing_analyses),
            "analyst_name": meta.get("analyst_name", ""),
            "industry": meta.get("industry", ""),
        },
        "execution_log": orchestrator.execution_log,
    }

    # Store to DynamoDB
    reports_table.put_item(Item=to_dynamo_item(complete_report))

    # Store JSON to S3
    if REPORTS_BUCKET:
        try:
            s3 = boto3.client("s3")
            s3_key = f"reports/{session_id}/{report_id}.json"
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=s3_key,
                Body=json.dumps(complete_report, indent=2, default=str),
                ContentType="application/json",
            )
            logger.info(f"Report saved to s3://{REPORTS_BUCKET}/{s3_key}")
        except Exception as e:
            logger.warning(f"S3 upload failed: {e}")

    # Update session status
    deals_table.update_item(
        Key={"session_id": session_id, "deal_id": "__SESSION_META__"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "report_generated"},
    )

    logger.info(f"Report {report_id} completed for {company_name}")

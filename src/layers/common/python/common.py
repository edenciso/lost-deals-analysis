"""Common utilities shared across all Lambda functions."""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# ============================================
# DynamoDB Helpers
# ============================================
def get_dynamodb_resource():
    return boto3.resource("dynamodb")


def get_table(table_name: str):
    return get_dynamodb_resource().Table(table_name)


def generate_id(prefix: str = "") -> str:
    short_id = str(uuid.uuid4())[:8]
    return f"{prefix}{short_id}" if prefix else short_id


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal types from DynamoDB."""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o) if o % 1 else int(o)
        return super().default(o)


def to_dynamo_item(data: dict) -> dict:
    """Convert floats to Decimal for DynamoDB."""
    return json.loads(json.dumps(data), parse_float=Decimal)


def from_dynamo_item(item: dict) -> dict:
    """Convert DynamoDB item back to standard Python types."""
    return json.loads(json.dumps(item, cls=DecimalEncoder))


# ============================================
# API Gateway Response Helpers
# ============================================
def api_response(status_code: int, body: Any, headers: dict = None) -> dict:
    default_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    }
    if headers:
        default_headers.update(headers)
    return {
        "statusCode": status_code,
        "headers": default_headers,
        "body": json.dumps(body, cls=DecimalEncoder) if not isinstance(body, str) else body,
    }


def success(body: Any) -> dict:
    return api_response(200, body)


def created(body: Any) -> dict:
    return api_response(201, body)


def bad_request(message: str) -> dict:
    return api_response(400, {"error": message})


def not_found(message: str = "Resource not found") -> dict:
    return api_response(404, {"error": message})


def server_error(message: str = "Internal server error") -> dict:
    return api_response(500, {"error": message})


# ============================================
# Bedrock Client
# ============================================
def get_bedrock_client():
    from botocore.config import Config
    return boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("BEDROCK_REGION", "us-east-1"),
        config=Config(
            read_timeout=600,
            connect_timeout=10,
            retries={"max_attempts": 2, "mode": "adaptive"},
        ),
    )


def invoke_claude(
    prompt: str,
    system: str = "",
    model_id: str = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    thinking_budget: int = 0,
) -> dict:
    """Invoke Claude via Bedrock."""
    client = get_bedrock_client()
    model = model_id or os.environ.get(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )

    messages = [{"role": "user", "content": prompt}]

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }

    if system:
        body["system"] = system

    if thinking_budget > 0:
        body["anthropic_version"] = "bedrock-2025-01-24"
        body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        body["temperature"] = 1
    else:
        body["temperature"] = temperature

    logger.info(f"Invoking Bedrock model={model}, thinking={'on' if thinking_budget else 'off'}")

    response = client.invoke_model(
        modelId=model,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )

    result = json.loads(response["body"].read())

    # Extract text from content blocks
    text_parts = []
    thinking_parts = []
    for block in result.get("content", []):
        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "thinking":
            thinking_parts.append(block.get("text", ""))

    text = "\n".join(text_parts)
    if not text:
        # Fallback: try to get any text from the response
        logger.warning(f"No text blocks found. Raw content: {json.dumps(result.get('content', []))[:500]}")
        text = str(result.get("content", ""))

    return {
        "text": text,
        "thinking": "\n".join(thinking_parts) if thinking_parts else None,
        "usage": result.get("usage", {}),
        "model": model,
    }


def invoke_claude_json(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
    thinking_budget: int = 0,
) -> dict:
    """Invoke Claude and parse JSON response."""
    json_system = (system + "\n\n" if system else "") + (
        "CRITICAL: Respond ONLY with valid JSON. No markdown fences, no backticks, "
        "no explanation outside the JSON structure. Start with { and end with }."
    )

    result = invoke_claude(
        prompt=prompt,
        system=json_system,
        max_tokens=max_tokens,
        thinking_budget=thinking_budget,
    )

    text = result["text"].strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last ``` line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.endswith("```"):
        text = text[: text.rfind("```")].strip()

    # Find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}\nRaw text (first 500 chars): {text[:500]}")
        # Return the raw text as a fallback
        parsed = {"raw_text": text, "parse_error": str(e)}

    return {"data": parsed, "thinking": result.get("thinking"), "usage": result["usage"]}

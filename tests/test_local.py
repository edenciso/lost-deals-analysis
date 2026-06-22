#!/usr/bin/env python3
"""
Local Test Script
==================
Test Lambda handlers locally without SAM.
Requires: pip install boto3 moto
Usage: python tests/test_local.py
"""

import json
import os
import sys

# Set env vars before imports
os.environ["DEALS_TABLE"] = "lostbot-deals-dev"
os.environ["ANALYSES_TABLE"] = "lostbot-analyses-dev"
os.environ["REPORTS_TABLE"] = "lostbot-reports-dev"
os.environ["REPORTS_BUCKET"] = "lostbot-reports-test"
os.environ["BEDROCK_MODEL_ID"] = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
os.environ["LOG_LEVEL"] = "DEBUG"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "layers", "common", "python"))


def make_event(method, path, body=None, path_params=None, query_params=None):
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body else None,
        "pathParameters": path_params,
        "queryStringParameters": query_params,
        "headers": {"Content-Type": "application/json"},
    }


def test_health():
    from handlers.status import handler
    event = make_event("GET", "/health")
    result = handler(event, None)
    print(f"Health: {result['statusCode']}")
    print(json.dumps(json.loads(result["body"]), indent=2))


def test_schema_validation():
    with open("sample-data/demo_10_deals.json") as f:
        data = json.load(f)
    print(f"Sample data loaded: {len(data['deals'])} deals")
    print(f"Company: {data['company_name']}")
    for d in data["deals"]:
        assert "deal_id" in d, f"Missing deal_id"
        assert "company" in d, f"Missing company"
        assert "arr_value" in d, f"Missing arr_value"
        assert "stage" in d, f"Missing stage"
    print("Schema validation: PASS")


def test_agent_definitions():
    from agents.agent_definitions import list_agents, get_agent
    agents = list_agents()
    print(f"\nAgents available: {len(agents)}")
    for a in agents:
        print(f"  • {a['name']}: {a['description'][:60]}...")
        agent = get_agent(a["name"].replace(" ", "_").lower())
        assert "system_prompt" in agent
    print("Agent definitions: PASS")


if __name__ == "__main__":
    print("=" * 50)
    print("  LOSTBOT LOCAL TESTS")
    print("=" * 50)

    test_schema_validation()
    test_agent_definitions()
    print("\nAll local tests passed!")
    print("\nTo test with DynamoDB + Bedrock, use: sam local start-api")

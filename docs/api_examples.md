# Lostbot API — curl Examples

Replace `$API` with your API Gateway URL and `$TOKEN` with your Cognito ID token.

```bash
export API=https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/dev
export TOKEN=eyJraWQ...
```

## Health Check (no auth)

```bash
curl -s $API/health | python3 -m json.tool
```

## Ingest Deals

```bash
curl -s -X POST $API/deals/ingest/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @sample-data/demo_10_deals.json | python3 -m json.tool
```

## Analyze Batch

```bash
curl -s -X POST $API/analyze/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "YOUR_SESSION_ID"}' | python3 -m json.tool
```

## Generate Report

```bash
curl -s -X POST $API/reports/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "YOUR_SESSION_ID"}' | python3 -m json.tool
```

## Get Report

```bash
curl -s "$API/reports/YOUR_REPORT_ID?session_id=YOUR_SESSION_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

## GTM Bugs

```bash
curl -s -X POST $API/gtm/bugs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "YOUR_SESSION_ID"}' | python3 -m json.tool
```

## Session Status

```bash
curl -s "$API/status/YOUR_SESSION_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

## Full Pipeline (one-liner)

```bash
SID=$(curl -s -X POST $API/deals/ingest/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @sample-data/demo_10_deals.json | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])") && \
echo "Session: $SID" && \
curl -s -X POST $API/analyze/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\"}" > /dev/null && \
curl -s -X POST $API/reports/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\"}" | python3 -m json.tool
```

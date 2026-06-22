# ValueOS Lost Deals Revenue Analysis — AWS SAM Deployment Package

**AI-Powered Lost Deal Revenue Analysis Engine**  
AWS Serverless Agentic workflow using AWS Bedrock + Claude Sonnet 4.5 for real-time deal loss forensics.

---

## Architecture

```
Client JSON  →  API Gateway  →  Lambda Functions  →  DynamoDB
                    │                   │
                    │              Bedrock (Claude Sonnet 4.5)
                    │                   │
                Cognito Auth      Multi-Agent Pipeline
                                       │
                               ┌───────┼───────────────┐
                               │       │               │
                          Root Cause  Revenue      Product
                          Analyst    Assessor     Feedback
                               │       │               │
                               └───────┼───────────────┘
                                       │
                               ┌───────┼───────┐
                               │               │
                           GTM Bug         Playbook
                           Detector       Generator
                               │               │
                               └───────┼───────┘
                                       │
                                  Report
                                Synthesizer
                                       │
                                  S3 (Reports)
```

### 6 Specialized AI Agents

| Agent | Purpose |
|-------|---------|
| **Root Cause Analyst** | Finds TRUE root cause vs AE-stated reason |
| **Revenue Impact Assessor** | Quantifies financial impact + win-back potential |
| **Product Feedback Extractor** | Extracts engineering-actionable product gaps |
| **GTM Bug Detector** | Identifies systematic sales process failures |
| **Playbook Generator** | Creates battle cards + winning playbooks |
| **Report Synthesizer** | Produces board-ready executive report |

---

## Prerequisites

- AWS CLI v2 configured with credentials
- AWS SAM CLI (`pip install aws-sam-cli`)
- Python 3.12+
- Amazon Bedrock access to **Claude Sonnet 4.5** (model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`)

### Enable Bedrock Model Access

1. Go to AWS Console → Amazon Bedrock → Model access
2. Request access for **Anthropic Claude Sonnet 4.5**
3. Wait for approval (usually instant for Anthropic models)

---

## Quick Start (< 15 minutes)

### 1. Build & Deploy

```bash
# Build
sam build

# Deploy (first time — interactive)
sam deploy --guided

# Deploy (subsequent — uses samconfig.toml)
sam deploy
```

### 2. Note Your Outputs

After deployment, SAM outputs:
```
ApiUrl:            https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/dev
UserPoolId:        us-east-1_XXXXXXXXX
UserPoolClientId:  xxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 3. Create a Demo User

```bash
# Create user
aws cognito-idp admin-create-user \
  --user-pool-id YOUR_USER_POOL_ID \
  --username demo@company.com \
  --temporary-password TempPass123! \
  --user-attributes Name=email,Value=demo@company.com

# Set permanent password
aws cognito-idp admin-set-user-password \
  --user-pool-id YOUR_USER_POOL_ID \
  --username demo@company.com \
  --password DemoPass123! \
  --permanent
```

### 4. Get Auth Token

```bash
aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id YOUR_CLIENT_ID \
  --auth-parameters USERNAME=demo@company.com,PASSWORD=DemoPass123!

# Copy the IdToken from the response
```

### 5. Run the Demo

```bash
python demo_runner.py \
  --api-url https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/dev \
  --token YOUR_ID_TOKEN
```

---

## API Reference

All endpoints require `Authorization: Bearer <cognito_token>` header (except `/health`).

### Ingest Deals

```bash
# Batch ingest (recommended)
POST /deals/ingest/batch
Body: contents of sample-data/demo_10_deals.json

# Response
{
  "session_id": "sess-a1b2c3d4",
  "deals_ingested": 10,
  "next_step": "POST /analyze/batch with session_id: sess-a1b2c3d4"
}
```

### Analyze Deals

```bash
# Batch analyze all deals in session
POST /analyze/batch
Body: { "session_id": "sess-a1b2c3d4" }

# Analyze single deal
POST /analyze/deal/{deal_id}?session_id=sess-a1b2c3d4
```

### Generate Report

```bash
# Generate full executive report
POST /reports/generate
Body: { "session_id": "sess-a1b2c3d4" }

# Retrieve report
GET /reports/{report_id}?session_id=sess-a1b2c3d4
```

### GTM Bugs & Playbook

```bash
# Identify GTM bugs
POST /gtm/bugs
Body: { "session_id": "sess-a1b2c3d4" }

# Get specific battle card
GET /gtm/playbook/{bug_id}?session_id=sess-a1b2c3d4
```

### Status

```bash
# Health check (no auth)
GET /health

# Session status
GET /status/{session_id}
```

---

## FDE Live Demo Workflow

**Total time: ~10 minutes with pre-deployed stack**

1. **Before the call**: Deploy stack, create user, get token
2. **On the call**:
   - Show sample data structure (2 min)
   - Run `demo_runner.py` — ingests + analyzes + generates report (5-8 min)
   - Walk through executive summary, GTM bugs, win-back opportunities
   - Show the delta between AE-stated reasons vs AI root causes
3. **After the call**: Share the full JSON report download link

### Using Client's Own Data

Have the client export their closed/lost deals into the JSON schema format (see `src/schemas/deals_input_schema.json`). Minimum required fields per deal:

```json
{
  "deal_id": "CRM-123",
  "company": "Prospect Corp",
  "arr_value": 50000,
  "stage": "closedlost",
  "ae_stated_reason": "Price too high"
}
```

Richer data (call transcripts, email threads, contacts) = dramatically better analysis.

---

## Project Structure

```
lostbot-sam-project/
├── template.yaml              # SAM/CloudFormation template
├── samconfig.toml             # SAM deployment config
├── demo_runner.py             # FDE demo CLI tool
├── sample-data/
│   └── demo_10_deals.json     # 10 realistic sample deals
├── src/
│   ├── requirements.txt
│   ├── handlers/              # Lambda function handlers
│   │   ├── ingest_deals.py    # POST /deals/ingest
│   │   ├── analyze_deals.py   # POST /analyze/*
│   │   ├── generate_report.py # POST /reports/generate
│   │   ├── gtm_bugs.py        # POST /gtm/bugs
│   │   └── status.py          # GET /health, /status
│   ├── agents/                # AI agent definitions
│   │   ├── agent_definitions.py  # 6 specialized agent prompts
│   │   └── orchestrator.py    # Multi-agent pipeline coordinator
│   ├── schemas/
│   │   └── deals_input_schema.json  # JSON schema for deal input
│   └── layers/
│       └── common/
│           ├── requirements.txt
│           └── python/
│               └── common.py  # Shared utilities (Bedrock, DynamoDB, API helpers)
├── tests/
│   └── test_local.py          # Local testing script
└── docs/
    └── api_examples.md        # curl examples for every endpoint
```

---

## Cost Estimate

| Resource | Monthly Cost (10 demos/month) |
|----------|-------------------------------|
| Lambda | ~$2 (within free tier) |
| API Gateway | ~$1 |
| DynamoDB | ~$1 (on-demand) |
| S3 | < $1 |
| Bedrock (Claude) | ~$30-50 (10 sessions × 10 deals × 6 agents) |
| Cognito | Free tier |
| **Total** | **~$35-55/month** |

---

## Cleanup

```bash
# Delete the stack
sam delete --stack-name lostbot-pilot

# Or manually
aws cloudformation delete-stack --stack-name lostbot-pilot
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Bedrock AccessDeniedException | Enable Claude Sonnet 4.5 in Bedrock console → Model access |
| Lambda timeout | Increase timeout in template.yaml (max 900s). Batch analysis of 10 deals takes 3-5 min. |
| 403 from API Gateway | Check Cognito token is valid and not expired (1 hour default) |
| DynamoDB throughput | Table uses PAY_PER_REQUEST — no throttling unless extreme load |

---

## Extending for Production

- Add Step Functions for long-running batch analysis (>15 min)
- Add SNS/SES for email report delivery
- Add CloudWatch dashboards for usage monitoring
- Integrate with real CRM via API Gateway → Lambda → CRM API pattern
- Add S3 presigned URLs for direct file upload (large transcripts)

## License
Proprietary — ValueLayer 2026. All rights reserved.

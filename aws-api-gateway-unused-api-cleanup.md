# AWS API Gateway — Unused API Cleanup: Design & Process Document

**Version:** 2.0  
**Date:** April 13, 2026  
**Status:** Active  
**Audience:** Platform Engineering, Cloud Infrastructure, DevOps Teams

---

## Table of Contents

1. [Overview & Goals](#1-overview--goals)
2. [Definition of "Unused"](#2-definition-of-unused)
3. [Discovery Process](#3-discovery-process)
4. [Analysis Methodology](#4-analysis-methodology)
5. [Classification Framework](#5-classification-framework)
6. [Stakeholder Communication & Approval](#6-stakeholder-communication--approval)
7. [Safe Cleanup Process](#7-safe-cleanup-process)
8. [Rollback & Recovery Plan](#8-rollback--recovery-plan)
9. [Automation Approach](#9-automation-approach)
10. [Governance & Ongoing Monitoring](#10-governance--ongoing-monitoring)

---

## 1. Overview & Goals

### 1.1 Business Drivers

Unused APIs in AWS API Gateway accumulate silently over time and create several organisational risks:

| Driver | Description |
|---|---|
| **Cost** | Each API Gateway stage with custom domains, WAF associations, or data transfer incurs charges even with zero invocations. |
| **Security** | Orphaned APIs with lingering IAM policies, API keys, or Lambda integrations expand the attack surface unnecessarily. |
| **Operational overhead** | Teams spend time investigating alerts, logs, and quota limits for APIs that serve no traffic. |
| **Compliance** | Unmanaged endpoints may violate internal security policies or regulatory requirements (e.g. PCI-DSS, SOC2). |

### 1.2 Scope

This process applies to all three API Gateway types across **all AWS regions and all accounts** in the organisation:

- **REST APIs** (v1)
- **HTTP APIs** (v2)
- **WebSocket APIs** (v2)

### 1.3 Success Metrics

- Number of APIs identified as unused
- Number of APIs safely decommissioned
- Estimated monthly cost saving (USD)
- Reduction in API Gateway quota usage per region
- Zero production incidents caused by accidental deletion

---

## 2. Definition of "Unused"

An API is considered **unused** if it meets one or more of the following criteria over a configurable look-back window (default: **90 days**).

### 2.1 Primary Signals

| Signal | CloudWatch Metric | Threshold |
|---|---|---|
| Zero invocations | `Count` | `SUM == 0` over look-back window |
| No successful responses | `5XXError`, `4XXError` | All requests erroring (no healthy traffic) |
| No integration latency recorded | `IntegrationLatency` | `SampleCount == 0` |

### 2.2 Secondary Signals

- **No deployment**: API has no active stage or deployment
- **No usage plan / API key**: REST API has no usage plan attached (suggests it was never integrated into a client)
- **No custom domain**: API has never been given a friendly DNS name
- **No CloudWatch log group**: Access logging was never configured
- **No WAF association**: API was never protected, suggesting it may be a forgotten test API

### 2.3 Look-back Window

The default look-back window is **90 days**. Teams can override this per API via the `last-reviewed` tag. APIs tagged `lifecycle: protected` are excluded from automated cleanup.

---

## 3. Discovery Process

### 3.1 Prerequisites

- AWS CLI v2 installed and configured
- Appropriate IAM permissions (see §3.2)
- AWS Organizations access (for multi-account discovery)

### 3.2 Required IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "apigateway:GET",
        "apigatewayv2:GET",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "logs:DescribeLogGroups",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "tag:GetResources",
        "config:SelectAggregateResourceConfig",
        "account:ListRegions",
        "organizations:ListAccounts"
      ],
      "Resource": "*"
    }
  ]
}
```

### 3.3 AWS Config Aggregator Query

For organisations using AWS Config, the automation uses the following advanced query to retrieve all API Gateway resources in one call:

```sql
SELECT
  resourceId,
  resourceName,
  resourceType,
  awsRegion,
  accountId,
  configuration,
  tags
WHERE
  resourceType IN (
    'AWS::ApiGateway::RestApi',
    'AWS::ApiGatewayV2::Api'
  )
```

> Discovery across all accounts and regions is performed automatically by **Lambda: Scanner** (see §9.2). No manual CLI steps are required.

---

## 4. Analysis Methodology

The automation pipeline analyses each discovered API against the following signals. All data collection is performed automatically by **Lambda: Scanner** (see §9.2).

### 4.1 Invocation Count (CloudWatch)

The scanner pulls the `Count` metric from the `AWS/ApiGateway` namespace over the configured look-back window (default: 90 days). A `Sum` of `0` or an empty `Datapoints` array means no traffic in the period.

### 4.2 Access Log Analysis

If access logging is enabled on a stage, the scanner queries CloudWatch Logs Insights to confirm zero real requests by counting log entries in the look-back window.

### 4.3 Stages and Deployments

The scanner checks whether each REST API has at least one active stage and deployment. An API with **no stages** or **no deployments** is an immediate candidate for deletion.

### 4.4 Usage Plans

The scanner checks whether each REST API is referenced by a usage plan. APIs not attached to any usage plan have never been formally integrated into a client workflow.

### 4.5 Cost Attribution

Cost data is surfaced via the DynamoDB inventory table and reported in the quarterly cost saving report (§10.3). The automation tags each API record with its estimated monthly cost contribution.

---

## 5. Classification Framework

Each API discovered in §3 is assigned one of four tiers after analysis in §4.

### Tier Definitions

| Tier | Criteria | Recommended Action | Timeline |
|---|---|---|---|
| **Active** | Traffic in the last 30 days | No action | — |
| **Low-Traffic** | < 10 requests/day averaged over 90 days | Tag for review; notify owner | Review within 60 days |
| **Dormant** | Zero invocations for 30–90 days, but has active deployment/stage | Notify owner; begin deprecation | 30-day notice, then cleanup |
| **Orphaned** | No stage, no deployment, no usage plan, or no traffic ever | Immediate cleanup candidate | 7-day notice, then delete |

### Classification Decision Tree

```
Does the API have any invocations in the last 90 days?
├── YES
│   └── Avg > 10 req/day? → ACTIVE
│       Avg < 10 req/day? → LOW-TRAFFIC
└── NO
    ├── Has an active stage/deployment? → DORMANT
    └── No stage / no deployment?      → ORPHANED
```

---

## 6. Stakeholder Communication & Approval

### 6.1 Owner Identification

The automation resolves the API owner in the following order:

1. Check the `owner` and `team` resource tags on the API.
2. Fall back to the IAM principal that created the API (CloudTrail lookup).
3. Fall back to the AWS account owner if no other signal is available.

### 6.2 Notification Template

**Subject:** `[ACTION REQUIRED] Unused AWS API Gateway API scheduled for deletion — <API_NAME> (<API_ID>)`

```
Hi <OWNER_NAME>,

Our automated API hygiene scan has identified the following API Gateway endpoint
as unused based on 90 days of CloudWatch metrics:

  API Name:    <API_NAME>
  API ID:      <API_ID>
  Region:      <REGION>
  Account:     <ACCOUNT_ID> (<ACCOUNT_NAME>)
  Last seen:   <LAST_TRAFFIC_DATE or "Never">
  Tier:        <DORMANT | ORPHANED>

Scheduled deletion date: <DATE + 30 days>

Actions you can take before deletion:
  1. Reply to confirm this API can be deleted.
  2. Reply to request a 30-day extension (one extension allowed).
  3. Tag the API with `lifecycle: protected` to permanently exclude it.

If no response is received by <DATE + 14 days>, deletion will proceed automatically.

Questions? Contact: mdziaur.rahman@corebridgefinancial.com
```

### 6.3 Approval Workflow

```
Scan identifies Dormant/Orphaned API
          │
          ▼
   Notify owner via email + Jira ticket
          │
     ┌────┴────┐
     │         │
  Response   No response
  received    in 14 days
     │              │
     ▼              ▼
Owner approves?  Escalate to team lead
   YES → proceed    │
   NO  → keep API   ▼
                Auto-approve after
                30-day total window
```

### 6.4 Jira Ticket Schema

| Field | Value |
|---|---|
| **Issue Type** | Task |
| **Summary** | `[API Cleanup] Delete unused API: <API_NAME> (<API_ID>)` |
| **Labels** | `api-cleanup`, `aws`, `api-gateway` |
| **Components** | Platform Engineering |
| **Priority** | Low |
| **Due Date** | Scheduled deletion date |
| **Custom fields** | `api_id`, `region`, `account_id`, `tier`, `owner_email` |

---

## 7. Safe Cleanup Process

### 7.1 Pre-Deletion Checklist

Before the automation proceeds with any deletion, the following conditions are automatically verified:

- [ ] API is classified as Dormant or Orphaned (§5)
- [ ] Owner has been notified and notice period has elapsed (§6)
- [ ] OpenAPI spec has been exported and archived to S3 (§8.1)
- [ ] Jira ticket approval is recorded
- [ ] No active CloudWatch alarms reference this API
- [ ] No Lambda event-source mappings reference this API
- [ ] Custom domain (if any) has been detached

### 7.2 Phase 1 — Soft Deprecation (Day 0)

Performed automatically by **Lambda: Cleaner** (`CLEANER_MODE=soft`):

- Exports and archives the OpenAPI spec to S3
- Throttles all stages to 0 requests/second
- Sets `soft_deleted_at` in the DynamoDB inventory

The pipeline then waits **7 days** (Step Functions Wait state). If any `Count` metric > 0 appears during this window, the pipeline halts and alerts via SNS.

### 7.3 Phase 2 — Hard Delete (Day 7+)

Performed automatically by **Lambda: Cleaner** (`CLEANER_MODE=hard`):

- Calls `delete-rest-api` (v1) or `delete-api` (v2)
- Sets `deleted_at` in DynamoDB
- Full audit trail is preserved in DynamoDB and the S3 archive

### 7.4 Dry-Run Mode

The automation defaults to `DRY_RUN=true`. In this mode all pipeline steps execute normally but no real AWS changes are made — throttle calls and delete calls are logged only. Set `DRY_RUN=false` in `terraform.tfvars` to enable live deletions (see §9.6).

---

## 8. Rollback & Recovery Plan

### 8.1 Pre-Deletion Archival to S3

Before any deletion the automation automatically exports the full OpenAPI 3.0 spec and uploads it to the configured S3 archive bucket:

```
s3://api-archive-bucket/api-gateway/<ACCOUNT_ID>/<REGION>/<API_ID>/<DATE>-oas30.json
```

The S3 bucket is configured with versioning enabled, AES-256 encryption, and a lifecycle policy that transitions objects to Glacier after 90 days with a 7-year retention period.

### 8.2 Re-Import Procedure

If an API needs to be restored after deletion, retrieve the archived spec from S3 and re-import:

```bash
# Download spec from S3
aws s3 cp \
  s3://api-archive-bucket/api-gateway/<ACCOUNT_ID>/<REGION>/<API_ID>/<DATE>-oas30.json \
  /tmp/<API_ID>-oas30.json

# Re-import REST API from OpenAPI spec
aws apigateway import-rest-api \
  --fail-on-warnings \
  --body fileb:///tmp/<API_ID>-oas30.json

# Re-deploy to a stage
aws apigateway create-deployment \
  --rest-api-id <NEW_API_ID> \
  --stage-name <STAGE>
```

### 8.3 RTO / RPO Targets

| Scenario | RTO | RPO |
|---|---|---|
| Accidental deletion of non-critical API | 2 hours | 0 (spec archived pre-deletion) |
| Accidental deletion of critical API | 30 minutes | 0 (spec archived pre-deletion) |
| Full region failure | N/A (redeploy from IaC) | 0 |

### 8.4 Terraform / CloudFormation State

If the API was originally provisioned via IaC:

- **Terraform**: Run `terraform state rm aws_api_gateway_rest_api.<RESOURCE>` before deletion to avoid state drift. Archive the `.tf` files to S3.
- **CloudFormation**: Note the Stack name and template S3 location before deleting the resource outside of CloudFormation.

---

## 9. Automation Approach

> **All automation code lives in:** [`automation/api-gateway-cleanup/`](./automation/api-gateway-cleanup/)  
> See the [automation README](./automation/api-gateway-cleanup/README.md) for full setup and usage instructions.

---

### 9.1 Repository Structure

```
automation/api-gateway-cleanup/
├── configs/
│   └── config.yaml            # Central config — thresholds, flags, bucket names
├── lambdas/
│   ├── lambda_scanner.py      # Lambda #1 — scans all APIs + CloudWatch metrics
│   ├── lambda_classifier.py   # Lambda #2 — classifies APIs into tiers
│   ├── lambda_notifier.py     # Lambda #3 — sends SES email + SNS alerts
│   └── lambda_cleaner.py      # Lambda #4 — soft/hard deletes APIs + archives specs
├── scripts/
│   ├── scan.py                # CLI — local scan → CSV + JSON report
│   ├── cleanup.py             # CLI — applies soft/hard delete from report
│   └── archive.py             # CLI — exports OpenAPI specs to S3
├── terraform/
│   ├── main.tf                # Full IaC: DynamoDB, S3, IAM, Lambda, Step Functions, EventBridge
│   └── terraform.tfvars       # Environment-specific values
└── requirements.txt           # boto3, pyyaml
```

---

### 9.2 Pipeline Architecture

```
EventBridge Scheduler (every Monday 08:00 UTC)
                  │
                  ▼
       Step Functions State Machine
                  │
    ┌─────────────▼─────────────┐
    │     Lambda: Scanner        │  Scans all REST/HTTP/WebSocket APIs
    │     lambda_scanner.py      │  across all enabled regions via boto3.
    │                            │  Pulls 90-day CloudWatch Count metric.
    │                            │  Upserts records → DynamoDB inventory.
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │    Lambda: Classifier      │  Reads DynamoDB inventory.
    │    lambda_classifier.py    │  Applies tier rules (§5).
    │                            │  Writes tier + classified_at back.
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │     Lambda: Notifier       │  Queries DORMANT/ORPHANED records.
    │     lambda_notifier.py     │  Sends SES email to tag:owner.
    │                            │  Publishes to SNS on escalation.
    │                            │  Sets notified_at + deletion_scheduled_date.
    └─────────────┬─────────────┘
                  │
             Wait 7 days
             (SFN Wait state)
                  │
    ┌─────────────▼─────────────┐
    │  Lambda: Cleaner (soft)    │  Exports + archives OpenAPI spec → S3.
    │  lambda_cleaner.py         │  Throttles all stages to 0 req/s.
    │  CLEANER_MODE=soft         │  Sets soft_deleted_at in DynamoDB.
    └─────────────┬─────────────┘
                  │
             Wait 7 days
             (SFN Wait state)
                  │
    ┌─────────────▼─────────────┐
    │  Lambda: Cleaner (hard)    │  Calls delete-rest-api / delete-api.
    │  lambda_cleaner.py         │  Sets deleted_at in DynamoDB.
    │  CLEANER_MODE=hard         │  Full audit trail preserved.
    └───────────────────────────┘
```

---

### 9.3 DynamoDB Inventory Table Schema

**Table name:** `api-gateway-inventory`  
**Partition key:** `api_id` (String) | **Sort key:** `region` (String)  
**GSI:** `tier-index` on `tier` (for efficient tier-based queries)

| Attribute | Type | Description |
|---|---|---|
| `api_id` | String | API Gateway API ID |
| `region` | String | AWS region |
| `account_id` | String | AWS account ID |
| `api_name` | String | Human-readable name |
| `protocol` | String | `REST`, `HTTP`, `WEBSOCKET` |
| `created_date` | String | ISO8601 creation date |
| `last_invocation` | String | ISO8601 of last recorded traffic, or `never` |
| `invocation_count_90d` | Number | Sum of `Count` metric over 90 days |
| `has_stages` | Boolean | Whether API has an active stage |
| `tier` | String | `ACTIVE`, `LOW_TRAFFIC`, `DORMANT`, `ORPHANED` |
| `classified_at` | String | ISO8601 of last classification run |
| `owner_email` | String | Resolved from `owner` tag or fallback |
| `notified_at` | String | ISO8601 of last notification sent |
| `approved_for_deletion` | Boolean | Owner approval flag |
| `deletion_scheduled_date` | String | ISO8601 scheduled deletion date |
| `soft_deleted_at` | String | ISO8601 of throttle-to-zero |
| `archived_s3_key` | String | S3 key of the exported OpenAPI spec |
| `deleted_at` | String | ISO8601 of actual hard deletion |
| `scanned_at` | String | ISO8601 of last scan |
| `tags` | Map | Raw AWS resource tags |

---

### 9.4 Configuration Reference

All settings are controlled via environment variables on the Lambda functions (sourced from `configs/config.yaml` and `terraform/terraform.tfvars`):

| Variable | Default | Description |
|---|---|---|
| `DYNAMODB_TABLE` | `api-gateway-inventory` | DynamoDB table name |
| `S3_ARCHIVE_BUCKET` | `api-archive-bucket` | S3 bucket for OpenAPI spec archives |
| `SNS_TOPIC_ARN` | — | SNS topic for orphan alerts |
| `SES_SENDER_EMAIL` | — | Verified SES sender |
| `LOOKBACK_DAYS` | `90` | CloudWatch metric look-back window |
| `LOW_TRAFFIC_THRESHOLD` | `10` | req/day threshold for LOW_TRAFFIC tier |
| `DORMANT_DAYS` | `30` | Zero-traffic days before DORMANT |
| `DRY_RUN` | `true` | **Must be `false` to allow real deletions** |
| `SOFT_DELETE_WINDOW_DAYS` | `7` | Days between soft and hard delete |
| `NOTICE_PERIOD_DAYS` | `30` | Days owners have to respond |
| `ESCALATION_DAYS` | `14` | Days before auto-escalation |
| `CLEANER_MODE` | `soft` | `soft` or `hard` — set per Lambda invocation |

---

### 9.5 Quick Start — Automated (Terraform)

```bash
# 1. Configure
cd automation/api-gateway-cleanup/terraform
cp terraform.tfvars terraform.tfvars.local  # edit your values

# 2. Deploy all infrastructure
terraform init
terraform plan
terraform apply

# 3. Trigger first run manually
aws stepfunctions start-execution \
  --state-machine-arn $(terraform output -raw state_machine_arn) \
  --input '{"triggered_by": "manual"}'

# 4. Watch the execution
aws stepfunctions describe-execution \
  --execution-arn <ARN from above>

# 5. Flip to live mode when satisfied
# Edit terraform.tfvars: dry_run = "false"
terraform apply
```

---

##

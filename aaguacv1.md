# Amazon API Gateway — Unused API Cleanup: Design & Process Document
Version: 1.0   Date: April 27, 2026   Status: Active   Audience: API Platform Team, Cloud Engineering, Cloud Ops, DevOps Teams

## Table of Contents
1. [Overview & Goals](#1-overview--goals)
1. [Definition of "Unused"](#2-definition-of-unused)
1. [Discovery Process](#3-discovery-process)
1. [Analysis Methodology](#4-analysis-methodology)
1. [Classification Framework](#5-classification-framework)
1. [Stakeholder Communication & Approval](#6-stakeholder-communication--approval)
1. [Safe Cleanup Process](#7-safe-cleanup-process)
1. [Rollback & Recovery Plan](#8-rollback--recovery-plan)
1. [Automation Approach](#9-automation-approach)
1. [Governance & Ongoing Monitoring](#10-governance--ongoing-monitoring)

## 1. Overview & Goals
### 1.1 Business Drivers
Unused APIs in Amazon API Gateway accumulate silently over time and create several organisational risks:

| Driver | Description |
|---|---|
| **Cost** | Each API Gateway stage with custom domains, WAF associations, or data transfer incurs charges even with zero invocations. |
| **Security** | Orphaned APIs with lingering IAM policies, API keys, or Lambda integrations expand the attack surface unnecessarily. |
| **Operational overhead** | Teams spend time investigating alerts, logs, and quota limits for APIs that serve no traffic. |
| **Compliance** | Unmanaged endpoints may violate internal security policies or regulatory requirements (e.g. PCI-DSS, SOC2). |


### 1.2 Scope
This process applies to all three API Gateway types across all AWS regions and all accounts in the organisation:
- REST APIs (v1)
### 1.3 Success Metrics
- Number of APIs identified as unused
- Number of APIs safely decommissioned
## 2. Definition of "Unused"
An API is considered unused if it meets one or more of the following criteria over a configurable look-back window (default: 90 days).
### 2.1 Primary Signals
| Signal | CloudWatch Metric | Threshold |
| --- | --- | --- |
| Zero invocations | `Count` | `SUM == 0` over look-back window |
| No successful responses | `5XXError`, `4XXError` | All requests erroring (no healthy traffic) |
| No integration latency recorded | `IntegrationLatency` | `SampleCount == 0` |


### 2.2 Secondary Signals
- No deployment: API has no active stage or deployment
- No usage plan / API key: REST API has no usage plan attached (suggests it was never integrated into a client)
- No custom domain: API has never been given a friendly DNS name
- No CloudWatch log group: Access logging was never configured
### 2.3 Look-back Window
The default look-back window is 90 days. Teams can override this per API via the last-reviewed tag. APIs tagged lifecycle: protected are excluded from automated cleanup.

## 3. Discovery Process
### 3.1 Prerequisites
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

Discovery across all accounts and regions is performed automatically by **Lambda: Scanner** (see §9.2). No manual CLI steps are required.

## 4. Analysis Methodology
The automation pipeline analyses each discovered API against the following signals. All data collection is performed automatically by Lambda: Scanner (see §9.2).
### 4.1 Invocation Count (CloudWatch)
The scanner pulls the Count metric from the AWS/ApiGateway namespace over the configured look-back window (default: 90 days). A Sum of 0 or an empty Datapoints array means no traffic in the period.
### 4.2 Access Log Analysis – Zero Invocations
If access logging is enabled on a stage, the scanner queries CloudWatch Logs Insights to confirm zero real requests by counting log entries in the look-back window.
### 4.3 Stages and Deployments – No Deployments
The scanner checks whether each REST API has at least one active stage and deployment. An API with no stages or no deployments is an immediate candidate for deletion.
### 4.4 Usage Plans – No Usage Plan
The scanner checks whether each REST API is referenced by a usage plan. APIs not attached to any usage plan have never been formally integrated into a client workflow.

## 5. Classification Framework
Each API discovered in §3 is assigned one of four tiers after analysis in §4.
### Tier Definitions
| Tier | Criteria | Recommended Action | Timeline |
| --- | --- | --- | --- |
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
1. Check the owner and team resource tags on the API.
1. Fall back to the AWS account owner if no other signal is available.
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

### 6.4 Jira Ticket Schema (For API Platform BAU Followup)
| Field | Value |
| --- | --- |
| **Issue Type** | Task |
| **Summary** | `[API Cleanup] Delete unused API: <API_NAME> (<API_ID>)` |
| **Labels** | `api-cleanup`, `aws`, `api-gateway` |
| **Components** | Platform Engineering |
| **Priority** | Low |
| **Due Date** | Scheduled deletion date |
| **Custom fields** | `api_id`, `region`, `account_id`, `tier`, `owner_email` |



## 7. Safe Cleanup Process
### 7.1 Pre-Deletion Checklist
Before the automation proceeds with any deletion, the following conditions are automatically verified:
- [ ] API is classified as Dormant or Orphaned (§5)
- [ ] Owner has been notified and notice period has elapsed (§6)
- [ ] OpenAPI spec has been exported and archived to S3 (§8.1)
- [ ] Service Now(SNOW) approval is recorded
- [ ] No active CloudWatch alarms reference this API

### 7.2 Phase 1 — Soft Deprecation (Day 0)
Performed automatically by Lambda: Cleaner (CLEANER_MODE=soft):
- Exports and archives the OpenAPI spec to S3
- Detach Custom Domain (If any)
- Throttles all stages to 0 requests/second (detach usage plans from the API)
- Sets soft_deleted_at in the DynamoDB inventory
The pipeline then waits 7 days (Step Functions Wait state). If any Count metric > 0 appears during this window, the pipeline halts and alerts via SNS.
### 7.3 Phase 2 — Hard Delete (Day 7+)
Performed automatically by Lambda: Cleaner (CLEANER_MODE=hard):
- Calls delete-rest-api (v1) or delete-api (v2)
- Sets deleted_at in DynamoDB
- Full audit trail is preserved in DynamoDB and the S3 archive

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
### 9.1 Pipeline Architecture

```
EventBridge Scheduler (every Monday 08:00 UTC)
                  │
                  ▼
    ┌─────────────▼─────────────┐
    │     Lambda: Scanner        │  Scans all REST APIs
    │     lambda_scanner.py      │  across all enabled regions via boto3.
    │                            │  Pulls 90-day CloudWatch Count metric.
    │                            │  Generates CSV: api-scan-<DATE>.csv
    │                            │  Uploads to S3: s3://bucket/scans/
    └─────────────┬─────────────┘
                  │
    ┌─────────────▼─────────────┐
    │    Lambda: Classifier      │  Downloads latest scan CSV from S3.
    │    lambda_classifier.py    │  Applies tier rules (§5).
    │                            │  Generates CSV: api-classification-<DATE>.csv
    │                            │  Uploads to S3: s3://bucket/classifications/
    │                            │  Sends email notification with S3 link to team.
    │                            │  Sends individual notifications to API owners
    │                            │  for DORMANT/ORPHANED APIs (§6.2).
    └─────────────┬─────────────┘
                  │
                  ▼
          ┌───────────────────┐
          │  Manual Review     │  Team downloads CSV from S3.
          │  by API Team       │  Reviews each API classification.
          │                    │  Updates "ApprovalStatus" column:
          │                    │    - APPROVE_DELETE
          │                    │    - KEEP
          │                    │    - EXTEND_REVIEW (30 days)
          │                    │  Adds "ReviewerComments" column.
          │                    │  Uploads reviewed CSV to:
          │                    │  s3://bucket/approvals/api-approved-<DATE>.csv
          └─────────┬─────────┘
                    │
                    ▼
          ┌───────────────────┐
          │  Jenkins Pipeline  │  Triggered manually or via S3 event.
          │  Jenkinsfile       │  Downloads approved CSV from S3.
          │                    │  Validates CSV format and approvals.
          │                    │  Filters rows with APPROVE_DELETE status.
          │                    │  
          │  Stage 1: Backup   │  Exports OpenAPI specs → S3 archive.
          │                    │  Takes DynamoDB backup.
          │                    │  
          │  Stage 2: Soft Del │  Throttles stages to 0 req/s.
          │                    │  Detaches custom domains.
          │                    │  Updates CSV with soft_delete_date.
          │                    │  
          │  Stage 3: Wait     │  Manual approval required (7 days).
          │                    │  
          │  Stage 4: Hard Del │  Calls delete-rest-api / delete-api.
          │                    │  Updates CSV with deletion_date.
          │                    │  Archives final CSV → S3.
          │                    │  Sends completion report email.
          └───────────────────┘
```

---

### 9.2 CSV Schema

#### 9.2.1 Scanner Output CSV (`api-scan-<DATE>.csv`)

| Column | Description | Example |
|---|---|---|
| `ScanDate` | ISO8601 timestamp of scan | `2026-04-28T08:00:00Z` |
| `AccountId` | AWS account ID | `123456789012` |
| `AccountName` | AWS account alias | `prod-account` |
| `Region` | AWS region | `us-east-1` |
| `ApiId` | API Gateway API ID | `abc123xyz` |
| `ApiName` | Human-readable API name | `customer-api` |
| `ApiType` | `REST` |
| `CreatedDate` | ISO8601 creation date | `2024-01-15T10:30:00Z` |
| `HasStages` | Boolean: has active stages | `true` |
| `StageNames` | Comma-separated stage names | `prod,dev` |
| `HasCustomDomain` | Boolean: has custom domain | `false` |
| `HasUsagePlan` | Boolean: attached to usage plan | `true` |
| `InvocationCount90d` | Total invocations (90 days) | `1523` |
| `LastInvocationDate` | ISO8601 of last traffic | `2026-02-10T14:20:00Z` |
| `AvgRequestsPerDay` | Average daily requests | `16.9` |
| `OwnerEmail` | From `owner` tag or fallback | `john.doe@company.com` |
| `TeamTag` | From `team` tag | `platform-team` |
| `Tags` | JSON string of all tags | `{"owner":"john.doe@company.com","team":"platform-team"}` |

#### 9.2.2 Classifier Output CSV (`api-classification-<DATE>.csv`)

Includes all columns from Scanner CSV plus:

| Column | Description | Example |
|---|---|---|
| `Tier` | Classification tier | `DORMANT` |
| `TierReason` | Reason for tier assignment | `Zero traffic for 45 days` |
| `ClassifiedDate` | ISO8601 of classification | `2026-04-28T08:15:00Z` |
| `RecommendedAction` | Suggested action | `DELETE` or `REVIEW` |
| `ApprovalStatus` | **[TO BE FILLED BY TEAM]** | `APPROVE_DELETE`, `KEEP`, `EXTEND_REVIEW` |
| `ReviewerName` | **[TO BE FILLED BY TEAM]** | `John Doe` |
| `ReviewerComments` | **[TO BE FILLED BY TEAM]** | `Confirmed with team, safe to delete` |
| `ReviewDate` | **[TO BE FILLED BY TEAM]** | `2026-04-29T10:00:00Z` |

#### 9.2.3 Approved CSV (`api-approved-<DATE>.csv`)

Same as Classifier Output CSV with `ApprovalStatus`, `ReviewerName`, `ReviewerComments`, and `ReviewDate` filled in by the API team.

#### 9.2.4 Final Deletion Report CSV (`api-deletion-report-<DATE>.csv`)

Includes all columns from Approved CSV plus:

| Column | Description | Example |
|---|---|---|
| `BackupS3Key` | S3 key of OpenAPI backup | `s3://bucket/archive/123456789012/us-east-1/abc123xyz/2026-04-30-oas30.json` |
| `SoftDeleteDate` | ISO8601 of throttle action | `2026-04-30T09:00:00Z` |
| `HardDeleteDate` | ISO8601 of actual deletion | `2026-05-07T09:00:00Z` |
| `DeletionStatus` | `SUCCESS` or `FAILED` | `SUCCESS` |
| `DeletionError` | Error message if failed | `` |
| `JenkinsJobUrl` | URL to Jenkins job | `https://jenkins.company.com/job/api-cleanup/123` |


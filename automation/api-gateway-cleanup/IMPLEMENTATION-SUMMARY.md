# API Gateway Cleanup - CSV Workflow Implementation Summary

## ✅ What Has Been Completed

### 1. Documentation Updates
- **aaguacv1.md**: Updated automation approach section with CSV-based manual approval workflow
- **README-CSV-WORKFLOW.md**: Comprehensive guide for the new CSV workflow including:
  - Architecture diagram
  - CSV schemas (Scanner, Classifier, Approved, Deletion Report)
  - Classification tiers
  - Setup instructions
  - Usage workflow
  - Troubleshooting guide

### 2. Lambda Functions

#### **lambda_scanner.py** (Updated)
Scans all API Gateway APIs and generates CSV output:
- ✅ Scans REST, HTTP, and WebSocket APIs across all enabled regions
- ✅ Pulls 90-day CloudWatch invocation metrics using proper dimension queries
- ✅ Collects comprehensive API metadata:
  - Account ID/Name, Region, API ID/Name/Type
  - Creation date, stages, custom domains, usage plans
  - Invocation counts, last invocation date, average requests/day
  - Owner email and team tags from API tags
- ✅ Generates CSV file: `api-scan-YYYYMMDD-HHMMSS.csv`
- ✅ Uploads to S3: `s3://bucket/scans/`
- ✅ Returns S3 location in Lambda response

**Key Features:**
- Multi-region support
- Protected API filtering (skips APIs with `lifecycle=protected` or `do-not-delete=true` tags)
- Proper CloudWatch metrics querying (handles all dimension combinations)
- CSV generation with 18 columns of detailed API information

#### **lambda_classifier.py** (Updated)
Classifies APIs and prepares CSV for manual review:
- ✅ Downloads latest scan CSV from S3
- ✅ Applies 4-tier classification logic:
  - **ACTIVE**: Traffic in last 30 days
  - **LOW_TRAFFIC**: < 10 req/day average
  - **DORMANT**: Zero traffic for 30+ days
  - **ORPHANED**: No stages or never had traffic
- ✅ Adds classification columns with reasons
- ✅ Adds empty approval columns for team to fill:
  - ApprovalStatus (APPROVE_DELETE / KEEP / EXTEND_REVIEW)
  - ReviewerName
  - ReviewerComments
  - ReviewDate
- ✅ Generates CSV: `api-classification-YYYYMMDD-HHMMSS.csv`
- ✅ Uploads to S3: `s3://bucket/classifications/`
- ✅ Sends email notification to team via SES with:
  - Summary statistics (counts by tier)
  - Pre-signed download URL (7-day expiration)
  - Instructions for manual review
  - Next steps
- ✅ **Sends individual notifications to API owners** for DORMANT/ORPHANED APIs:
  - Groups APIs by owner email
  - Lists all unused APIs owned by each person
  - 30-day deletion warning
  - 14-day response deadline
  - Actions they can take (confirm/extend/protect)

**Key Features:**
- Configurable thresholds (LOW_TRAFFIC_THRESHOLD, DORMANT_DAYS)
- Detailed classification reasons
- HTML and text email formats
- Pre-signed S3 URLs for secure CSV downloads
- Automated owner notifications for proactive engagement

### 3. Jenkins Pipeline

#### **Jenkinsfile-decommission** (New)
Complete Jenkins pipeline for API decommissioning:

**Pipeline Stages:**
1. ✅ **Setup**: Install dependencies, validate parameters
2. ✅ **Download & Validate CSV**: 
   - Download approved CSV from S3
   - Validate required columns
   - Count approved deletions
   - Fail if no deletions approved
3. ✅ **Backup APIs**:
   - Export OpenAPI 3.0 specs for all APIs
   - Upload to S3: `s3://bucket/backups/{account}/{region}/{api-id}/`
   - Continue on individual failures
4. ✅ **Soft Delete**:
   - Throttle all stages to 0 req/s (burst and rate limits)
   - Update CSV with SoftDeleteDate
   - APIs become inaccessible but not deleted
5. ✅ **Manual Approval**:
   - Pause pipeline for manual approval
   - 7-day timeout
   - Restricted to `api-admins`, `platform-team` groups
6. ✅ **Hard Delete**:
   - Permanently delete APIs (REST: delete-rest-api, v2: delete-api)
   - Update CSV with HardDeleteDate and DeletionStatus
   - Capture errors for failed deletions
7. ✅ **Generate Report**:
   - Create deletion report CSV
   - Upload to S3: `s3://bucket/reports/`
   - Include Jenkins job URL
8. ✅ **Send Notification**:
   - Email completion report via SES
   - Include success/failure counts
   - Pre-signed report download URL
   - Jenkins job link

**Features:**
- DRY_RUN mode for testing without deletions
- Configurable parameters (S3 bucket, email recipients, CSV location)
- Error handling (continues on individual API failures)
- Comprehensive logging
- Artifact archiving

### 4. PowerShell Script Updates

#### **jps.ps1** (Updated)
Fixed CloudWatch metrics querying:
- ✅ Lists all available Count metrics for each API
- ✅ Queries each unique dimension combination (ApiId, Stage, Method, Resource)
- ✅ Sums up counts from all dimension combinations
- ✅ Properly handles APIs with detailed CloudWatch metrics enabled
- ✅ Returns actual request counts instead of 0

**Key Fix:**
CloudWatch requires exact dimension matches. The script now:
1. Lists all metrics with their dimension combinations
2. Queries each combination separately
3. Aggregates the results for total count

## 📋 CSV Schemas

### Scanner Output
18 columns including: ScanDate, AccountId, AccountName, Region, ApiId, ApiName, ApiType, CreatedDate, HasStages, StageNames, HasCustomDomain, HasUsagePlan, InvocationCount90d, LastInvocationDate, AvgRequestsPerDay, OwnerEmail, TeamTag, Tags

### Classifier Output
Scanner columns + 8 classification columns: Tier, TierReason, ClassifiedDate, RecommendedAction, ApprovalStatus, ReviewerName, ReviewerComments, ReviewDate

### Deletion Report
Classifier columns + 6 deletion columns: BackupS3Key, SoftDeleteDate, HardDeleteDate, DeletionStatus, DeletionError, JenkinsJobUrl

## 🔄 Complete Workflow

```
1. Monday 08:00 UTC → Scanner Lambda runs
   ↓
2. Scanner uploads CSV to s3://bucket/scans/
   ↓
3. Classifier Lambda triggers (can be manual or EventBridge)
   ↓
4. Classifier uploads CSV to s3://bucket/classifications/
   ↓
5. Email sent to team with download link
   ↓
6. Individual emails sent to API owners for DORMANT/ORPHANED APIs
   ↓
7. Team downloads CSV from email link
   ↓
8. Team reviews in Excel/Google Sheets
   ↓
9. Team fills approval columns (ApprovalStatus, ReviewerName, etc.)
   ↓
10. Team uploads to s3://bucket/approvals/
   ↓
11. Jenkins pipeline triggered (manual or S3 event)
   ↓
12. Pipeline validates CSV and counts deletions
   ↓
13. Pipeline backs up OpenAPI specs to S3
   ↓
14. Pipeline soft deletes (throttles to 0 req/s)
   ↓
15. Pipeline waits for manual approval (7 days timeout)
   ↓
16. Pipeline hard deletes approved APIs
   ↓
17. Pipeline generates report and sends email
   ↓
18. Team verifies results from deletion report
```

## 🔧 Configuration Required

### Lambda Environment Variables

**Scanner:**
```
S3_BUCKET=api-gateway-cleanup-bucket
S3_SCAN_PREFIX=scans/
LOOKBACK_DAYS=90
```

**Classifier:**
```
S3_BUCKET=api-gateway-cleanup-bucket
S3_SCAN_PREFIX=scans/
S3_CLASSIFICATION_PREFIX=classifications/
LOOKBACK_DAYS=90
LOW_TRAFFIC_THRESHOLD=10
DORMANT_DAYS=30
SES_SENDER_EMAIL=noreply@company.com
NOTIFICATION_EMAILS=mdziaur.rahman@corebridgefinancial.com,mdziaur.rahman@mphasis.com,sust.cse.zia@gmail.com
```

### Jenkins Configuration

**Job Parameters:**
- APPROVED_CSV_S3_KEY (required)
- S3_BUCKET (default: api-gateway-cleanup-bucket)
- NOTIFICATION_EMAILS (default: api-team@company.com)
- DRY_RUN (default: false)

## 📁 S3 Bucket Structure

```
api-gateway-cleanup-bucket/
├── scans/                    # Scanner outputs
├── classifications/          # Classifier outputs (for team review)
├── approvals/               # Team-approved CSVs
├── backups/                 # OpenAPI spec backups
│   └── {account}/
│       └── {region}/
│           └── {api-id}/
└── reports/                 # Deletion reports
```

## 🔐 IAM Permissions

### Lambda Role Needs:
- apigateway:GET
- apigatewayv2:GET
- cloudwatch:GetMetricStatistics
- cloudwatch:ListMetrics
- account:ListRegions
- s3:GetObject, s3:PutObject, s3:ListBucket
- ses:SendEmail
- sts:GetCallerIdentity
- iam:ListAccountAliases

### Jenkins Role Needs:
- All Lambda permissions +
- apigateway:DELETE, apigateway:UpdateStage
- apigatewayv2:DELETE, apigatewayv2:UpdateStage, apigatewayv2:ExportApi

## ✨ Key Benefits of This Approach

1. **Manual Control**: Team reviews and approves every deletion
2. **Audit Trail**: Complete CSV-based audit trail of all decisions
3. **Safety**: Two-stage delete (soft → manual approval → hard)
4. **Transparency**: Email notifications at every step
5. **Backup**: OpenAPI specs backed up before any deletion
6. **Flexibility**: Can approve/reject/extend review per API
7. **Rollback**: Have 7 days to verify soft delete before permanent deletion

## 🚀 Next Steps

1. Deploy Lambda functions using Terraform
2. Create S3 bucket with proper lifecycle policies
3. Configure SES and verify email addresses
4. Set up Jenkins job with Jenkinsfile-decommission
5. Test with DRY_RUN=true first
6. Run scanner and classifier manually to test
7. Review generated CSV format with team
8. Establish approval process and assign reviewers
9. Run first production cleanup cycle

## 📞 Contact

For questions about this implementation:
- Email: mdziaur.rahman@corebridgefinancial.com
- Email: mdziaur.rahman@mphasis.com
- Email: sust.cse.zia@gmail.com

---

**Implementation Date**: April 30, 2026
**Status**: ✅ Complete - Ready for deployment

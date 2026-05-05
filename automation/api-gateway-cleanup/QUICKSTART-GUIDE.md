# Quick Start Guide - API Cleanup CSV Workflow

## For API Team Reviewers

### Step 1: Receive Email Notification

Every Monday, you'll receive an email with subject:
```
[API Cleanup] New Classification Results - YYYY-MM-DD
```

The email contains:
- Summary of API counts by tier
- Link to download classification CSV
- Instructions for next steps

**Additionally**, if you own any APIs classified as **DORMANT** or **ORPHANED**, you'll receive a separate email:
```
[ACTION REQUIRED] N Unused AWS API Gateway API(s) scheduled for deletion
```

This owner notification includes:
- List of your specific APIs that are unused
- 30-day scheduled deletion date
- Actions you can take (confirm, request extension, or tag as protected)
- 14-day response deadline

### Step 2: Download Classification CSV

Click the download link in the email, or use AWS CLI:

```bash
aws s3 cp s3://api-gateway-cleanup-bucket/classifications/api-classification-20260428-081500.csv .
```

### Step 3: Open in Excel/Google Sheets

The CSV has these columns (last 4 are empty for you to fill):

| Column | You Fill? | Description |
|--------|-----------|-------------|
| ScanDate | No | When the scan ran |
| AccountId | No | AWS account |
| Region | No | AWS region |
| ApiId | No | API Gateway ID |
| ApiName | No | Human-readable name |
| ApiType | No | REST/HTTP/WEBSOCKET |
| InvocationCount90d | No | Total requests (90 days) |
| LastInvocationDate | No | When last used |
| AvgRequestsPerDay | No | Average daily traffic |
| **Tier** | No | **ACTIVE/LOW_TRAFFIC/DORMANT/ORPHANED** |
| TierReason | No | Why classified this way |
| RecommendedAction | No | DELETE or REVIEW |
| **ApprovalStatus** | **YES** | **Your decision** |
| **ReviewerName** | **YES** | **Your name** |
| **ReviewerComments** | **YES** | **Why you decided this** |
| **ReviewDate** | **YES** | **Today's date** |

### Step 4: Review Each API

Focus on APIs where `Tier` = `DORMANT` or `ORPHANED` (recommended for deletion).

For each API:

1. **Check if you recognize it**
   - Is it still needed?
   - Who owns it?
   - Is it documented?

2. **Verify the metrics**
   - Look at `InvocationCount90d` and `LastInvocationDate`
   - Check `AvgRequestsPerDay`
   - Does this match your expectations?

3. **Make a decision**
   - **APPROVE_DELETE** - Safe to delete
   - **KEEP** - Still needed, keep it
   - **EXTEND_REVIEW** - Need more time (30 days)

### Step 5: Fill in Your Decision

For each API, fill in these 4 columns:

#### ApprovalStatus
Choose one:
- `APPROVE_DELETE` - API will be deleted
- `KEEP` - API will be kept
- `EXTEND_REVIEW` - Re-check in 30 days

#### ReviewerName
Your name, e.g., `John Doe`

#### ReviewerComments
Brief reason for your decision, e.g.:
- `"Deprecated service, confirmed with team, safe to delete"`
- `"Still used by legacy client, keep for now"`
- `"Need to check with Product team first"`

#### ReviewDate
Today's date, e.g., `2026-04-29`

### Example Rows After Review

```csv
ApiName,Tier,RecommendedAction,ApprovalStatus,ReviewerName,ReviewerComments,ReviewDate
old-payment-api,ORPHANED,DELETE,APPROVE_DELETE,John Doe,Migrated to new API in March,2026-04-29
internal-reports,DORMANT,DELETE,KEEP,Jane Smith,Used for monthly reports only,2026-04-29
test-api-dev,DORMANT,DELETE,APPROVE_DELETE,John Doe,Dev environment leftover,2026-04-29
customer-api,ACTIVE,REVIEW,KEEP,Jane Smith,Active production API,2026-04-29
```

### Step 6: Upload Approved CSV to S3

Save the file with a new name: `api-approved-YYYYMMDD.csv`

Upload to S3:
```bash
aws s3 cp api-approved-20260429.csv \
  s3://api-gateway-cleanup-bucket/approvals/api-approved-20260429.csv
```

Or use AWS Console:
1. Go to S3 → `api-gateway-cleanup-bucket`
2. Navigate to `approvals/` folder
3. Click "Upload"
4. Select your CSV file
5. Click "Upload"

### Step 7: Trigger Jenkins Pipeline (Optional)

#### Option A: Automatic
If configured, the Jenkins pipeline will trigger automatically when you upload the CSV.

#### Option B: Manual
Go to Jenkins and start the pipeline:
1. Visit: https://jenkins.company.com/job/api-gateway-decommission
2. Click "Build with Parameters"
3. Fill in:
   - **APPROVED_CSV_S3_KEY**: `approvals/api-approved-20260429.csv`
   - **DRY_RUN**: `false`
4. Click "Build"

### Step 8: Pipeline Stages (What Happens Next)

1. **Backup** (2-5 min)
   - OpenAPI specs backed up to S3
   - Safe to proceed even if something goes wrong

2. **Soft Delete** (2-5 min)
   - APIs throttled to 0 req/s
   - No traffic can reach them
   - **APIs NOT deleted yet** - you have 7 days to verify

3. **Manual Approval Required** (7 days)
   - Jenkins pauses and waits for your approval
   - Check that soft delete didn't break anything
   - Monitor for 7 days

4. **Hard Delete** (2-5 min)
   - After approval, APIs permanently deleted
   - Cannot be undone
   - Deletion report sent to your email

### Step 9: Verify Results

You'll receive a completion email with:
- Success/failure counts
- Link to deletion report CSV
- Jenkins job URL

Download the deletion report to verify:
```bash
aws s3 ls s3://api-gateway-cleanup-bucket/reports/
aws s3 cp s3://api-gateway-cleanup-bucket/reports/api-deletion-report-20260430-100000.csv .
```

## Quick Reference

### Approval Status Values

| Value | Meaning | What Happens |
|-------|---------|--------------|
| `APPROVE_DELETE` | Delete this API | API will be backed up → soft deleted → manually approved → permanently deleted |
| `KEEP` | Keep this API | API will NOT be touched, removed from deletion list |
| `EXTEND_REVIEW` | Review again later | API skipped this cycle, will appear in next scan (30 days) |

### Classification Tiers

| Tier | Meaning | Typical Action |
|------|---------|----------------|
| **ACTIVE** | Traffic in last 30 days | Keep |
| **LOW_TRAFFIC** | < 10 req/day average | Review usage |
| **DORMANT** | No traffic for 30+ days | Usually delete |
| **ORPHANED** | No stages or never used | Usually delete |

### Common Scenarios

#### Scenario 1: API is truly unused
```csv
ApprovalStatus: APPROVE_DELETE
ReviewerName: John Doe
ReviewerComments: Confirmed with team, service decommissioned in March
ReviewDate: 2026-04-29
```

#### Scenario 2: API has seasonal/periodic usage
```csv
ApprovalStatus: KEEP
ReviewerName: Jane Smith
ReviewerComments: Used for quarterly reports only, last used Feb 2026
ReviewDate: 2026-04-29
```

#### Scenario 3: Need more investigation
```csv
ApprovalStatus: EXTEND_REVIEW
ReviewerName: John Doe
ReviewerComments: Need to check with Product team before deletion
ReviewDate: 2026-04-29
```

#### Scenario 4: False positive (API is actually active)
```csv
ApprovalStatus: KEEP
ReviewerName: Jane Smith
ReviewerComments: CloudWatch metrics incorrect, API is actively used
ReviewDate: 2026-04-29
```

## Safety Features

✅ **Two-stage deletion**: Soft delete → 7-day wait → Hard delete
✅ **Backups**: OpenAPI specs backed up before any action
✅ **Manual approval**: Human verification required at every step
✅ **Audit trail**: Complete CSV record of all decisions
✅ **Reversible**: Soft delete can be reversed by re-enabling stages
✅ **DRY RUN mode**: Test the pipeline without actual deletions

## Tips for Reviewers

1. **Start with ORPHANED tier** - Usually safe to delete (no stages)
2. **Check tags** - Look at `OwnerEmail` and `TeamTag` columns
3. **Verify with teams** - When in doubt, ask the API owner
4. **Use EXTEND_REVIEW** - If you need more time to investigate
5. **Document your reasoning** - Future you will thank you
6. **Review as a team** - Multiple eyes on the list is better

## FAQ

### Q: What if I'm not sure about an API?
A: Use `EXTEND_REVIEW` to skip it this cycle and investigate further.

### Q: Can I undo a deletion?
A: After soft delete (7 days), yes. After hard delete, no - but OpenAPI specs are backed up.

### Q: What if I accidentally approve the wrong API?
A: Before hard delete, you have 7 days to stop the pipeline or re-enable the API.

### Q: How do I know if an API is really unused?
A: Check multiple sources:
- CloudWatch metrics (90-day history)
- CloudTrail logs
- Ask the team listed in `TeamTag`
- Check internal documentation

### Q: What happens to custom domains?
A: Soft delete detaches custom domains. They won't route to the API anymore.

### Q: Can I test this without deleting anything?
A: Yes! Set `DRY_RUN=true` in Jenkins parameters.

## Contact Support

- **Technical Issues**: DevOps team
- **API Ownership Questions**: Check `OwnerEmail` and `TeamTag` in CSV
- **Process Questions**: api-platform-team@company.com

## Email Contacts

Review results will be sent to:
- mdziaur.rahman@corebridgefinancial.com
- mdziaur.rahman@mphasis.com
- sust.cse.zia@gmail.com

---

**Need help?** Ask in #api-platform Slack channel or email the contacts above.

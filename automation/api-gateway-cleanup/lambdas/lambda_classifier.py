"""
lambda_classifier.py
─────────────────────
Lambda #2 — Reads latest scan CSV from S3, applies the
four-tier classification rules, and generates classification CSV
with empty approval fields for manual team review.

Tiers:
  ACTIVE       — traffic in the last 30 days
  LOW_TRAFFIC  — < LOW_TRAFFIC_THRESHOLD req/day over 90 days
  DORMANT      — zero traffic 30–90 days, has a live stage
  ORPHANED     — no stage, no deployment, or zero traffic ever

Triggered by: EventBridge Scheduler (weekly, Monday 09:00 UTC)
              OR directly after lambda_scanner completes
IAM role needs: s3:GetObject, s3:PutObject, ses:SendEmail
"""

import os
import boto3
import logging
import csv
from io import StringIO
from datetime import datetime, timedelta, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_BUCKET", "api-gateway-cleanup-bucket")
S3_SCAN_PREFIX = os.environ.get("S3_SCAN_PREFIX", "scans/")
S3_CLASSIFICATION_PREFIX = os.environ.get("S3_CLASSIFICATION_PREFIX", "classifications/")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "90"))
LOW_TRAFFIC_THRESHOLD = int(os.environ.get("LOW_TRAFFIC_THRESHOLD", "10"))  # req/day
DORMANT_DAYS = int(os.environ.get("DORMANT_DAYS", "30"))
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "noreply@company.com")
NOTIFICATION_EMAILS = os.environ.get("NOTIFICATION_EMAILS", "").split(",")


# ── Tier constants ────────────────────────────────────────────────────────────

ACTIVE = "ACTIVE"
LOW_TRAFFIC = "LOW_TRAFFIC"
DORMANT = "DORMANT"
ORPHANED = "ORPHANED"


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    # Get the latest scan CSV from S3
    scan_csv_key = event.get("csv_key") or get_latest_scan_csv()
    if not scan_csv_key:
        logger.error("No scan CSV found in S3")
        return {"error": "No scan CSV found"}

    logger.info(f"Processing scan CSV: {scan_csv_key}")

    # Download and parse CSV
    records = download_and_parse_csv(scan_csv_key)
    logger.info(f"Classifying {len(records)} API record(s)")

    # Classify each record
    counts = {ACTIVE: 0, LOW_TRAFFIC: 0, DORMANT: 0, ORPHANED: 0}
    classified_records = []

    for record in records:
        tier, reason = classify(record)
        counts[tier] += 1

        # Add classification fields
        classified_record = record.copy()
        classified_record.update({
            "Tier": tier,
            "TierReason": reason,
            "ClassifiedDate": datetime.now(timezone.utc).isoformat(),
            "RecommendedAction": "DELETE" if tier in [DORMANT, ORPHANED] else "REVIEW",
            "ApprovalStatus": "",  # To be filled by team
            "ReviewerName": "",  # To be filled by team
            "ReviewerComments": "",  # To be filled by team
            "ReviewDate": "",  # To be filled by team
        })
        classified_records.append(classified_record)

    # Generate and upload classification CSV
    classification_csv_key = generate_classification_csv(classified_records)

    # Send email notification to team
    send_notification_email(classification_csv_key, counts)

    # Send individual notifications to API owners for DORMANT/ORPHANED APIs
    owner_notifications = send_owner_notifications(classified_records)

    logger.info(f"Classification results: {counts}")
    logger.info(f"Owner notifications sent: {owner_notifications['sent']}, failed: {owner_notifications['failed']}")

    return {
        "classified": len(classified_records),
        "counts": counts,
        "csv_location": f"s3://{S3_BUCKET}/{classification_csv_key}",
        "classification_date": datetime.now(timezone.utc).isoformat(),
        "owner_notifications": owner_notifications
    }


# ── Classification logic ──────────────────────────────────────────────────────

def classify(record: dict) -> tuple[str, str]:
    """Classify API and return (tier, reason)."""
    count_90d = int(record.get("InvocationCount90d", 0))
    has_stages = record.get("HasStages", "false").lower() == "true"
    last_invocation = record.get("LastInvocationDate", "never")
    avg_daily = float(record.get("AvgRequestsPerDay", 0))

    # ORPHANED: never had a stage or never had any traffic at all
    if not has_stages:
        return ORPHANED, "No active stages"

    if count_90d == 0 and last_invocation == "never":
        return ORPHANED, "No traffic ever recorded"

    # DORMANT: had a stage but zero traffic for DORMANT_DAYS+
    if count_90d == 0:
        return DORMANT, f"Zero traffic for {LOOKBACK_DAYS} days"

    # LOW_TRAFFIC: less than threshold req/day on average
    if avg_daily < LOW_TRAFFIC_THRESHOLD:
        # Check if traffic is recent (last 30 days)
        if _last_traffic_within_days(last_invocation, 30):
            return LOW_TRAFFIC, f"Low traffic: {avg_daily:.2f} req/day (threshold: {LOW_TRAFFIC_THRESHOLD})"
        else:
            days_since = _days_since_last_traffic(last_invocation)
            return DORMANT, f"No traffic for {days_since} days (last traffic > 30 days ago)"

    return ACTIVE, f"Active traffic: {avg_daily:.2f} req/day"


def _last_traffic_within_days(last_invocation: str, days: int) -> bool:
    if last_invocation in ("never", "unknown", ""):
        return False
    try:
        dt = datetime.fromisoformat(last_invocation.replace("Z", "+00:00"))
        return dt >= datetime.now(timezone.utc) - timedelta(days=days)
    except ValueError:
        return False


def _days_since_last_traffic(last_invocation: str) -> int:
    if last_invocation in ("never", "unknown", ""):
        return 999
    try:
        dt = datetime.fromisoformat(last_invocation.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except ValueError:
        return 999


# ── S3 and CSV helpers ───────────────────────────────────────────────────────

def get_latest_scan_csv() -> str:
    """Get the most recent scan CSV from S3."""
    s3 = boto3.client("s3")
    try:
        response = s3.list_objects_v2(
            Bucket=S3_BUCKET,
            Prefix=S3_SCAN_PREFIX,
            MaxKeys=100
        )

        if "Contents" not in response:
            return ""

        # Sort by LastModified descending
        objects = sorted(
            response["Contents"],
            key=lambda x: x["LastModified"],
            reverse=True
        )

        # Find first CSV file
        for obj in objects:
            if obj["Key"].endswith(".csv"):
                return obj["Key"]

        return ""
    except Exception as e:
        logger.error(f"Error listing S3 objects: {e}")
        return ""


def download_and_parse_csv(s3_key: str) -> list:
    """Download CSV from S3 and parse into list of dicts."""
    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response["Body"].read().decode("utf-8")

        csv_reader = csv.DictReader(StringIO(csv_content))
        return list(csv_reader)
    except Exception as e:
        logger.error(f"Error downloading/parsing CSV from S3: {e}")
        return []


def generate_classification_csv(records: list) -> str:
    """Generate classification CSV and upload to S3. Returns S3 key."""
    if not records:
        logger.warning("No records to write to classification CSV")
        return ""

    # Define CSV columns (scan columns + classification columns)
    fieldnames = [
        "ScanDate", "AccountId", "AccountName", "Region", "ApiId", "ApiName",
        "ApiType", "CreatedDate", "HasStages", "StageNames", "HasCustomDomain",
        "HasUsagePlan", "InvocationCount90d", "LastInvocationDate",
        "AvgRequestsPerDay", "OwnerEmail", "TeamTag", "Tags",
        # Classification columns
        "Tier", "TierReason", "ClassifiedDate", "RecommendedAction",
        # Manual review columns (empty)
        "ApprovalStatus", "ReviewerName", "ReviewerComments", "ReviewDate"
    ]

    # Create CSV in memory
    csv_buffer = StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(records)

    # Upload to S3
    s3 = boto3.client("s3")
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    s3_key = f"{S3_CLASSIFICATION_PREFIX}api-classification-{date_str}.csv"

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=csv_buffer.getvalue(),
        ContentType="text/csv",
        Metadata={
            "classification-date": datetime.now(timezone.utc).isoformat(),
            "record-count": str(len(records))
        }
    )

    logger.info(f"Uploaded classification CSV to s3://{S3_BUCKET}/{s3_key}")
    return s3_key


def send_notification_email(csv_key: str, counts: dict):
    """Send email notification to team with classification results."""
    if not NOTIFICATION_EMAILS or not NOTIFICATION_EMAILS[0]:
        logger.warning("No notification emails configured, skipping email")
        return

    ses = boto3.client("ses")

    # Generate pre-signed URL for CSV download (valid for 7 days)
    s3 = boto3.client("s3")
    csv_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": csv_key},
        ExpiresIn=604800  # 7 days
    )

    subject = f"[API Cleanup] New Classification Results - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    body_text = f"""
API Gateway Unused API Classification Report

Classification Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}

Summary:
--------
ACTIVE:       {counts.get(ACTIVE, 0)} APIs
LOW_TRAFFIC:  {counts.get(LOW_TRAFFIC, 0)} APIs
DORMANT:      {counts.get(DORMANT, 0)} APIs (recommended for deletion)
ORPHANED:     {counts.get(ORPHANED, 0)} APIs (recommended for deletion)

Total Deletion Candidates: {counts.get(DORMANT, 0) + counts.get(ORPHANED, 0)} APIs

Next Steps:
-----------
1. Download the classification CSV from:
   {csv_url}

2. Review each API marked as DORMANT or ORPHANED

3. Update the following columns for each API:
   - ApprovalStatus: Set to APPROVE_DELETE, KEEP, or EXTEND_REVIEW
   - ReviewerName: Your name
   - ReviewerComments: Brief reason for your decision
   - ReviewDate: Today's date

4. Upload the reviewed CSV to:
   s3://{S3_BUCKET}/approvals/api-approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv

5. Trigger the Jenkins cleanup pipeline manually or wait for automatic trigger

Questions? Contact: {SES_SENDER_EMAIL}
"""

    body_html = f"""
<html>
<head></head>
<body>
<h2>API Gateway Unused API Classification Report</h2>
<p><strong>Classification Date:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>

<h3>Summary</h3>
<table border="1" cellpadding="5">
<tr><th>Tier</th><th>Count</th></tr>
<tr><td>ACTIVE</td><td>{counts.get(ACTIVE, 0)}</td></tr>
<tr><td>LOW_TRAFFIC</td><td>{counts.get(LOW_TRAFFIC, 0)}</td></tr>
<tr style="background-color: #fff3cd;"><td>DORMANT</td><td>{counts.get(DORMANT, 0)}</td></tr>
<tr style="background-color: #f8d7da;"><td>ORPHANED</td><td>{counts.get(ORPHANED, 0)}</td></tr>
</table>

<p><strong>Total Deletion Candidates:</strong> {counts.get(DORMANT, 0) + counts.get(ORPHANED, 0)} APIs</p>

<h3>Next Steps</h3>
<ol>
<li>Download the classification CSV: <a href="{csv_url}">Click here</a></li>
<li>Review each API marked as DORMANT or ORPHANED</li>
<li>Update approval columns: ApprovalStatus, ReviewerName, ReviewerComments, ReviewDate</li>
<li>Upload reviewed CSV to: <code>s3://{S3_BUCKET}/approvals/api-approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv</code></li>
<li>Trigger the Jenkins cleanup pipeline</li>
</ol>

<p>Questions? Contact: {SES_SENDER_EMAIL}</p>
</body>
</html>
"""

    try:
        ses.send_email(
            Source=SES_SENDER_EMAIL,
            Destination={"ToAddresses": [email.strip() for email in NOTIFICATION_EMAILS if email.strip()]},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Text": {"Data": body_text},
                    "Html": {"Data": body_html}
                }
            }
        )
        logger.info(f"Sent notification email to {NOTIFICATION_EMAILS}")
    except Exception as e:
        logger.error(f"Failed to send notification email: {e}")


def send_owner_notifications(records: list) -> dict:
    """Send individual notification emails to API owners for DORMANT/ORPHANED APIs."""
    ses = boto3.client("ses")

    # Filter only DORMANT and ORPHANED APIs with valid owner emails
    apis_to_notify = [
        r for r in records
        if r.get("Tier") in [DORMANT, ORPHANED] and r.get("OwnerEmail")
    ]

    # Group APIs by owner email
    owner_apis = {}
    for api in apis_to_notify:
        owner = api.get("OwnerEmail")
        if owner not in owner_apis:
            owner_apis[owner] = []
        owner_apis[owner].append(api)

    logger.info(f"Sending owner notifications to {len(owner_apis)} unique owners for {len(apis_to_notify)} APIs")

    sent_count = 0
    failed_count = 0

    for owner_email, apis in owner_apis.items():
        try:
            # Calculate scheduled deletion date (30 days from now)
            deletion_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime('%Y-%m-%d')
            response_deadline = (datetime.now(timezone.utc) + timedelta(days=14)).strftime('%Y-%m-%d')

            # Build API list for email
            api_list_text = []
            api_list_html = []

            for api in apis:
                api_list_text.append(
                    f"\n  API Name:    {api.get('ApiName', 'N/A')}\n"
                    f"  API ID:      {api.get('ApiId', 'N/A')}\n"
                    f"  Region:      {api.get('Region', 'N/A')}\n"
                    f"  Account:     {api.get('AccountId', 'N/A')} ({api.get('AccountName', 'N/A')})\n"
                    f"  Last seen:   {api.get('LastInvocationDate', 'Never')}\n"
                    f"  Tier:        {api.get('Tier', 'N/A')}\n"
                    f"  Reason:      {api.get('TierReason', 'N/A')}\n"
                )

                api_list_html.append(
                    f"<tr>"
                    f"<td>{api.get('ApiName', 'N/A')}</td>"
                    f"<td>{api.get('ApiId', 'N/A')}</td>"
                    f"<td>{api.get('Region', 'N/A')}</td>"
                    f"<td>{api.get('Tier', 'N/A')}</td>"
                    f"<td>{api.get('LastInvocationDate', 'Never')}</td>"
                    f"<td>{api.get('TierReason', 'N/A')}</td>"
                    f"</tr>"
                )

            subject = f"[ACTION REQUIRED] {len(apis)} Unused AWS API Gateway API(s) scheduled for deletion"

            body_text = f"""
Hi,

Our automated API hygiene scan has identified {len(apis)} API Gateway endpoint(s)
owned by you as unused based on 90 days of CloudWatch metrics:

{''.join(api_list_text)}

Scheduled deletion date: {deletion_date}

ACTIONS YOU CAN TAKE BEFORE DELETION:
--------------------------------------
1. Reply to confirm these APIs can be deleted.
2. Reply to request a 30-day extension (one extension allowed).
3. Tag the API with `lifecycle: protected` to permanently exclude it from cleanup.

If no response is received by {response_deadline}, the deletion will proceed through
our standard approval workflow.

WHAT HAPPENS NEXT:
------------------
1. Our platform team will review the classification in the next few days.
2. If approved for deletion, APIs will be:
   - Backed up (OpenAPI specs archived to S3)
   - Soft deleted (throttled to 0 req/s) for 7 days
   - Hard deleted (permanently removed) after final approval

You will receive another notification before the final deletion.

QUESTIONS OR CONCERNS?
----------------------
Contact: {SES_SENDER_EMAIL}
Reply to this email with "KEEP" if you need these APIs retained.

This is an automated message from the AWS API Gateway Cleanup Service.
"""

            body_html = f"""
<html>
<head></head>
<body>
<h2 style="color: #d9534f;">[ACTION REQUIRED] Unused API Gateway API(s) Detected</h2>

<p>Hi,</p>

<p>Our automated API hygiene scan has identified <strong>{len(apis)}</strong> API Gateway endpoint(s)
owned by you as unused based on 90 days of CloudWatch metrics:</p>

<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
<thead style="background-color: #f5f5f5;">
<tr>
<th>API Name</th>
<th>API ID</th>
<th>Region</th>
<th>Tier</th>
<th>Last Seen</th>
<th>Reason</th>
</tr>
</thead>
<tbody>
{''.join(api_list_html)}
</tbody>
</table>

<p><strong style="color: #d9534f;">Scheduled deletion date: {deletion_date}</strong></p>

<h3>Actions You Can Take Before Deletion:</h3>
<ol>
<li><strong>Reply to confirm</strong> these APIs can be deleted.</li>
<li><strong>Reply to request</strong> a 30-day extension (one extension allowed).</li>
<li><strong>Tag the API</strong> with <code>lifecycle: protected</code> to permanently exclude it from cleanup.</li>
</ol>

<p style="background-color: #fff3cd; padding: 10px; border-left: 4px solid #ff9800;">
<strong>⚠️ Important:</strong> If no response is received by <strong>{response_deadline}</strong>, 
the deletion will proceed through our standard approval workflow.
</p>

<h3>What Happens Next:</h3>
<ol>
<li>Our platform team will review the classification in the next few days.</li>
<li>If approved for deletion, APIs will be:
<ul>
<li><strong>Backed up</strong> (OpenAPI specs archived to S3)</li>
<li><strong>Soft deleted</strong> (throttled to 0 req/s) for 7 days</li>
<li><strong>Hard deleted</strong> (permanently removed) after final approval</li>
</ul>
</li>
<li>You will receive another notification before the final deletion.</li>
</ol>

<h3>Questions or Concerns?</h3>
<p>Contact: <a href="mailto:{SES_SENDER_EMAIL}">{SES_SENDER_EMAIL}</a><br>
Reply to this email with <strong>"KEEP"</strong> if you need these APIs retained.</p>

<hr>
<p style="font-size: 12px; color: #666;">
This is an automated message from the AWS API Gateway Cleanup Service.<br>
Classification Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
</p>
</body>
</html>
"""

            ses.send_email(
                Source=SES_SENDER_EMAIL,
                Destination={"ToAddresses": [owner_email]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": body_text},
                        "Html": {"Data": body_html}
                    }
                }
            )

            logger.info(f"✓ Sent owner notification to {owner_email} for {len(apis)} APIs")
            sent_count += 1

        except Exception as e:
            logger.error(f"✗ Failed to send owner notification to {owner_email}: {e}")
            failed_count += 1

    return {
        "sent": sent_count,
        "failed": failed_count,
        "total_owners": len(owner_apis),
        "total_apis": len(apis_to_notify)
    }


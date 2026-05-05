"""
lambda_scanner.py
─────────────────
Lambda #1 — Scans all API Gateway APIs across all enabled regions,
pulls 90-day CloudWatch invocation counts, and generates CSV file
uploaded to S3 for manual review.

Triggered by: EventBridge Scheduler (weekly, Monday 08:00 UTC)
IAM role needs: apigateway:GET, apigatewayv2:GET,
                cloudwatch:GetMetricStatistics, s3:PutObject,
                account:ListRegions
"""

import os
import boto3
import logging
import csv
import json
from io import StringIO
from datetime import datetime, timedelta, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ.get("S3_BUCKET", "api-gateway-cleanup-bucket")
S3_SCAN_PREFIX = os.environ.get("S3_SCAN_PREFIX", "scans/")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "90"))


# ── Entry point ──────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    scan_date = datetime.now(timezone.utc)
    regions = get_enabled_regions()
    logger.info(f"Scanning {len(regions)} region(s): {regions}")

    inventory = []
    for region in regions:
        try:
            inventory.extend(scan_rest_apis(region, scan_date))
            inventory.extend(scan_v2_apis(region, scan_date))
        except Exception as e:
            logger.error(f"Error scanning region {region}: {e}")

    csv_key = generate_csv(inventory, scan_date)
    logger.info(f"Generated CSV with {len(inventory)} API record(s): s3://{S3_BUCKET}/{csv_key}")

    return {
        "scanned": len(inventory),
        "regions": regions,
        "csv_location": f"s3://{S3_BUCKET}/{csv_key}",
        "scan_date": scan_date.isoformat()
    }


# ── Region discovery ─────────────────────────────────────────────────────────

def get_enabled_regions():
    client = boto3.client("account")
    paginator = client.get_paginator("list_regions")
    regions = []
    for page in paginator.paginate(RegionOptStatusContains=["ENABLED"]):
        regions.extend([r["RegionName"] for r in page["Regions"]])
    return regions


# ── REST API (v1) scanner ────────────────────────────────────────────────────

def scan_rest_apis(region: str, scan_date: datetime) -> list:
    apigw = boto3.client("apigateway", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)
    results = []

    paginator = apigw.get_paginator("get_rest_apis")
    for page in paginator.paginate():
        for api in page.get("items", []):
            api_id = api["id"]
            api_name = api["name"]

            # Skip protected APIs
            tags = api.get("tags", {})
            if is_protected(tags):
                logger.info(f"Skipping protected REST API: {api_id} ({api_name})")
                continue

            count = get_invocation_count(cw, api_id=api_id, region=region)
            last_invocation = get_last_invocation_date(cw, api_id=api_id, region=region)
            stages = get_stages(apigw, api_id)
            has_custom_domain = check_has_custom_domain(apigw, api_id)
            has_usage_plan = check_has_usage_plan(apigw, api_id)

            avg_requests_per_day = count / LOOKBACK_DAYS if count > 0 else 0

            results.append({
                "ScanDate": scan_date.isoformat(),
                "AccountId": get_account_id(),
                "AccountName": get_account_name(),
                "Region": region,
                "ApiId": api_id,
                "ApiName": api_name,
                "ApiType": "REST",
                "CreatedDate": safe_isoformat(api.get("createdDate")),
                "HasStages": "true" if stages else "false",
                "StageNames": ",".join(stages) if stages else "",
                "HasCustomDomain": "true" if has_custom_domain else "false",
                "HasUsagePlan": "true" if has_usage_plan else "false",
                "InvocationCount90d": count,
                "LastInvocationDate": last_invocation,
                "AvgRequestsPerDay": round(avg_requests_per_day, 2),
                "OwnerEmail": tags.get("owner", tags.get("Owner", "")),
                "TeamTag": tags.get("team", tags.get("Team", "")),
                "Tags": json.dumps(tags),
            })

    return results


# ── HTTP / WebSocket API (v2) scanner ────────────────────────────────────────

def scan_v2_apis(region: str, scan_date: datetime) -> list:
    apigwv2 = boto3.client("apigatewayv2", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)
    results = []

    paginator = apigwv2.get_paginator("get_apis")
    for page in paginator.paginate():
        for api in page.get("Items", []):
            api_id = api["ApiId"]
            api_name = api["Name"]

            tags = api.get("Tags", {})
            if is_protected(tags):
                logger.info(f"Skipping protected v2 API: {api_id} ({api_name})")
                continue

            count = get_invocation_count(cw, api_id=api_id, region=region)
            last_invocation = get_last_invocation_date(cw, api_id=api_id, region=region)
            stages = get_v2_stages(apigwv2, api_id)

            avg_requests_per_day = count / LOOKBACK_DAYS if count > 0 else 0

            results.append({
                "ScanDate": scan_date.isoformat(),
                "AccountId": get_account_id(),
                "AccountName": get_account_name(),
                "Region": region,
                "ApiId": api_id,
                "ApiName": api_name,
                "ApiType": api.get("ProtocolType", "HTTP"),
                "CreatedDate": safe_isoformat(api.get("CreatedDate")),
                "HasStages": "true" if stages else "false",
                "StageNames": ",".join(stages) if stages else "",
                "HasCustomDomain": "false",  # v2 API custom domain check can be added
                "HasUsagePlan": "false",  # v2 APIs don't use usage plans
                "InvocationCount90d": count,
                "LastInvocationDate": last_invocation,
                "AvgRequestsPerDay": round(avg_requests_per_day, 2),
                "OwnerEmail": tags.get("owner", tags.get("Owner", "")),
                "TeamTag": tags.get("team", tags.get("Team", "")),
                "Tags": json.dumps(tags),
            })

    return results


# ── CloudWatch helpers ───────────────────────────────────────────────────────

def get_invocation_count(cw_client, api_id: str, region: str) -> int:
    """Return total invocation Count over the look-back window."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    try:
        # List all available metrics for this API
        metrics_response = cw_client.list_metrics(
            Namespace="AWS/ApiGateway",
            MetricName="Count",
            Dimensions=[{"Name": "ApiId", "Value": api_id}]
        )

        total_count = 0
        processed_dims = set()

        for metric in metrics_response.get("Metrics", []):
            # Create unique key for dimension combination
            dim_key = tuple(sorted((d["Name"], d["Value"]) for d in metric["Dimensions"]))
            if dim_key in processed_dims:
                continue
            processed_dims.add(dim_key)

            # Query this specific dimension combination
            response = cw_client.get_metric_statistics(
                Namespace="AWS/ApiGateway",
                MetricName="Count",
                Dimensions=metric["Dimensions"],
                StartTime=start,
                EndTime=end,
                Period=86400,
                Statistics=["Sum"],
            )

            datapoints = response.get("Datapoints", [])
            for dp in datapoints:
                total_count += int(dp.get("Sum", 0))

        return total_count
    except Exception as e:
        logger.warning(f"CloudWatch query failed for {api_id}: {e}")
        return 0


def get_last_invocation_date(cw_client, api_id: str, region: str) -> str:
    """Return ISO8601 timestamp of the last non-zero data point, or 'never'."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    try:
        response = cw_client.get_metric_statistics(
            Namespace="AWS/ApiGateway",
            MetricName="Count",
            Dimensions=[{"Name": "ApiId", "Value": api_id}],
            StartTime=start,
            EndTime=end,
            Period=86400,
            Statistics=["Sum"],
        )
        datapoints = sorted(
            [dp for dp in response.get("Datapoints", []) if dp["Sum"] > 0],
            key=lambda x: x["Timestamp"],
            reverse=True,
        )
        return datapoints[0]["Timestamp"].isoformat() if datapoints else "never"
    except Exception:
        return "unknown"


# ── Helpers ──────────────────────────────────────────────────────────────────

_ACCOUNT_ID_CACHE = None
_ACCOUNT_NAME_CACHE = None

def get_account_id() -> str:
    global _ACCOUNT_ID_CACHE
    if not _ACCOUNT_ID_CACHE:
        _ACCOUNT_ID_CACHE = boto3.client("sts").get_caller_identity()["Account"]
    return _ACCOUNT_ID_CACHE


def get_account_name() -> str:
    global _ACCOUNT_NAME_CACHE
    if not _ACCOUNT_NAME_CACHE:
        try:
            iam = boto3.client("iam")
            aliases = iam.list_account_aliases()["AccountAliases"]
            _ACCOUNT_NAME_CACHE = aliases[0] if aliases else get_account_id()
        except Exception:
            _ACCOUNT_NAME_CACHE = get_account_id()
    return _ACCOUNT_NAME_CACHE


def get_stages(apigw_client, api_id: str) -> list:
    """Return list of stage names for REST API."""
    try:
        response = apigw_client.get_stages(restApiId=api_id)
        return [stage["stageName"] for stage in response.get("item", [])]
    except Exception:
        return []


def get_v2_stages(apigwv2_client, api_id: str) -> list:
    """Return list of stage names for v2 API."""
    try:
        response = apigwv2_client.get_stages(ApiId=api_id)
        return [stage["StageName"] for stage in response.get("Items", [])]
    except Exception:
        return []


def check_has_custom_domain(apigw_client, api_id: str) -> bool:
    """Check if REST API has custom domain mapping."""
    try:
        response = apigw_client.get_base_path_mappings()
        for mapping in response.get("items", []):
            if mapping.get("restApiId") == api_id:
                return True
        return False
    except Exception:
        return False


def check_has_usage_plan(apigw_client, api_id: str) -> bool:
    """Check if REST API is attached to any usage plan."""
    try:
        response = apigw_client.get_usage_plans()
        for plan in response.get("items", []):
            for api_stage in plan.get("apiStages", []):
                if api_stage.get("apiId") == api_id:
                    return True
        return False
    except Exception:
        return False


def is_protected(tags: dict) -> bool:
    protected = {
        "lifecycle": "protected",
        "do-not-delete": "true",
    }
    return any(tags.get(k) == v for k, v in protected.items())


def safe_isoformat(dt) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def generate_csv(records: list, scan_date: datetime) -> str:
    """Generate CSV file and upload to S3. Returns S3 key."""
    if not records:
        logger.warning("No records to write to CSV")
        return ""

    # Define CSV columns
    fieldnames = [
        "ScanDate", "AccountId", "AccountName", "Region", "ApiId", "ApiName",
        "ApiType", "CreatedDate", "HasStages", "StageNames", "HasCustomDomain",
        "HasUsagePlan", "InvocationCount90d", "LastInvocationDate",
        "AvgRequestsPerDay", "OwnerEmail", "TeamTag", "Tags"
    ]

    # Create CSV in memory
    csv_buffer = StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)

    # Upload to S3
    s3 = boto3.client("s3")
    date_str = scan_date.strftime("%Y%m%d-%H%M%S")
    s3_key = f"{S3_SCAN_PREFIX}api-scan-{date_str}.csv"

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=csv_buffer.getvalue(),
        ContentType="text/csv",
        Metadata={
            "scan-date": scan_date.isoformat(),
            "record-count": str(len(records))
        }
    )

    logger.info(f"Uploaded CSV to s3://{S3_BUCKET}/{s3_key}")
    return s3_key

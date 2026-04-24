"""
lambda_reporter.py
──────────────────
Lambda — Scans all API Gateway APIs across all enabled regions and
collects a rich inventory:

    Index | ApiName | ApiId | Basepath | Stage | Domain | Authorizer |
    IntegrationType | IntegrationUri | IntegrationTimeout | AccountId |
    AccountName | Count (90-day invocation total)

Sends the report as an HTML email (with an inline CSV attachment) to:
    • mdziaur.rahman@corebridgefinancial.com
    • mdziaur.rahman@mphasis.com
    • sust.cse.zia@gmail.com

Triggered by:  EventBridge Scheduler (or manual invoke)
IAM role needs:
    apigateway:GET  apigatewayv2:GET
    cloudwatch:GetMetricStatistics
    ses:SendRawEmail
    account:ListRegions
    sts:GetCallerIdentity
    organizations:DescribeAccount   (optional — for AccountName)
"""

import csv
import io
import os
import boto3
import logging
import email.mime.multipart as mmp
import email.mime.text as mmt
import email.mime.application as mma
from datetime import datetime, timedelta, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "90"))
SES_SENDER    = os.environ.get("SES_SENDER_EMAIL", "mdziaur.rahman@corebridgefinancial.com")
RECIPIENTS    = [
    "mdziaur.rahman@corebridgefinancial.com",
    "mdziaur.rahman@mphasis.com",
    "sust.cse.zia@gmail.com",
]

REPORT_COLUMNS = [
    "Index", "ApiName", "ApiId", "Basepath", "Stage", "Domain",
    "Authorizer", "IntegrationType", "IntegrationUri",
    "IntegrationTimeout", "AccountId", "AccountName", "Count",
]

# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    account_id   = get_account_id()
    account_name = get_account_name(account_id)
    regions      = get_enabled_regions()

    logger.info(f"Account: {account_id} ({account_name}) | Scanning {len(regions)} region(s)")

    rows = []
    for region in regions:
        try:
            rows.extend(collect_rest_apis(region, account_id, account_name))
            rows.extend(collect_v2_apis(region, account_id, account_name))
        except Exception as exc:
            logger.error(f"Error scanning region {region}: {exc}")

    # Add 1-based index
    for idx, row in enumerate(rows, start=1):
        row["Index"] = idx

    logger.info(f"Collected {len(rows)} API record(s)")

    csv_bytes  = build_csv(rows)
    html_body  = build_html(rows, account_id, account_name)
    send_report(html_body, csv_bytes, total=len(rows))

    return {"status": "ok", "total_apis": len(rows), "regions_scanned": len(regions)}


# ── Region / account discovery ────────────────────────────────────────────────

def get_enabled_regions() -> list[str]:
    client    = boto3.client("account")
    paginator = client.get_paginator("list_regions")
    regions   = []
    for page in paginator.paginate(RegionOptStatusContains=["ENABLED", "ENABLED_BY_DEFAULT"]):
        regions.extend(r["RegionName"] for r in page["Regions"])
    return regions


_ACCOUNT_ID_CACHE = None

def get_account_id() -> str:
    global _ACCOUNT_ID_CACHE
    if not _ACCOUNT_ID_CACHE:
        _ACCOUNT_ID_CACHE = boto3.client("sts").get_caller_identity()["Account"]
    return _ACCOUNT_ID_CACHE


def get_account_name(account_id: str) -> str:
    try:
        orgs = boto3.client("organizations")
        resp = orgs.describe_account(AccountId=account_id)
        return resp["Account"]["Name"]
    except Exception:
        return account_id   # fall back to account ID if no Org access


# ── REST API (v1) collector ───────────────────────────────────────────────────

def collect_rest_apis(region: str, account_id: str, account_name: str) -> list[dict]:
    apigw = boto3.client("apigateway", region_name=region)
    cw    = boto3.client("cloudwatch",  region_name=region)
    rows  = []

    paginator = apigw.get_paginator("get_rest_apis")
    for page in paginator.paginate():
        for api in page.get("items", []):
            api_id   = api["id"]
            api_name = api["name"]

            # ── Stages ──────────────────────────────────────────────────────
            stages = get_rest_stages(apigw, api_id)   # list of stage names

            # ── Base-path mappings ───────────────────────────────────────────
            basepaths = get_rest_basepaths(apigw, api_id)

            # ── Custom domains (via base-path mapping) ───────────────────────
            domains = get_rest_domains(apigw, api_id)

            # ── Authorizers ──────────────────────────────────────────────────
            authorizers = get_rest_authorizers(apigw, api_id)

            # ── Integrations (first resource found) ──────────────────────────
            integration = get_rest_integration_summary(apigw, api_id)

            # ── CloudWatch 90-day count ───────────────────────────────────────
            count = get_invocation_count(cw, api_name=api_name)

            rows.append({
                "Index":              0,          # filled in later
                "ApiName":            api_name,
                "ApiId":              api_id,
                "Basepath":           "; ".join(basepaths) if basepaths else "/",
                "Stage":              "; ".join(stages) if stages else "",
                "Domain":             "; ".join(domains) if domains else "",
                "Authorizer":         "; ".join(authorizers) if authorizers else "NONE",
                "IntegrationType":    integration.get("type", ""),
                "IntegrationUri":     integration.get("uri", ""),
                "IntegrationTimeout": integration.get("timeout_ms", ""),
                "AccountId":          account_id,
                "AccountName":        account_name,
                "Count":              count,
            })

    return rows


def get_rest_stages(apigw, api_id: str) -> list[str]:
    try:
        resp = apigw.get_stages(restApiId=api_id)
        return [s["stageName"] for s in resp.get("item", [])]
    except Exception:
        return []


def get_rest_basepaths(apigw, api_id: str) -> list[str]:
    """Collect base-path mappings across all custom domains for this API."""
    paths = []
    try:
        domains_page = apigw.get_paginator("get_domain_names").paginate()
        for page in domains_page:
            for domain in page.get("items", []):
                domain_name = domain["domainName"]
                try:
                    mappings = apigw.get_base_path_mappings(domainName=domain_name)
                    for m in mappings.get("items", []):
                        if m.get("restApiId") == api_id:
                            paths.append(m.get("basePath", "(none)"))
                except Exception:
                    pass
    except Exception:
        pass
    return paths


def get_rest_domains(apigw, api_id: str) -> list[str]:
    """Return custom domain names that map to this API."""
    domains = []
    try:
        pages = apigw.get_paginator("get_domain_names").paginate()
        for page in pages:
            for domain in page.get("items", []):
                domain_name = domain["domainName"]
                try:
                    mappings = apigw.get_base_path_mappings(domainName=domain_name)
                    for m in mappings.get("items", []):
                        if m.get("restApiId") == api_id:
                            domains.append(domain_name)
                            break
                except Exception:
                    pass
    except Exception:
        pass
    return domains


def get_rest_authorizers(apigw, api_id: str) -> list[str]:
    try:
        resp = apigw.get_authorizers(restApiId=api_id)
        return [f"{a['name']} ({a['type']})" for a in resp.get("items", [])]
    except Exception:
        return []


def get_rest_integration_summary(apigw, api_id: str) -> dict:
    """Return integration details from the first resource/method found."""
    try:
        resources = apigw.get_resources(restApiId=api_id, embed=["methods"])
        for resource in resources.get("items", []):
            methods = resource.get("resourceMethods", {})
            for method_key, method_val in methods.items():
                if method_key == "OPTIONS":
                    continue
                try:
                    integration = apigw.get_integration(
                        restApiId=api_id,
                        resourceId=resource["id"],
                        httpMethod=method_key,
                    )
                    return {
                        "type":       integration.get("type", ""),
                        "uri":        integration.get("uri", ""),
                        "timeout_ms": integration.get("timeoutInMillis", ""),
                    }
                except Exception:
                    continue
    except Exception:
        pass
    return {}


# ── HTTP / WebSocket API (v2) collector ───────────────────────────────────────

def collect_v2_apis(region: str, account_id: str, account_name: str) -> list[dict]:
    apigwv2 = boto3.client("apigatewayv2", region_name=region)
    cw      = boto3.client("cloudwatch",   region_name=region)
    rows    = []

    paginator = apigwv2.get_paginator("get_apis")
    for page in paginator.paginate():
        for api in page.get("Items", []):
            api_id   = api["ApiId"]
            api_name = api["Name"]

            stages      = get_v2_stages(apigwv2, api_id)
            domains     = get_v2_domains(apigwv2, api_id)
            authorizers = get_v2_authorizers(apigwv2, api_id)
            integration = get_v2_integration_summary(apigwv2, api_id)
            count       = get_invocation_count(cw, api_name=api_name)

            # basepath lives on the API mapping
            basepaths = get_v2_basepaths(apigwv2, api_id)

            rows.append({
                "Index":              0,
                "ApiName":            api_name,
                "ApiId":              api_id,
                "Basepath":           "; ".join(basepaths) if basepaths else "/",
                "Stage":              "; ".join(stages) if stages else "$default",
                "Domain":             "; ".join(domains) if domains else "",
                "Authorizer":         "; ".join(authorizers) if authorizers else "NONE",
                "IntegrationType":    integration.get("type", ""),
                "IntegrationUri":     integration.get("uri", ""),
                "IntegrationTimeout": integration.get("timeout_ms", ""),
                "AccountId":          account_id,
                "AccountName":        account_name,
                "Count":              count,
            })

    return rows


def get_v2_stages(apigwv2, api_id: str) -> list[str]:
    try:
        resp = apigwv2.get_stages(ApiId=api_id)
        return [s["StageName"] for s in resp.get("Items", [])]
    except Exception:
        return []


def get_v2_domains(apigwv2, api_id: str) -> list[str]:
    domains = []
    try:
        pages = apigwv2.get_paginator("get_domain_names").paginate()
        for page in pages:
            for domain in page.get("Items", []):
                domain_name = domain["DomainName"]
                try:
                    mappings = apigwv2.get_api_mappings(DomainName=domain_name)
                    for m in mappings.get("Items", []):
                        if m.get("ApiId") == api_id:
                            domains.append(domain_name)
                            break
                except Exception:
                    pass
    except Exception:
        pass
    return domains


def get_v2_basepaths(apigwv2, api_id: str) -> list[str]:
    paths = []
    try:
        pages = apigwv2.get_paginator("get_domain_names").paginate()
        for page in pages:
            for domain in page.get("Items", []):
                domain_name = domain["DomainName"]
                try:
                    mappings = apigwv2.get_api_mappings(DomainName=domain_name)
                    for m in mappings.get("Items", []):
                        if m.get("ApiId") == api_id:
                            paths.append(m.get("ApiMappingKey", "/"))
                except Exception:
                    pass
    except Exception:
        pass
    return paths


def get_v2_authorizers(apigwv2, api_id: str) -> list[str]:
    try:
        resp = apigwv2.get_authorizers(ApiId=api_id)
        return [f"{a['Name']} ({a['AuthorizerType']})" for a in resp.get("Items", [])]
    except Exception:
        return []


def get_v2_integration_summary(apigwv2, api_id: str) -> dict:
    try:
        resp = apigwv2.get_integrations(ApiId=api_id)
        for integration in resp.get("Items", []):
            return {
                "type":       integration.get("IntegrationType", ""),
                "uri":        integration.get("IntegrationUri", ""),
                "timeout_ms": integration.get("TimeoutInMillis", ""),
            }
    except Exception:
        pass
    return {}


# ── CloudWatch ────────────────────────────────────────────────────────────────

def get_invocation_count(cw_client, api_name: str) -> int:
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    try:
        response = cw_client.get_metric_statistics(
            Namespace="AWS/ApiGateway",
            MetricName="Count",
            Dimensions=[{"Name": "ApiName", "Value": api_name}],
            StartTime=start,
            EndTime=end,
            Period=LOOKBACK_DAYS * 86400,
            Statistics=["Sum"],
        )
        datapoints = response.get("Datapoints", [])
        return int(datapoints[0]["Sum"]) if datapoints else 0
    except Exception as exc:
        logger.warning(f"CloudWatch query failed for {api_name}: {exc}")
        return 0


# ── Report builders ───────────────────────────────────────────────────────────

def build_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def build_html(rows: list[dict], account_id: str, account_name: str) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    th_style = (
        "background:#1F497D;color:#fff;padding:7px 10px;"
        "font-size:12px;text-align:left;white-space:nowrap;"
    )
    td_style = "padding:6px 10px;font-size:11px;border-bottom:1px solid #e0e0e0;"
    td_alt   = "padding:6px 10px;font-size:11px;border-bottom:1px solid #e0e0e0;background:#f4f8ff;"

    header_cells = "".join(f"<th style='{th_style}'>{col}</th>" for col in REPORT_COLUMNS)

    data_rows_html = ""
    for i, row in enumerate(rows):
        style = td_alt if i % 2 == 0 else td_style
        cells = "".join(
            f"<td style='{style}'>{_esc(str(row.get(col, '')))}</td>"
            for col in REPORT_COLUMNS
        )
        data_rows_html += f"<tr>{cells}</tr>\n"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Calibri,Arial,sans-serif;color:#333;margin:0;padding:20px;">

  <h2 style="color:#1F497D;margin-bottom:4px;">
    AWS API Gateway — Full Inventory Report
  </h2>
  <p style="color:#666;font-size:13px;margin-top:0;">
    Account: <strong>{_esc(account_name)}</strong> ({_esc(account_id)}) &nbsp;|&nbsp;
    Lookback: <strong>{LOOKBACK_DAYS} days</strong> &nbsp;|&nbsp;
    Generated: <strong>{generated_at}</strong> &nbsp;|&nbsp;
    Total APIs: <strong>{len(rows)}</strong>
  </p>

  <table style="border-collapse:collapse;width:100%;min-width:900px;">
    <thead><tr>{header_cells}</tr></thead>
    <tbody>{data_rows_html}</tbody>
  </table>

  <p style="font-size:11px;color:#999;margin-top:16px;">
    A CSV copy of this report is attached for offline analysis.<br>
    This is an automated message from the Platform Engineering API inventory pipeline.
  </p>
</body>
</html>"""


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


# ── SES sender ────────────────────────────────────────────────────────────────

def send_report(html_body: str, csv_bytes: bytes, total: int):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"[API Inventory] AWS API Gateway Report — {total} APIs — {generated_at}"

    # Build multipart MIME message (HTML body + CSV attachment)
    msg = mmp.MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = SES_SENDER
    msg["To"]      = ", ".join(RECIPIENTS)

    # HTML part
    alt = mmp.MIMEMultipart("alternative")
    alt.attach(mmt.MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    # CSV attachment
    attachment = mma.MIMEApplication(csv_bytes, Name=f"api_inventory_{generated_at}.csv")
    attachment["Content-Disposition"] = (
        f'attachment; filename="api_inventory_{generated_at}.csv"'
    )
    msg.attach(attachment)

    ses = boto3.client("ses")
    ses.send_raw_email(
        Source=SES_SENDER,
        Destinations=RECIPIENTS,
        RawMessage={"Data": msg.as_string()},
    )
    logger.info(f"Report sent to {RECIPIENTS} — {total} APIs listed")

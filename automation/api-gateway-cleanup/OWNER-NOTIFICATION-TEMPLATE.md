# Sample Owner Notification Email

This document shows the email template that API owners will receive when their APIs are classified as DORMANT or ORPHANED.

---

## Email Template

**From**: noreply@company.com  
**To**: [API Owner Email from `owner` tag]  
**Subject**: [ACTION REQUIRED] N Unused AWS API Gateway API(s) scheduled for deletion

---

### Text Version

```
Hi,

Our automated API hygiene scan has identified 2 API Gateway endpoint(s)
owned by you as unused based on 90 days of CloudWatch metrics:


  API Name:    customer-api-v1
  API ID:      abc123xyz
  Region:      us-east-1
  Account:     123456789012 (prod-account)
  Last seen:   2026-02-10T14:20:00Z
  Tier:        DORMANT
  Reason:      Zero traffic for 45 days


  API Name:    legacy-reports-api
  API ID:      def456uvw
  Region:      us-east-1
  Account:     123456789012 (prod-account)
  Last seen:   Never
  Tier:        ORPHANED
  Reason:      No traffic ever recorded


Scheduled deletion date: 2026-05-30

ACTIONS YOU CAN TAKE BEFORE DELETION:
--------------------------------------
1. Reply to confirm these APIs can be deleted.
2. Reply to request a 30-day extension (one extension allowed).
3. Tag the API with `lifecycle: protected` to permanently exclude it from cleanup.

If no response is received by 2026-05-14, the deletion will proceed through
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
Contact: noreply@company.com
Reply to this email with "KEEP" if you need these APIs retained.

This is an automated message from the AWS API Gateway Cleanup Service.
```

---

### HTML Version

```html
<html>
<head></head>
<body>
<h2 style="color: #d9534f;">[ACTION REQUIRED] Unused API Gateway API(s) Detected</h2>

<p>Hi,</p>

<p>Our automated API hygiene scan has identified <strong>2</strong> API Gateway endpoint(s)
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
<tr>
<td>customer-api-v1</td>
<td>abc123xyz</td>
<td>us-east-1</td>
<td>DORMANT</td>
<td>2026-02-10T14:20:00Z</td>
<td>Zero traffic for 45 days</td>
</tr>
<tr>
<td>legacy-reports-api</td>
<td>def456uvw</td>
<td>us-east-1</td>
<td>ORPHANED</td>
<td>Never</td>
<td>No traffic ever recorded</td>
</tr>
</tbody>
</table>

<p><strong style="color: #d9534f;">Scheduled deletion date: 2026-05-30</strong></p>

<h3>Actions You Can Take Before Deletion:</h3>
<ol>
<li><strong>Reply to confirm</strong> these APIs can be deleted.</li>
<li><strong>Reply to request</strong> a 30-day extension (one extension allowed).</li>
<li><strong>Tag the API</strong> with <code>lifecycle: protected</code> to permanently exclude it from cleanup.</li>
</ol>

<p style="background-color: #fff3cd; padding: 10px; border-left: 4px solid #ff9800;">
<strong>⚠️ Important:</strong> If no response is received by <strong>2026-05-14</strong>, 
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
<p>Contact: <a href="mailto:noreply@company.com">noreply@company.com</a><br>
Reply to this email with <strong>"KEEP"</strong> if you need these APIs retained.</p>

<hr>
<p style="font-size: 12px; color: #666;">
This is an automated message from the AWS API Gateway Cleanup Service.<br>
Classification Date: 2026-04-30 08:15:00 UTC
</p>
</body>
</html>
```

---

## Key Features of Owner Notifications

### 1. Grouped by Owner
- All APIs owned by the same person are grouped in one email
- Owner email resolved from `owner` tag on the API
- Reduces email fatigue (one email per owner, not per API)

### 2. Clear Timeline
- **30-day deletion schedule** from classification date
- **14-day response deadline** for owner feedback
- Gives owners enough time to respond

### 3. Multiple Actions Available
Owners can:
- **Confirm deletion** - Reply to approve
- **Request extension** - Reply to get 30 more days (one-time)
- **Tag as protected** - Add `lifecycle: protected` tag to permanently exclude

### 4. Transparency
- Shows exactly which APIs are affected
- Explains why each API was classified
- Details the entire deletion process
- Promises another notification before final deletion

### 5. Both Text and HTML
- Text version for email clients that don't support HTML
- HTML version with formatted tables and color-coded warnings

---

## Notification Logic

### When Sent
- After Classifier Lambda completes classification
- Only for APIs classified as **DORMANT** or **ORPHANED**
- Only if the API has a valid `OwnerEmail` in the CSV

### Who Receives
- Email address from `owner` tag on the API
- If multiple APIs have the same owner, one combined email is sent

### Grouping
```python
# Pseudo-code
apis_by_owner = {}
for api in dormant_or_orphaned_apis:
    owner_email = api.get('OwnerEmail')
    if owner_email:
        if owner_email not in apis_by_owner:
            apis_by_owner[owner_email] = []
        apis_by_owner[owner_email].append(api)

# Send one email per owner
for owner_email, apis in apis_by_owner.items():
    send_email(owner_email, apis)
```

---

## Configuration

### Lambda Environment Variables

```bash
SES_SENDER_EMAIL=noreply@company.com
```

### SES Requirements
- Sender email must be verified in AWS SES
- If SES is in sandbox mode, recipient emails must also be verified
- Move SES to production mode to send to any email

---

## Testing

### Test Owner Notification
1. Run scanner Lambda with test account
2. Run classifier Lambda
3. Check that owner receives email
4. Verify email content matches template
5. Check grouping (multiple APIs → one email)

### Test Cases
- [ ] Single API, single owner
- [ ] Multiple APIs, single owner (grouped)
- [ ] Multiple APIs, multiple owners (separate emails)
- [ ] API with no owner tag (skipped)
- [ ] API with invalid email format (logged error)

---

## Troubleshooting

### Owner not receiving emails
1. Check if `owner` tag exists on API
2. Verify email format is valid
3. Check SES is not in sandbox (or recipient is verified)
4. Check CloudWatch logs for send errors
5. Check spam folder

### Email formatting issues
1. HTML version should work in most clients
2. Text version is fallback
3. Both versions sent simultaneously (multipart/alternative)

---

## Metrics to Track

After implementing owner notifications, track:
- **Owner response rate**: % of owners who respond to notification
- **Extension requests**: How many request 30-day extension
- **Protection tags added**: How many APIs tagged as protected
- **Email delivery rate**: % of emails successfully delivered
- **Bounce rate**: % of emails that bounced

---

**Last Updated**: April 30, 2026  
**Owner**: API Platform Team

# ✅ Owner Notification Feature - Implementation Complete

## Summary

Successfully added **automatic owner notifications** to the API Gateway cleanup workflow. API owners now receive individual emails when their APIs are classified as DORMANT or ORPHANED, giving them a 30-day warning before deletion.

---

## What Was Added

### 1. Lambda Classifier Updates

**File**: `lambdas/lambda_classifier.py`

#### New Function: `send_owner_notifications()`
- Groups APIs by owner email
- Sends one email per owner (not per API)
- Includes detailed table of all their unused APIs
- Provides 30-day deletion schedule
- 14-day response deadline
- Clear actions they can take

#### Enhanced Return Value
Lambda now returns:
```json
{
  "classified": 150,
  "counts": {"ACTIVE": 100, "LOW_TRAFFIC": 20, "DORMANT": 25, "ORPHANED": 5},
  "csv_location": "s3://bucket/classifications/...",
  "classification_date": "2026-04-30T08:15:00Z",
  "owner_notifications": {
    "sent": 15,
    "failed": 0,
    "total_owners": 15,
    "total_apis": 30
  }
}
```

### 2. Documentation Updates

Updated files:
- ✅ `aaguacv1.md` - Updated workflow diagram
- ✅ `README-CSV-WORKFLOW.md` - Added owner notification details
- ✅ `QUICKSTART-GUIDE.md` - Updated Step 1 with owner notification info
- ✅ `IMPLEMENTATION-SUMMARY.md` - Added feature to classifier section
- ✅ `COMPLETE.md` - Updated workflow and features

### 3. New Documentation

Created:
- ✅ `OWNER-NOTIFICATION-TEMPLATE.md` - Complete email template with examples

---

## Email Template Overview

### Subject Line
```
[ACTION REQUIRED] N Unused AWS API Gateway API(s) scheduled for deletion
```

### Email Content Includes

1. **List of unused APIs** owned by recipient
   - API Name, ID, Region
   - Tier (DORMANT/ORPHANED)
   - Last seen date
   - Classification reason

2. **Timeline**
   - Deletion scheduled: 30 days from classification
   - Response deadline: 14 days from classification

3. **Actions Available**
   - Confirm deletion (reply to approve)
   - Request 30-day extension (one-time only)
   - Tag API as protected (permanent exclusion)

4. **Next Steps**
   - Platform team review process
   - Backup → Soft delete → Hard delete workflow
   - Promise of another notification before final deletion

### Email Format
- Both **HTML** (with formatted tables) and **plain text** versions
- Color-coded warnings (yellow warning box)
- Professional and clear language
- Contact information for questions

---

## How It Works

### Notification Flow

```
Classifier Lambda completes classification
         │
         ▼
Filter APIs with Tier = DORMANT or ORPHANED
         │
         ▼
Filter APIs with valid OwnerEmail
         │
         ▼
Group APIs by owner email
         │
         ▼
For each owner:
  - Build list of their APIs
  - Generate email (HTML + text)
  - Send via SES
  - Log success/failure
         │
         ▼
Return notification statistics
```

### Grouping Logic

```python
# Example: 3 owners, 5 APIs
Owner A: [api-1, api-2, api-3]  → 1 email with 3 APIs
Owner B: [api-4]                → 1 email with 1 API
Owner C: [api-5]                → 1 email with 1 API

Total: 3 emails sent (not 5)
```

### Benefits
- **Reduces email fatigue** - One email per owner, not per API
- **Better context** - Owners see all their unused APIs together
- **Clear actions** - Owners know exactly what to do
- **Proactive engagement** - Owners notified before team review

---

## Configuration

### Environment Variables

Add to Classifier Lambda:
```bash
SES_SENDER_EMAIL=noreply@company.com
```

### SES Setup

1. **Verify sender email** in AWS SES:
   ```bash
   aws ses verify-email-identity --email-address noreply@company.com
   ```

2. **Move out of sandbox** (if needed):
   - Go to SES Console → Account Dashboard
   - Request production access
   - Or verify all recipient emails individually

3. **Test email sending**:
   ```bash
   aws ses send-email \
     --from noreply@company.com \
     --to test@company.com \
     --subject "Test" \
     --text "Test email"
   ```

---

## Testing Checklist

### Pre-Deployment Tests
- [ ] Deploy classifier Lambda with new code
- [ ] Set SES_SENDER_EMAIL environment variable
- [ ] Verify SES sender email
- [ ] Create test APIs with `owner` tags
- [ ] Run scanner Lambda
- [ ] Run classifier Lambda
- [ ] Check owner receives email
- [ ] Verify email content (HTML and text)
- [ ] Check CloudWatch logs for errors

### Test Scenarios
- [ ] Single owner, single API
- [ ] Single owner, multiple APIs (grouped correctly)
- [ ] Multiple owners, multiple APIs (separate emails)
- [ ] API with no owner tag (skipped, no email)
- [ ] API with invalid email format (logged error, skipped)
- [ ] API classified as ACTIVE (no email sent)
- [ ] API classified as DORMANT (email sent)
- [ ] API classified as ORPHANED (email sent)

### Verification
- [ ] Check Lambda return value has `owner_notifications` key
- [ ] Verify `sent` count matches expected
- [ ] Check no emails sent to ACTIVE/LOW_TRAFFIC APIs
- [ ] Confirm emails are grouped by owner
- [ ] Verify HTML rendering in email client
- [ ] Check text version displays correctly

---

## Timeline Example

**Day 0 (Monday 08:00 UTC)**: Scanner runs
- Collects all API data
- Generates scan CSV

**Day 0 (Monday 08:15 UTC)**: Classifier runs
- Classifies APIs into tiers
- Generates classification CSV
- **Sends team notification email**
- **Sends owner notification emails** ← NEW

**Day 0-7**: Owner response period (first 7 days)
- Owners can reply to confirm/extend/protect

**Day 7-14**: Team review period
- Platform team reviews CSV
- Fills approval columns
- Uploads approved CSV

**Day 14**: Response deadline
- If no owner response, proceed with team approval

**Day 30**: Scheduled deletion date
- Jenkins pipeline triggered
- Backup → Soft delete → Manual approval → Hard delete

---

## Owner Response Handling

### Current Implementation
- Owners receive email notification
- They can reply to the email
- **Manual process**: Platform team checks inbox and updates CSV

### Future Enhancement (Optional)
Could add:
- Auto-reply parsing (scan inbox for "KEEP", "DELETE", "EXTEND")
- Automated CSV updates based on replies
- Self-service web portal for owner responses
- Integration with ticketing system (Jira/ServiceNow)

---

## Metrics to Track

After deploying owner notifications:

1. **Email Delivery**
   - Total notifications sent
   - Failed sends (bounces, errors)
   - Delivery rate %

2. **Owner Engagement**
   - Response rate (% owners who reply)
   - Confirmation rate (% who approve deletion)
   - Extension requests (% who ask for more time)
   - Protection tags added (% who tag as protected)

3. **Impact on Process**
   - Reduction in surprise escalations
   - Faster approval times
   - Fewer APIs saved at last minute

4. **CloudWatch Metrics**
   - `OwnerNotificationsSent` - Custom metric
   - `OwnerNotificationsFailed` - Custom metric
   - Lambda duration impact

---

## Error Handling

### Email Send Failures

Classifier handles failures gracefully:
```python
try:
    ses.send_email(...)
    sent_count += 1
except Exception as e:
    logger.error(f"Failed to send to {owner_email}: {e}")
    failed_count += 1
    # Continue with other owners
```

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `EmailAddressNotVerified` | Sender email not verified | Verify in SES console |
| `MessageRejected` | Recipient email invalid | Check `owner` tag format |
| `MailFromDomainNotVerified` | Domain not verified | Verify domain in SES |
| `AccountSendingPaused` | SES in sandbox | Request production access |

### Logging

All actions logged to CloudWatch:
```
INFO: Sending owner notifications to 15 unique owners for 30 APIs
INFO: ✓ Sent owner notification to john.doe@company.com for 3 APIs
INFO: ✓ Sent owner notification to jane.smith@company.com for 2 APIs
ERROR: ✗ Failed to send owner notification to invalid-email: Invalid email format
INFO: Owner notifications sent: 14, failed: 1
```

---

## Security Considerations

### Email Content
- Does NOT include sensitive data (credentials, keys)
- Shows API IDs (public identifiers)
- Shows account IDs (acceptable for internal use)

### Access Control
- Only APIs with `owner` tag get notifications
- No PII beyond email addresses
- CloudWatch logs contain email addresses (retention policy applies)

### SES Security
- Use verified domain (not just email)
- Enable DKIM signing
- Configure SPF records
- Set up bounce and complaint handling

---

## Rollback Plan

If owner notifications cause issues:

1. **Disable notifications** without redeployment:
   ```python
   # In lambda_classifier.py, comment out:
   # owner_notifications = send_owner_notifications(classified_records)
   ```

2. **Or use environment variable** to toggle:
   ```python
   SEND_OWNER_NOTIFICATIONS = os.environ.get("SEND_OWNER_NOTIFICATIONS", "true") == "true"
   
   if SEND_OWNER_NOTIFICATIONS:
       owner_notifications = send_owner_notifications(classified_records)
   ```

3. **Redeploy** previous Lambda version:
   ```bash
   aws lambda update-function-code \
     --function-name api-classifier \
     --s3-bucket lambdas \
     --s3-key lambda_classifier_v1.0.zip
   ```

---

## Success Criteria

✅ Owner notifications successfully implemented when:
- Owners of DORMANT/ORPHANED APIs receive emails
- Emails are grouped by owner (one per person)
- Email content is clear and actionable
- No emails sent to ACTIVE/LOW_TRAFFIC APIs
- Team notification still sent separately
- Lambda completes without errors
- CloudWatch logs show send success

---

## Next Steps

1. ✅ Code implementation complete
2. ✅ Documentation updated
3. ⏳ Deploy to AWS Lambda
4. ⏳ Configure SES sender email
5. ⏳ Test with real APIs
6. ⏳ Monitor first week of notifications
7. ⏳ Collect owner feedback
8. ⏳ Iterate on email template if needed

---

**Implementation Date**: April 30, 2026  
**Status**: ✅ Complete - Ready for deployment  
**Feature**: Owner Notifications After Classification  
**Impact**: Proactive engagement with API owners before deletion

---

**Questions?** Contact:
- mdziaur.rahman@corebridgefinancial.com
- mdziaur.rahman@mphasis.com
- sust.cse.zia@gmail.com

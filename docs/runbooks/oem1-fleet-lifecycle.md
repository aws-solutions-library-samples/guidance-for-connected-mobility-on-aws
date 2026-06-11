# OEM1 Fleet Lifecycle Operator Runbook

This runbook covers seven common scenarios in OEM1 fleet enrollment, unenroll, and status synchronization operations. For each scenario, follow the Detection → Investigation → Remediation steps.

## Enroll batch stuck in IN_PROGRESS

### Detection

1. Open the Vehicles list in the CMS UI.
2. Filter by OEM1 source.
3. Observe one or more vehicles with `oem1_enrollment_status = 'IN_PROGRESS'` and `oem1_status_refreshed_at` timestamp older than 30 minutes.

### Investigation

1. Note the `oem1_request_id` from the vehicle detail view.
2. Query the `cms-{stage}-storage-oem1-enrollment-requests` DDB table:
   ```bash
   aws dynamodb get-item \
     --table-name cms-{stage}-storage-oem1-enrollment-requests-{region}-{account} \
     --key '{"request_id": {"N": "<oem1_request_id>"}}' \
     --region {region}
   ```
3. Check the `status_summary` field for the latest OEM1 fcs_code and message.
4. Check CloudWatch Logs for the enrollment-poller Lambda:
   ```bash
   aws logs tail /aws/lambda/cms-{stage}-oem1-poller-{region} \
     --since 2h \
     --follow \
     --filter-pattern '[actor, actor_email, action = "ENROLL", ...]'
   ```

### Remediation

- If `status_summary` shows a terminal fcs_code (0, 2, 3, 5, 6, 7, 1001, 1002, 1003, 8010, 8020, 9999, 8030, 8040), the enrollment has reached a terminal state per the Consumer Action policy (spec § 4.1). No further poller cycles will change the status.
- If `status_summary` is absent or shows a transient code (1, 429), the poller may still be in flight. Wait 2–5 minutes and re-check. The default poller cadence is every 1 minute (configurable via `cdk.json:context.oem1EnrollmentPollerCadenceMinutes`).
- If the row shows no `terminal_at` timestamp and the submission is >8 days old, the enrollment is considered permanently stuck and will be marked FAILED on the next poller cycle.

---

## OEM1 429 quota exceeded

### Detection

1. In the Vehicles list, attempt to enroll a fleet of vehicles.
2. On submission, the UI displays: "OEM1 hourly enroll quota exhausted; retry after HH:MM UTC."
3. The `oem1_enrollment_status` remains absent or `UNENROLLED` (no enrollment-requests row created).

### Investigation

1. Check the current quota consumption via the UI: navigate to Fleet Management and select the fleet. The quota counter displays "0 of 4 enroll requests remaining this hour."
2. Query the `cms-{stage}-storage-oem1-enrollment-requests` table to confirm the failed batch was NOT written:
   ```bash
   aws dynamodb query \
     --table-name cms-{stage}-storage-oem1-enrollment-requests-{region}-{account} \
     --index-name customer_id-submitted_at \
     --key-condition-expression 'customer_id = :{stage}-default AND submitted_at > :one_hour_ago' \
     --expression-attribute-values '{{":one_hour_ago": {"N": "{unix_timestamp_1h_ago}"}}}' \
     --region {region}
   ```
3. Identify the 4 successful enrollments in the last hour (the quota window is per-customer, per-hour, hardcoded to 4 requests/hour per OEM1 SLA).

### Remediation

- Wait until the next hour boundary. The quota resets automatically at the top of each UTC hour.
- If quota exhaustion is a recurring blocker, contact the OEM Business Support team to discuss quota increase options.
- Advise the user that OEM1's 4-request-per-hour limit is a hard constraint per the OEM1 SLA and cannot be overridden client-side.

---

## Unenroll didn't terminate after 7 days

### Detection

1. In the Vehicles list, view an OEM1 vehicle with `oem1_enrollment_status = 'UN_ENROLL_IN_PROGRESS'`.
2. The `oem1_status_refreshed_at` timestamp is older than 7 days.

### Investigation

1. Query the enrollment-requests table for the unenroll request:
   ```bash
   aws dynamodb query \
     --table-name cms-{stage}-storage-oem1-enrollment-requests-{region}-{account} \
     --key-condition-expression 'request_id = :<oem1_request_id>' \
     --region {region}
   ```
2. Check the `request_type` field — it should be `'UN_ENROLL'`.
3. Check the `status_summary` field for the latest fcs_code.
4. If `terminal_at` is absent, the unenroll request is still pending at OEM1 (likely a backend issue on OEM1's side).
5. Check CloudWatch Logs for the poller Lambda over the past 7 days:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/lambda/cms-{stage}-oem1-poller-{region} \
     --start-time $(date -d '7 days ago' +%s)000 \
     --filter-pattern 'UN_ENROLL' \
     --region {region}
   ```

### Remediation

- Contact the OEM Business Support team and provide the `oem1_request_id` from the enrollment-requests row. Include the vehicle VIN and the submission timestamp.
- The unenroll is stuck at OEM1's backend and requires manual intervention on their side.
- Once OEM1 resolves the backend issue and the poller detects the terminal status, the CMS vehicle row will be updated to `oem1_enrollment_status = 'UNENROLLED'` and `Inactive`.

---

## Status sync emits drift events

### Detection

1. View CloudWatch Metrics under the `cms/oem1/status_sync` namespace.
2. Observe a spike in the `drift_detected` metric or a non-zero value for `OEM1StatusDrift` EventBridge events in the past hour.
3. In the Vehicles list, spot-check an OEM1 vehicle and note any recent change in `oem1_enrollment_status` (e.g., from COMPLETED to UNENROLLED).

### Investigation

1. Query the vehicle detail view to confirm the current `oem1_enrollment_status` and `oem1_status_message`.
2. Check the enrollment-requests table to confirm the vehicle was previously enrolled with a terminal status:
   ```bash
   aws dynamodb query \
     --table-name cms-{stage}-storage-oem1-enrollment-requests-{region}-{account} \
     --index-name customer_id-submitted_at \
     --key-condition-expression 'customer_id = :{stage}-default' \
     --region {region} | jq '.Items[] | select(.request_type == "ENROLL" and .oem1_request_id | contains("<vin>"))'
   ```
3. Check CloudWatch Logs for the status-sync Lambda:
   ```bash
   aws logs tail /aws/lambda/cms-{stage}-oem1-status-sync-{region} \
     --since 1h \
     --filter-pattern 'OEM1StatusDrift'
   ```

### Remediation

- Drift is **expected behavior** when a vehicle's enrollment status changes at OEM1 (e.g., a manager unenrolls via OEM1 portal). The drift event documents the reconciliation.
- Review the `oem1_status_message` to understand the reason for the status change.
- If the drift is unexpected (e.g., vehicle still enrolled at OEM1 but status shows UNENROLLED in CMS), escalate to the OEM Business Support team with the vehicle VIN and `oem1_request_id`.
- Verify the vehicle was refreshed within the status-sync window (default 15 minutes, configurable via `cdk.json:context.oem1StatusSyncCadenceMinutes`).

---

## Config-SKU update needed

### Detection

1. Operators receive a request to update the catalog of eligible SKUs for OEM1 enrollments.
2. The current SKU list no longer reflects available vehicle trim options.

### Investigation

1. Confirm the new SKU list with the requestor (internal product team or external OEM1 contacts).
2. Verify the SKU is not already in `cdk.json:context.oem1ProductCatalog`.

### Remediation

1. Update `cdk.json` in the CMS CDK deployment directory with the new SKU:
   ```json
   {
     "context": {
       "oem1ProductCatalog": [
         "SKU-X",
         "SKU-Y",
         "SKU-Z-NEW"
       ]
     }
   }
   ```
2. Commit the change to the CMS repository.
3. Run `cdk deploy CmsConnectorStack` to propagate the new catalog to the enroll wizard UI via CDK context propagation.
4. Verify the new SKU appears in the "Select SKU" dropdown in the enroll wizard.

---

## Engineering tenant fleet rejected

### Detection

1. An operator attempts to enroll vehicles from an internal engineering-test fleet.
2. The bulk-enroll Lambda returns an HTTP 400 error with message: "Fleet is in the engineering-tenant exclusion list and cannot be enrolled in OEM1."

### Investigation

1. Confirm the fleet ID is listed in the SSM parameter `/cms/{stage}/engineering-fleet-ids`:
   ```bash
   aws ssm get-parameter \
     --name /cms/{stage}/engineering-fleet-ids \
     --region {region} \
     --query 'Parameter.Value'
   ```
2. The value is a comma-separated list of fleet IDs (or JSON array) that are reserved for internal use and excluded from OEM1 enrollment.

### Remediation

- Engineering tenant fleets cannot be enrolled in OEM1. This is by design per Phase 2 (C3) to prevent live customer subscriptions from unintended test activity.
- If the fleet should be production-eligible, it must be removed from the engineering-tenant exclusion list. Coordinate with the platform team to update the SSM parameter and redeploy the Lambda.
- For testing, create a separate non-engineering fleet (e.g., `eng-integration-test-fleet-01-PROD`-named with clear intent) or use the mock OEM1 server in the integration test suite.

---

## TC9999 / 8030 / 8040 surface-immediately failure

### Detection

1. In the Vehicles list, view an OEM1 vehicle with `oem1_enrollment_status = 'FAILED'` and `oem1_fcs_code` equal to 9999, 8030, or 8040.
2. The `oem1_status_message` displays one of:
   - "Please retry the request" (TC9999)
   - "VIN not in OEM1 ecosystem" (TC8030)
   - "Capability check service unavailable" (TC8040)

### Investigation

1. Query the enrollment-requests table for the failed request:
   ```bash
   aws dynamodb query \
     --table-name cms-{stage}-storage-oem1-enrollment-requests-{region}-{account} \
     --key-condition-expression 'request_id = :<oem1_request_id>' \
     --region {region}
   ```
2. Record the `oem1_request_id` and `submitted_by` (the admin user who submitted the enroll).
3. Per spec § 4.3 (OQ16 policy), these three failure codes surface immediately on the first poll cycle and do NOT trigger automatic retry.

### Remediation

- **Do NOT** click "Retry failed VINs" in the UI expecting automatic retry to succeed. The system surfaces these codes immediately because they indicate a fundamental issue (VIN not in ecosystem, transient OEM1 service issue) that cannot be resolved by simple retry.
- Contact the OEM Business Support team with the following information:
  - `oem1_request_id` (from the enrollment-requests row)
  - Vehicle VIN
  - Failure code (9999, 8030, or 8040)
  - Timestamp of the failed enrollment
  - Submitted by: `<submitted_by>` user email
- Provide the Support team with the failure code and allow them to investigate on OEM1's backend.
- For TC8030 (VIN not in OEM1 ecosystem), verify the VIN is correct and is an enrolled vehicle in OEM1's system. If not, that vehicle cannot be managed via OEM1 and should remain CMS-native.
- For TC9999 or TC8040, coordinate with OEM1 support to determine when the issue is resolved, then manually retry the enrollment from the UI or via the bulk-enroll API.

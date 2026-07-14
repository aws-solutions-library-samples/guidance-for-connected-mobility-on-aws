# OEM1 Add-Vehicle Operator Runbook

## Prerequisites

Before attempting to add OEM1 vehicles through the UI, verify:

1. **SSM Parameter `/cms/{stage}/engineering-fleet-ids` exists** — this StringList parameter defines which fleet IDs belong to the Engineering tenant. It must be present before the Lambda initializes. If absent, the Lambda logs a CloudWatch warning and assumes no Engineering-tenant fleets exist (fail-open behavior).

2. **Environment variable `CMS_USER_POOL_ID` is set during Lambda deployment** — the OEM1 add-vehicle route is only created when this env var is present. First-time deployment without this env var will omit the route; after the UI stack lands and the User Pool ID is known, the connector stack must be re-deployed with `CMS_USER_POOL_ID=<pool-id>` set.

## Troubleshooting: "OEM1 Vehicle Won't Add Through the UI"

### Symptom: Response is `PENDING`

**Meaning**: OEM1 has not yet marked the VIN as enrolled (it may be in backlog, under review, or delayed).

**Expected behavior**: The vehicle row is written to DynamoDB with `enrollment_pending: true` and no `enrolled_at` field. The UI displays "Enrollment pending". The next run of `make seed-vehicles-oem1` (typically once per day in staging) will reconcile the status when OEM1 completes enrollment.

**Action**: Wait for the next sync or manually run the seed command:
```bash
make seed-vehicles-oem1 AWS_PROFILE=<profile> DEPLOYMENT_STAGE=<stage>
```

### Symptom: Response is `UNKNOWN`

**Meaning**: The VIN was not found in the first 500 vehicles returned by OEM1's `/enrollment/v2/status/latest` endpoint.

**Why this happens**: The UI flow queries only the first 5 pages of OEM1's enrollment list (500 vehicles). If the VIN is enrolled but is the 501st or later in the result order, it is not visible to this single-VIN add flow.

**Action**: Use the bulk enrollment CLI instead:
```bash
make seed-vehicles-oem1 AWS_PROFILE=<profile> DEPLOYMENT_STAGE=<stage>
```
This fetches all enrolled VINs from OEM1 and synchronizes them to the vehicles table. After the bulk sync completes, you can verify the vehicle was added by querying the DynamoDB table or refreshing the vehicle list in the UI.

### Symptom: Response is 403 with error "oem1 add-vehicle requires platform admin"

**Meaning**: The user's Cognito ID token does not include the `platform-admin` group claim.

**Why this happens**: The OEM1 add-vehicle route requires the Cognito User Pool `platform-admin` group membership. Only users assigned to this group can enroll new OEM1 vehicles.

**Action**: 
1. Verify the user is in the `platform-admin` Cognito group:
   ```bash
   aws cognito-idp get-group --group-name platform-admin \
     --user-pool-id <USER_POOL_ID> --region <REGION>
   ```
2. If the user is not listed, add them:
   ```bash
   aws cognito-idp admin-add-user-to-group \
     --user-pool-id <USER_POOL_ID> --username <USER_EMAIL> \
     --group-name platform-admin --region <REGION>
   ```
3. Ask the user to sign out and sign back in to refresh their ID token.

### Symptom: OEM1 card is disabled in the source-picker with tooltip "OEM1 vehicles are not available in the Engineering tenant"

**Meaning**: The active tenant is the Engineering tenant, which does not support OEM1 vehicles.

**Why this happens**: OEM1 vehicles and Engineering-tenant fleets are mutually exclusive. All OEM1 vehicles must belong to non-Engineering fleets.

**Action**: Switch to a different tenant before adding an OEM1 vehicle:
1. In the CMS UI, locate the tenant selector (usually at the top or in account settings)
2. Select a non-Engineering tenant (e.g., "Default" or "Production")
3. The OEM1 card in the source-picker will become enabled
4. Complete the add-vehicle flow

If only the Engineering tenant is available, contact an administrator to create or grant access to a non-Engineering tenant.

## Authentication & Authorization Details

### Cognito User Pool Authorizer

The OEM1 add-vehicle route uses **Cognito User Pool authorization** (not IAM).

**How it works**:
- Client sends: `Authorization: Bearer <idToken>` (via the standard `authFetch` utility)
- API Gateway's Cognito User Pool authorizer validates the JWT signature and extracts claims
- Lambda receives the claims in `event.requestContext.authorizer.claims['cognito:groups']`
- Lambda rejects the request with 403 if `platform-admin` is missing

**Cognito vs IAM**: Note that the existing `/admin/oem1/vehicle-state/{vehicleId}` route (for OEM1 readiness checks) uses IAM auth, while the new `/admin/oem1/add-vehicle` route uses Cognito User Pool auth. This inconsistency is intentional and time-boxed. A future admin API auth-mode unification initiative (P3 backlog row) will converge both routes to a single auth pattern.

### Bearer Token Sourcing

The frontend uses the project-standard `authFetch` utility (not Amplify v6 direct). This utility reads the Cognito User Pool ID token from the local session and automatically includes it in all API calls:

```
Authorization: Bearer <idToken>
```

No additional client-side configuration is required; the session is managed by the existing Amplify context (`AmplifyAuthProvider.tsx`).

## Field Shape & Naming

The Lambda writes vehicle rows using the same DynamoDB shape as `seed_vehicles.py:_write_vehicle`. Fields use **snake_case**:

- `oem_source: "oem1"`
- `last_seen_at: "2026-06-04T11:47:50Z"` (ISO8601 UTC)
- `enrolled_at: "2026-06-04T11:47:50Z"` (absent for PENDING)
- `enrollment_pending: true` (BOOLEAN, only for PENDING status; omitted for COMPLETED)

API field normalization (camelCase ↔ snake_case cleanup) is **out of scope** for this initiative and is tracked as a separate P3 hygiene row (`API field normalization`). When reading or writing OEM1 vehicle data, use the snake_case names as shown above.

## Common Scenarios

### Bulk-adding 100 VINs

Use the CLI instead of the UI:
```bash
# Bulk sync from OEM1 (enrolls all VINs in one batch)
make seed-vehicles-oem1 AWS_PROFILE=prod DEPLOYMENT_STAGE=prod
```

The UI single-VIN flow (with its 500-vehicle pagination cap) is not suitable for bulk operations. See [OEM1 add-vehicle architecture](../architecture/oem1-add-vehicle.md) for details on the cap.

### Checking OEM1 Sync Status

View the most recent sync job:
```bash
# View seed Lambda logs (if running on-demand)
aws logs tail /aws/lambda/cms-{stage}-seed-oem1-vehicles --follow --since 1h

# Or check the DynamoDB table for `last_seen_at` freshness
aws dynamodb query --table-name cms-{stage}-storage-vehicles \
  --key-condition-expression "oem_source = :src" \
  --expression-attribute-values "{\":src\":{\"S\":\"oem1\"}}" \
  --max-items 10
```

### VIN Format

OEM1 vehicle IDs follow the pattern: `oem1#{vin}` where `{vin}` is the 17-character Vehicle Identification Number provided by the user in the UI.

Example: `oem1#WF0LXXGC06G102567`

## Logs & Monitoring

**Lambda logs** (authorization failures, OEM1 API errors):
```bash
aws logs tail /aws/lambda/cms-{stage}-oem1-admin-add-vehicle --follow
```

**Look for**:
- `403 Forbidden` entries → missing `platform-admin` group claim
- `OEM1 vehicles are not available in the Engineering tenant` → fleet rejection
- `VIN not found in first 500 enrollments` → pagination cap hit
- `PENDING` → enrollment in progress; reconcile on next seed run

**API calls to OEM1**:
```bash
# X-Ray tracing (if enabled)
aws xray get-service-graph --start-time "$(date -u -d '1 hour ago' +%s)" --end-time "$(date -u +%s)"
```

## See Also

- [OEM1 add-vehicle architecture](../architecture/oem1-add-vehicle.md)
- [Bulk OEM1 enrollment](../../services/connectors/oem1/seed_vehicles.py)
- [Engineering-fleet configuration](../../scripts/seed_engineering_fleets.py)

# API Gateway Authorizer Audit

**Date**: 2026-06-11  
**Scope**: `deployment/stacks/`, `services/`, `modules/`  
**Spec**: `.kiro/specs/2026-06-11-cms-api-authorizer-template-fix/`  
**Verify count**: `grep -rE 'apigateway\.RestApi|RestApi\(' deployment/stacks/ | wc -l` → **7**

## Summary

6 `RestApi` / `LambdaRestApi` constructs found across 5 stack files. No API Gateway constructs found in `services/` or `modules/` (excluding frontend `node_modules/`).

- **3 fully authorized gateways** — all `add_method` calls carry explicit `authorization_type`
- **3 unauth gateways** — all `add_method` calls carry no authorizer (CDK default: `AuthorizationType.NONE`)
- **No 7th unauth gateway found** — the set of unprotected gateways is exactly {simulation, commands, predictive-agent}

---

## Inventory

| Stack file | RestApi construct ID | Route count | Authorized count | Unauth count | Intended state |
|---|---|---|---|---|---|
| `deployment/stacks/ui_stack.py` | `SimulationAPI` *(wrong — see note)* → `"MainFleetAPI"` (logical) | 21 | 21 | 0 | **COGNITO** — user-facing `/api/*` fleet manager |
| `deployment/stacks/simulation_stack.py` | `SimulationAPI` | 14 | 0 | 14 | **COGNITO** — user-facing `/api/simulation/*`; H1 3775026 finding |
| `deployment/stacks/commands_stack.py` | `CommandsAPI` | 6 | 0 | 6 | **COGNITO** — user-facing `/api/commands/*`, `/api/geofences/*`; H1 3775026 finding |
| `deployment/stacks/predictive_agent_stack.py` | `PredictiveAgentAPI` | 5 | 0 | 5 | **COGNITO** — user-facing `/vehicles/*`, `/fleet/*`, `/maintenance/*`; H1 3775026 finding |
| `deployment/stacks/connector_stack.py` | `AdminApi` | 9 (conditional) | 9 | 0 | **AWS_IAM** (1 route: `/admin/oem1/vehicle-state/{vehicleId}`) + **COGNITO** (8 routes: conditional on `CMS_USER_POOL_ID`) — internal-service admin API |
| `deployment/stacks/data_processing_stack.py` | `DataProcessingRestAPI` (LambdaRestApi) | proxy (all routes) | conditional | conditional | **COGNITO** when `CMS_USER_POOL_ID` set; **NONE** on first-deploy before UI stack (documented in stack, acceptable bootstrap gap) |

---

## Per-Gateway Detail

### 1. `ui_stack.py` — `RestApi` construct `"MainFleetAPI"` (id in code: `apigateway.RestApi(self, ...)` at line 926)

**Construct instantiation**: line 926  
**Authorizer**: `CognitoUserPoolsAuthorizer` created at line 954, wired on every method  
**Route count**: 21 explicit `add_method` calls (lines 981–1034)  
**Authorized count**: 21 — all carry `authorizer=cognito_authorizer, authorization_type=apigateway.AuthorizationType.COGNITO`  
**Unauth count**: 0  
**CORS preflights**: handled by `default_cors_preflight_options` (auto-generated OPTIONS methods, unauthenticated — correct and expected)  
**Status**: ✅ COMPLIANT

Routes:
- `GET /api/realtime/vehicles`
- `GET /api/realtime/trips`
- `GET /api/iot-endpoint`
- `GET /api/fleets`, `POST /api/fleets`
- `GET /api/fleets/{fleetId}`
- `GET /api/fleets/{fleetId}/vehicles`
- `GET /api/vehicles`, `POST /api/vehicles`
- `GET /api/vehicles/locations`
- `GET /api/vehicles/{vehicleId}`
- `GET /api/vehicles/{vehicleId}/trips`
- `GET /api/vehicles/{vehicleId}/trips/{tripId}`
- `GET /api/vehicles/{vehicleId}/safety`
- `GET /api/vehicles/{vehicleId}/maintenance`
- `GET /api/trips`
- `GET /api/alerts/safety`
- `GET /api/alerts/maintenance`
- `GET /api/dashboard/metrics`
- `GET /api/dashboard/comparison`
- `ANY /api/{proxy+}` (admin bulk routes)

---

### 2. `simulation_stack.py` — `RestApi` construct `"SimulationAPI"`

**Construct instantiation**: line 462  
**Authorizer**: None — no `CognitoUserPoolsAuthorizer` created or wired  
**Route count**: 14 explicit `add_method` calls (lines 476–517)  
**Authorized count**: 0  
**Unauth count**: 14 — all `add_method(method, integration)` with no auth params; CDK default is `AuthorizationType.NONE`  
**Status**: ❌ NON-COMPLIANT — H1 3775026 finding, requires Pattern A fix

Unauth routes:
- `GET /api/simulation` (list/health)
- `POST /api/simulation/start`
- `GET /api/simulation/status/{simulationId}`
- `POST /api/simulation/stop/{simulationId}`
- `GET /api/simulation/list`
- `GET /api/simulation/health`
- `GET /api/simulation/drivers`
- `GET /api/simulation/presets`
- `GET /api/simulation/campaigns`
- `GET /api/simulation/discover-iot-endpoint`
- `POST /api/simulation/agent/start`
- `POST /api/simulation/agent/stop`
- `GET /api/simulation/agent/status`
- `GET /api/simulation/agent/logs/{vin}`

---

### 3. `commands_stack.py` — `RestApi` construct `"CommandsAPI"`

**Construct instantiation**: line 127  
**Authorizer**: None — no `CognitoUserPoolsAuthorizer` created or wired  
**Route count**: 6 explicit `add_method` calls (lines 142–154)  
**Authorized count**: 0  
**Unauth count**: 6 — all `add_method(method, integration)` with no auth params; CDK default is `AuthorizationType.NONE`  
**Status**: ❌ NON-COMPLIANT — H1 3775026 finding, requires Pattern A fix

Unauth routes:
- `GET /api/commands/catalog`
- `POST /api/commands/{vehicleId}` (send command)
- `GET /api/commands/{vehicleId}` (get history)
- `POST /api/geofences` (create geofence)
- `GET /api/geofences/{vehicleId}` (list geofences)
- `DELETE /api/geofences/{vehicleId}` (delete geofence)

---

### 4. `predictive_agent_stack.py` — `RestApi` construct `"PredictiveAgentAPI"`

**Construct instantiation**: line 165  
**Authorizer**: None — no `CognitoUserPoolsAuthorizer` created or wired  
**Route count**: 5 explicit `add_method` calls (lines 190–218)  
**Authorized count**: 0  
**Unauth count**: 5 — all `add_method(method, integration)` with no auth params; CDK default is `AuthorizationType.NONE`  
**Status**: ❌ NON-COMPLIANT — H1 3775026 finding, requires Pattern A fix

Unauth routes:
- `POST /vehicles/{vehicle_id}/analysis`
- `GET /vehicles/{vehicle_id}/analysis`
- `POST /fleet/analysis`
- `POST /maintenance/schedule`
- `GET /maintenance/schedule`

---

### 5. `connector_stack.py` — `RestApi` construct `"AdminApi"`

**Construct instantiation**: line 516  
**Authorizer**: Mixed — depends on route  
**Route count**: 1 unconditional + 8 conditional on `CMS_USER_POOL_ID` env var = 9 total  
**Authorized count**: 9 (all routes carry explicit auth)  
**Unauth count**: 0  
**Status**: ✅ COMPLIANT

Route detail:
- `POST /admin/oem1/vehicle-state/{vehicleId}` — `AuthorizationType.IAM` (line 528) — internal-service, machine-to-machine; correct for connector→admin proxy pattern
- `POST /admin/oem1/add-vehicle` — `AuthorizationType.COGNITO` (line 639) — conditional on `CMS_USER_POOL_ID`
- `GET /admin/oem1/enroll-quota` — `AuthorizationType.COGNITO` (line 898) — conditional
- `POST /admin/oem1/preflight` — `AuthorizationType.COGNITO` (line 907) — conditional
- `POST /admin/oem1/refresh-status` — `AuthorizationType.COGNITO` (line 916) — conditional
- `GET /admin/oem1/list-enrolled` — `AuthorizationType.COGNITO` (line 973) — conditional
- `POST /admin/oem1/command` — `AuthorizationType.COGNITO` (line 1010) — conditional
- `POST /admin/oem1/bulk-enroll` — `AuthorizationType.COGNITO` (line 1153) — conditional
- `POST /admin/oem1/bulk-unenroll` — `AuthorizationType.COGNITO` (line 1162) — conditional

**Note**: The `CMS_USER_POOL_ID`-gated routes are absent on first-deploy (before ui_stack creates the pool). This is the documented bootstrap pattern in the connector stack (`# Conditional on CMS_USER_POOL_ID per the bootstrap pattern`). The Makefile re-runs the stack after ui_stack to flip the routes on. Operationally acceptable — the admin routes are not user-facing on first-deploy.

**Note**: The one IAM-auth route (`/admin/oem1/vehicle-state/{vehicleId}`) is an internal-service endpoint called by the OEM1 connector Lambda, not browser traffic. IAM auth is the correct authorizer here. A P3 backlog row (`AdminApi auth-mode unification`) tracks eventual unification.

---

### 6. `data_processing_stack.py` — `LambdaRestApi` construct `"DataProcessingRestAPI"`

**Construct instantiation**: line 299 (`apigw.LambdaRestApi`)  
**Authorizer**: `CognitoUserPoolsAuthorizer` (line 289) — wired via `default_method_options` (line 293–296) passed to `LambdaRestApi` as `default_method_options=`  
**Route count**: All routes via Lambda proxy (`proxy=True`) — no explicit `add_method` calls  
**Authorized count**: All routes — `default_method_options` applies `COGNITO` auth to every method when `CMS_USER_POOL_ID` is set  
**Unauth count**: 0 when `CMS_USER_POOL_ID` is set; all routes when not set (first-deploy bootstrap gap, identical to connector_stack pattern)  
**Status**: ✅ COMPLIANT (conditional auth is the intentional bootstrap pattern; `CfnOutput` at line 314 exposes the auth mode for operator visibility)

---

## Services / Modules

No `apigateway.RestApi` or `add_method` calls found in:
- `services/simulation/`, `services/commands/`, `services/data_processing/`, `services/connectors/`, `services/vfo-pipeline/`, `services/trip-sweeper/`, `services/recall-integration/`, `services/ws-fanout/`, `services/cost_api/`, `services/websocket/`
- `modules/campaign_manager/`, `modules/predictive_agent/`, `modules/flink/`, `modules/cms_ui/`, `modules/oem_ingestion/`

These service directories contain Lambda handler code; the API Gateway resource definitions all live in `deployment/stacks/`.

---

## Findings Summary

| Finding | Count |
|---|---|
| Unauth gateways | 3 (simulation, commands, predictive-agent) |
| Unauth routes total | 25 (14 + 6 + 5) |
| Compliant gateways | 3 (ui, connector, data-processing) |
| 7th unauth gateway? | **NO — confirmed** |

The 25 unauth routes span exactly the 3 known stacks identified in the H1 report. No additional unprotected API Gateway construct exists in the codebase.

---

## Verify Command

```bash
grep -rE 'apigateway\.RestApi|RestApi\(' deployment/stacks/ | wc -l
# Expected: 7
# (simulation_stack.py:1, ui_stack.py:1, predictive_agent_stack.py:2 [method def + call],
#  commands_stack.py:1, data_processing_stack.py:1 [LambdaRestApi], connector_stack.py:1)
```

The count of 7 matches this document: 6 distinct RestApi instantiations, plus 1 method definition signature in `predictive_agent_stack.py` (`def _create_api_gateway(self) -> apigateway.RestApi:`).

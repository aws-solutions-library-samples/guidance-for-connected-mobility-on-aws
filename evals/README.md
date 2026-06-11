# CMS Eval Pipeline

## Scope (Spec 1: CMS production-ready foundation)

This spec ports **only Tier 3** (REST + WebSocket integration tests against deployed CMS endpoints). Tier 1 (lambda handler unit tests) and Tier 2 (workflow integration) are deferred to Spec 2 (CMS observability + broader tests).

## Why only Tier 3?

CMS's surface is a deployed REST + WebSocket API plus a CMS UI bundle. There is no Bedrock voice agent like CVX has, so CVX's Tier 1 (tool unit) and Tier 2 (mocked-Nova conversation) don't directly apply. Tier 3 is the highest-leverage tier for CMS — it validates the deployed system actually works end-to-end against real AWS resources.

## Folder layout

```
evals/
  README.md              # this file
  runner/
    __init__.py
    schema.py            # Pydantic v2 schema for case YAML files (CVX + CMS extensions)
    reporter.py          # regression detector + Markdown report builder
    _run_tier.py         # pytest wrapper that emits JSON report (Tier 3 only in Spec 1)
    tier3_e2e.py         # CMS Tier 3 runner: REST + WebSocket (added in task 2b)
    test_reporter.py     # unit tests for reporter.py
  cases/
    e2e/                 # Tier 3 YAML cases (added in task 2c)
      *.yaml
  baselines/
    README.md            # how to regenerate baselines
    tier3.json           # committed reference baseline (populated in Group 5)
  conftest.py            # pytest fixtures: cms_jwt, stage_endpoint (added in task 2b)
```

## Running

```bash
# Run Tier 3 against a deployed staging endpoint:
STAGE_ENDPOINT=https://<api-id>.execute-api.us-west-2.amazonaws.com/prod \
  make eval-tier3

# Update the committed baseline after a deliberate change:
make eval-update-baseline TIER=3
```

`make eval-tier3` and `make eval-update-baseline` are added to the Makefile in Group 4/5.
Without `STAGE_ENDPOINT` set, all Tier 3 tests auto-skip (safe for PR CI).

## Adding a case

Create a YAML file under `evals/cases/e2e/`. REST example:

```yaml
id: vehicles-list-fleet-001
description: "List vehicles for a fleet — 200 with paginated payload"
tier: 3
persona: fleet-operator
input:
  type: rest
  method: GET
  path: /api/v1/fleets/{fleet_id}/vehicles
  path_params:
    fleet_id: demo-fleet-001
  query_params:
    limit: 25
expected:
  status_code: 200
  response:
    must_contain_keys: [vehicles, pagination]
  latency_budget_ms: 2000
```

WebSocket example:

```yaml
id: vehicle-live-state-stream-001
description: "Subscribe to vehicle live state; receive at least one telemetry frame"
tier: 3
persona: fleet-operator
input:
  type: websocket
  path: /ws/vehicle-live-state
  subscribe:
    vehicle_id: VEH-0025
  duration_ms: 10000
expected:
  events:
    min_count: 1
    must_contain_one_of_types: [telemetry, status]
  latency_budget_ms: 12000
```

See `runner/schema.py` for the full field reference.

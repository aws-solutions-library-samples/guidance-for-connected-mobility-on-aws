# TCO Optimization — Cost API Service

> **Status:** Wireframe / Stubbed — no implementations yet.

## Overview

Fleet cost intelligence service for the Connected Mobility Solution. Provides REST API endpoints for cost data queries, CSV upload processing, and agentic cost optimization (monitor → diagnose → recommend → learn pipeline).

## Architecture

See the full design doc: [DESIGN.md](../../docs/tco-design.md)

```
services/cost_api/
├── index.py                  # Lambda handler — REST API routes
├── glue_etl.py              # Glue ETL job — CSV → DynamoDB + Iceberg
├── agents/
│   ├── monitor_agent.py     # Continuous cost anomaly detection
│   ├── diagnose_agent.py    # Root cause analysis (Bedrock)
│   ├── recommend_agent.py   # Actionable recommendations (Bedrock)
│   ├── learn_agent.py       # Outcome tracking + threshold tuning
│   └── lifecycle_agent.py   # Buy/sell/hold optimization
└── README.md
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/fleets/{fleetId}/costs` | Fleet cost summary |
| `GET` | `/api/v1/fleets/{fleetId}/costs/trend` | Monthly cost trend |
| `GET` | `/api/v1/fleets/{fleetId}/costs/outliers` | Cost outlier vehicles |
| `GET` | `/api/v1/fleets/{fleetId}/costs/comparison` | Fleet comparison |
| `GET` | `/api/v1/vehicles/{vehicleId}/costs` | Vehicle cost summary |
| `GET` | `/api/v1/vehicles/{vehicleId}/costs/history` | Vehicle cost history |
| `POST` | `/api/v1/costs/upload` | CSV upload |
| `GET` | `/api/v1/fleets/{fleetId}/costs/recommendations` | Agent recommendations |
| `POST` | `/api/v1/costs/recommendations/{id}/approve` | Approve recommendation |
| `POST` | `/api/v1/costs/recommendations/{id}/reject` | Reject recommendation |

## Deployment

```bash
DEPLOY_TCO=true cdk deploy cms-dev-tco
```

## Phased Implementation

- **Phase 1:** REST API + CSV upload + static cost alerts + dashboard
- **Phase 2:** ML anomaly detection + Diagnose/Recommend agents + forecasting
- **Phase 3:** Learn agent + Lifecycle agent + auto-approval + NL queries

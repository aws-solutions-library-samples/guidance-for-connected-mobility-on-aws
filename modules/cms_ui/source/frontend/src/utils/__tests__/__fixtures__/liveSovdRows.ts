// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Real commands-API rows, captured 2026-09-25 from staging
// `cms-staging-storage-commands` for the synthetic vehicle VEH-MRDN-0001 and
// converted the way the API serialises them (DynamoDB item -> JSON). Only
// `attestation`, `ttl` and `response.storage_uri` were dropped, and one
// `issuedBy` was replaced with an example.com address.
//
// Use these instead of hand-written rows: the Diagnostics tab's first UAT
// failed because hand-written fixtures used field names (`sessionId`,
// `submittedAt`, a top-level results map) that no live row has.
// Issue: issues/2026-09-25-diagnostics-ia-uat-defects/.

type LiveRow = Record<string, unknown>;

export const LIVE_SOVD_ROWS: { scan: LiveRow; sessionRoutine: LiveRow; unsessionedRoutine: LiveRow } = {
  "scan": {
    "topic": "cms/commands/things/MRDN0000000000001/executions/f3d321beb98c480fa692517f37a334f1/sovd/request",
    "response": {
      "status": "SUCCEEDED",
      "latency_ms": 27026,
      "topic": "cms/commands/things/MRDN0000000000001/executions/f3d321beb98c480fa692517f37a334f1/sovd/response",
      "components": {
        "ECU_PCM": {
          "protocol": "ISO 15765-4",
          "dtcs": [],
          "id": "ECU_PCM"
        },
        "ECU_BATTERY_12V": {
          "dtcs": [],
          "protocol": "ISO 15765-4",
          "id": "ECU_BATTERY_12V"
        },
        "ECU_BATTERY_HV": {
          "protocol": "ISO 15765-4",
          "dtcs": [],
          "id": "ECU_BATTERY_HV"
        },
        "ECU_BRAKE": {
          "id": "ECU_BRAKE",
          "protocol": "ISO 15765-4",
          "dtcs": []
        },
        "ECU_POWERTRAIN": {
          "protocol": "ISO 15765-4",
          "id": "ECU_POWERTRAIN",
          "dtcs": []
        },
        "ECU_EVAP": {
          "id": "ECU_EVAP",
          "protocol": "ISO 15765-4",
          "dtcs": []
        },
        "ECU_BODY": {
          "id": "ECU_BODY",
          "protocol": "ISO 15765-4",
          "dtcs": []
        },
        "ECU_COMM": {
          "protocol": "ISO 15765-4",
          "dtcs": [],
          "id": "ECU_COMM"
        },
        "ECU_ENGINE": {
          "protocol": "ISO 15765-4",
          "dtcs": [],
          "id": "ECU_ENGINE"
        }
      },
      "correlation_id": "f3d321beb98c480fa692517f37a334f1",
      "command_type": "read_dtcs"
    },
    "progress": {
      "ecu_index": 8,
      "ecu_status": "ok",
      "ecu_name": "ECU_BODY",
      "ecu_total": 9
    },
    "issuedBy": "sovd.verify.agent@example.com",
    "vehicleId": "VEH-MRDN-0001",
    "timestamp": 1790205429354,
    "latencyMs": 27173,
    "commandType": "read_dtcs",
    "type": "sovd",
    "commandId": "f3d321beb98c480fa692517f37a334f1",
    "updatedAt": 1790205456527,
    "respondedAt": "2026-09-23T23:17:36.527291+00:00",
    "components": [
      "*"
    ],
    "includeFreezeFrame": true,
    "issuedAt": "2026-09-23T23:17:09.354339+00:00",
    "correlationId": "f3d321beb98c480fa692517f37a334f1",
    "status": "SUCCEEDED"
  },
  "sessionRoutine": {
    "updatedAt": 1790202968201,
    "response": {
      "latency_ms": 7,
      "verdict": "out_of_spec",
      "components": {
        "status": "SUCCEEDED",
        "correlation_id": "6f3f4a4ce4bc441085fe70caea7b3d31",
        "routine_id": "lamp_self_check"
      },
      "correlation_id": "6f3f4a4ce4bc441085fe70caea7b3d31",
      "status": "SUCCEEDED",
      "result": {
        "ambient_lux": 6322
      },
      "topic": "cms/commands/things/MRDN0000000000001/executions/6f3f4a4ce4bc441085fe70caea7b3d31/sovd/response",
      "command_type": "run_routine"
    },
    "commandType": "run_routine",
    "vehicleId": "VEH-MRDN-0001",
    "latencyMs": 1097,
    "type": "sovd",
    "correlationId": "6f3f4a4ce4bc441085fe70caea7b3d31",
    "respondedAt": "2026-09-23T22:36:08.201703+00:00",
    "topic": "cms/commands/things/MRDN0000000000001/executions/6f3f4a4ce4bc441085fe70caea7b3d31/sovd/request",
    "issuedAt": "2026-09-23T22:36:07.104947+00:00",
    "timestamp": 1790202967104,
    "safetyClass": "INERT",
    "components": null,
    "commandId": "6f3f4a4ce4bc441085fe70caea7b3d31",
    "session_id": "2cf1b8de-9df7-467d-a0d8-e2c867e3d935",
    "status": "SUCCEEDED",
    "issuedBy": "sovd.smoke.agent@example.com",
    "routineId": "lamp_self_check"
  },
  "unsessionedRoutine": {
    "updatedAt": 1790083109860,
    "correlationId": "280bebfa365b47e9a83ede3e8ba9f8cb",
    "issuedBy": "operator@example.com",
    "latencyMs": 164,
    "issuedAt": "2026-09-22T13:18:29.696389+00:00",
    "commandType": "run_routine",
    "commandId": "280bebfa365b47e9a83ede3e8ba9f8cb",
    "vehicleId": "VEH-MRDN-0001",
    "response": {
      "status": "RATE_LIMITED",
      "correlation_id": "280bebfa365b47e9a83ede3e8ba9f8cb",
      "topic": "cms/commands/things/MRDN0000000000001/executions/280bebfa365b47e9a83ede3e8ba9f8cb/sovd/response",
      "latency_ms": 0,
      "components": {},
      "retry_after_ms": 646
    },
    "topic": "cms/commands/things/MRDN0000000000001/executions/280bebfa365b47e9a83ede3e8ba9f8cb/sovd/request",
    "type": "sovd",
    "status": "RATE_LIMITED",
    "safetyClass": "INERT",
    "components": null,
    "respondedAt": "2026-09-22T13:18:29.860327+00:00",
    "timestamp": 1790083109696,
    "routineId": "lamp_self_check"
  }
};

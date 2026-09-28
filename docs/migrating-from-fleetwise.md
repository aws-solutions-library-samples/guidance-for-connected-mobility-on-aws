# Migrating from AWS IoT FleetWise

AWS has announced that
[**AWS IoT FleetWise** is no longer open to new customers](https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/iotfleetwise-availability-change.html)
as of April 30, 2026, and AWS's own guidance names this repository as the path forward.
From the official [AWS IoT FleetWise availability change][availability-change] page:

> The [Guidance for Connected Mobility on AWS][guidance-page] provides guidance on how
> to develop and deploy modular services for connected mobility solutions that can be
> used to achieve equivalent capabilities as AWS IoT FleetWise.

The same statement appears on the AWS IoT FleetWise
[product page][guidance-page] and the
[Region-availability page][fleetwise-regions].

This document is a starting point for teams evaluating Guidance for Connected Mobility
on AWS (CMS) as the equivalent-capability path. It is deliberately honest about where
capabilities are native, where they are architecturally different, and where they are
missing.

## What this repository does NOT require

CMS does **not** call the AWS IoT FleetWise managed service:

- No stack imports `aws_cdk.aws_iotfleetwise`.
- No template creates `AWS::IoTFleetWise::*` resources.
- No handler or script calls `boto3.client("iotfleetwise")`.

CMS **does** use the
[AWS IoT FleetWise Edge Agent](https://github.com/aws/aws-iot-fleetwise-edge)
(FWE) — the **open-source reference edge agent**, built from source in
`deployment/ecr/cms-fwe-agent/Dockerfile`. The edge agent is a separate artifact from
the managed service, and its availability is unaffected by the service's status.
Throughout CMS documentation, "FleetWise Edge" or "FWE" always refers to that open-
source agent.

## Concept map

The table below maps AWS IoT FleetWise concepts to their equivalent in CMS. Two
categories are called out and *not* claimed as parity: **data destinations**
(architecturally different) and **vision system data** (not covered by CMS).

| AWS IoT FleetWise concept | Equivalent in CMS | Where it lives |
|---|---|---|
| Signal catalog | Native. A CMS-managed signal catalog seeded from the DBC file at `services/simulation/can/cms-fleet.dbc`. | DynamoDB (`cms-{stage}-signal-catalog`); seeded via `deployment/scripts/seed_signal_catalog.py`. |
| Decoder manifest | Native. Maps CAN signal IDs to human-readable signal names; delivered to the FWE agent as protobuf, and used inside CMS by the `FWTelemetryProcessor` Flink app to decode uploaded telemetry. | DynamoDB (`cms-{stage}-decoder-manifest`); seeded via `deployment/scripts/seed_decoder_manifest.py`. |
| Model manifest | Native. Vehicle-model definitions with ECU inventory + signal counts. | DynamoDB (`cms-{stage}-model-manifest`); seeded via `deployment/scripts/seed_model_manifests.py`. |
| Campaigns (collection schemes) | Native. Time-based and condition-based campaigns drive FWE data collection; the `CampaignSyncProcessor` Flink app pushes collection schemes + decoder manifests to the agent over IoT Core MQTT on each agent checkin. | Campaign records: DynamoDB (`cms-{stage}-campaigns`). Runtime: `modules/flink/campaign_sync_processor/`. |
| Fleets | Native. Fleets are a first-class domain object with per-fleet campaigns, per-fleet access control, and bulk enrollment workflows. | See `services/connectors/oem1/README.md` for bulk fleet operations and `README.md` § "OEM1 Fleet Lifecycle Management". |
| Remote commands | Native. Commands are delivered to the FWE agent as protobuf `CommandRequest` messages on IoT Core MQTT, and responses are decoded by a Command Response Handler Lambda. | `services/commands/`; IoT Core topics under `cms/commands/things/{VIN}/executions/`. |
| Vehicle "last known state" | Present. The Flink telemetry processor writes each signal value to Redis (ElastiCache) hashes on every message, providing sub-millisecond vehicle-state lookups. | ElastiCache for Redis, populated by the `TelemetryProcessor` Flink app. |
| **Data destinations** | **Architecturally different.** AWS IoT FleetWise supports Amazon S3, Timestream, and MQTT topics as first-class data destinations. CMS routes telemetry through **IoT Core → Amazon MSK (Kafka) → Apache Flink → Amazon DynamoDB**, with real-time state in Redis. This is a different pipeline, not a missing one, and choosing between them is an architectural decision — evaluate against your latency, retention, query, and cost targets. | Pipeline: `deployment/stacks/{msk_stack,flink_stack,telemetry_integration_stack}.py`; storage: DynamoDB tables listed in `deployment/stacks/storage_stack.py`. |
| **Vision system data (camera/lidar)** | **Not covered.** AWS IoT FleetWise supports vision-system data collection (camera and lidar streams). CMS does not implement this pathway. Teams migrating a vision-system workload need to design that integration themselves. | — |

## What "equivalent capability" means in practice

Six of the eight concept categories above are represented natively in CMS and are
seeded, tested, and exercised end-to-end by the deployment. One is thin (Vehicle Last
Known State — present, though richer implementations may live in downstream systems).
Two are called out above as gaps or differences that migration teams should evaluate
against their own workload.

If your FleetWise use case is signal collection + campaign-driven data upload +
downstream processing that lands in your own store, CMS's native services cover the
same ground and are what AWS's availability-change notice points at.

If your FleetWise use case depends on **vision-system data** or on **the S3 /
Timestream / MQTT destination integrations** the managed service ships, evaluate those
specifically before treating this as a drop-in path.

## Related documentation

- **AWS IoT FleetWise availability change** —
  <https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/iotfleetwise-availability-change.html>
- **Guidance for Connected Mobility on AWS (product page)** —
  <https://aws.amazon.com/solutions/guidance/connected-mobility-on-aws/>
- **AWS IoT FleetWise Region and feature availability** —
  <https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/fleetwise-regions.html>
- **AWS IoT FleetWise Edge Agent (open source)** —
  <https://github.com/aws/aws-iot-fleetwise-edge>
- **CMS README** — [`../README.md`](../README.md)
- **CMS Implementation Guide** — published as part of *Guidance for Connected Mobility
  on AWS* on the AWS Solutions Library.

[availability-change]: https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/iotfleetwise-availability-change.html
[guidance-page]: https://aws.amazon.com/solutions/guidance/connected-mobility-on-aws/
[fleetwise-regions]: https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/fleetwise-regions.html

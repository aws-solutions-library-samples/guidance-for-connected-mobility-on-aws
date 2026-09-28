#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Seed one decoder manifest per Meridian vehicle model.

Companion to `seed_model_manifests.py`. Each Meridian model manifest declares
a distinct `decoderManifestRef` (`meridian-<line>-v1`), but until this script
ran, only the CMS baseline decoder manifest existed in the decoder-manifest
table — every Meridian model's `decoderManifestRef` pointed at nothing, and
the `/decoder-manifests` API returned only the CMS baseline row.

This script writes the six-field metadata row that `/decoder-manifests`
projects (`decoderManifestName`, `decoderManifestVersion`, `description`,
`modelName`, `status`, `createTimestamp`). It does NOT populate CAN signal
mappings or network interfaces — those are FleetWise-specific and are
handled by `seed_decoder_and_campaign.py` for the CMS baseline. The Meridian
manifests are demo metadata for the Data Model screens.

The seven manifests are sized in parallel to the model manifests'
`signalCount` to keep the cross-reference honest:

    meridian-windrose-v1   EV       252 signals
    meridian-trailwind-v1  EV       245 signals
    meridian-crestwind-v1  EV       240 signals
    meridian-zephyr-v1     EV       248 signals
    meridian-azimuth-v1    Hybrid   232 signals
    meridian-sirocco-v1    Diesel   215 signals
    meridian-mistral-v1    Gasoline 210 signals

Idempotent: PutItem with a stable pk/sk overwrites; a second run is a no-op.
"""
from __future__ import annotations

import os
import time

import boto3

STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")

session = (
    boto3.Session(profile_name=PROFILE, region_name=REGION)
    if PROFILE
    else boto3.Session(region_name=REGION)
)
dynamodb = session.resource("dynamodb")

DECODER_TABLE = f"cms-{STAGE}-decoder-manifest"

# One row per Meridian model. `decoderManifestName` values match the
# `decoderManifestRef` values declared in seed_model_manifests.py.
MERIDIAN_DECODER_MANIFESTS = [
    {
        "decoderManifestName": "meridian-windrose-v1",
        "modelName": "MERIDIAN-WINDROSE",
        "signalCount": 252,
        "powertrain": "EV",
        "description": "Meridian Windrose EV decoder manifest (252 signals). Full-EV SUV; no ECM.",
    },
    {
        "decoderManifestName": "meridian-trailwind-v1",
        "modelName": "MERIDIAN-TRAILWIND",
        "signalCount": 245,
        "powertrain": "EV",
        "description": "Meridian Trailwind EV decoder manifest (245 signals). Full-EV SUV; no ECM. 2023 is the CES demo vehicle.",
    },
    {
        "decoderManifestName": "meridian-crestwind-v1",
        "modelName": "MERIDIAN-CRESTWIND",
        "signalCount": 240,
        "powertrain": "EV",
        "description": "Meridian Crestwind EV decoder manifest (240 signals). Full-EV Sedan; no ECM.",
    },
    {
        "decoderManifestName": "meridian-zephyr-v1",
        "modelName": "MERIDIAN-ZEPHYR",
        "signalCount": 248,
        "powertrain": "EV",
        "description": "Meridian Zephyr EV decoder manifest (248 signals). Full-EV Van; no ECM.",
    },
    {
        "decoderManifestName": "meridian-azimuth-v1",
        "modelName": "MERIDIAN-AZIMUTH",
        "signalCount": 232,
        "powertrain": "HYBRID",
        "description": "Meridian Azimuth hybrid decoder manifest (232 signals). ECM + HV pack (BMS+CCU); superset ECU set.",
    },
    {
        "decoderManifestName": "meridian-sirocco-v1",
        "modelName": "MERIDIAN-SIROCCO",
        "signalCount": 215,
        "powertrain": "ICE_DIESEL",
        "description": "Meridian Sirocco ICE-diesel decoder manifest (215 signals). ECM present; ECU_EVAP absent (diesel).",
    },
    {
        "decoderManifestName": "meridian-mistral-v1",
        "modelName": "MERIDIAN-MISTRAL",
        "signalCount": 210,
        "powertrain": "ICE_GASOLINE",
        "description": "Meridian Mistral ICE-gasoline decoder manifest (210 signals). ECM present with EVAP.",
    },
]


def seed_one(row: dict) -> None:
    table = dynamodb.Table(DECODER_TABLE)
    name = row["decoderManifestName"]
    version = "1"
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Metadata row — this is the shape /decoder-manifests projects (six fields:
    # decoderManifestName, decoderManifestVersion, description, modelName,
    # status, createTimestamp). Keep the field set aligned with the CMS
    # baseline seeded by seed_decoder_and_campaign.py::seed_decoder_manifest.
    table.put_item(
        Item={
            "pk": f"DECODER#{name}#{version}",
            "sk": f"DECODER#{name}",
            "decoderManifestName": name,
            "decoderManifestVersion": version,
            "status": "ACTIVE",
            "modelName": row["modelName"],
            "description": row["description"],
            "signalCount": row["signalCount"],
            "powertrain": row["powertrain"],
            "createTimestamp": now,
            "updateTimestamp": now,
        }
    )
    print(f"  ✅ {name} ({row['signalCount']} signals, {row['powertrain']})")


def main() -> None:
    print(f"Seeding {DECODER_TABLE} with Meridian decoder manifests...")
    for row in MERIDIAN_DECODER_MANIFESTS:
        seed_one(row)
    print(f"Done — {len(MERIDIAN_DECODER_MANIFESTS)} decoder manifests seeded.")


if __name__ == "__main__":
    main()

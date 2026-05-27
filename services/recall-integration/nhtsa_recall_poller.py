#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
NHTSA Recall Integration — fetches real recall data and matches against fleet vehicles.
Outputs JSON that the CMS UI can consume.
"""

import json
import os
import urllib.request
import boto3
from datetime import datetime

REGION = os.environ.get("AWS_REGION", "us-east-1")
VEHICLES_TABLE = "cms-prod-storage-vehicles"
RECALLS_TABLE = "cms-prod-storage-recalls"
OUTPUT_FILE = "nhtsa_fleet_recalls.json"


def get_fleet_vehicles():
    """Scan DynamoDB for all fleet vehicles."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(VEHICLES_TABLE)
    items = []
    resp = table.scan()
    items.extend(resp["Items"])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp["Items"])
    return items


def fetch_nhtsa_recalls(make, model, year):
    """Fetch recalls from NHTSA API for a make/model/year."""
    from urllib.parse import quote
    url = (
        "https://api.nhtsa.gov/recalls/recallsByVehicle"
        f"?make={quote(make)}&model={quote(model)}&modelYear={quote(str(year))}"
    )
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read())
        return data.get("results", [])
    except Exception as e:
        print(f"  Error fetching {make} {model} {year}: {e}")
        return []


def match_recalls_to_fleet(vehicles):
    """Fetch NHTSA recalls for each unique make/model/year in the fleet."""
    # Get unique make/model/year combos
    combos = set()
    for v in vehicles:
        make = v.get("make", "")
        model = v.get("model", "")
        # Vehicles table stores year as numeric "year" attribute, not "modelYear".
        # Keep modelYear as a fallback in case schema drifts back.
        year_raw = v.get("year") or v.get("modelYear") or "2022"
        year = str(int(year_raw)) if isinstance(year_raw, (int, float)) else str(year_raw)
        if make and model:
            combos.add((make, model, year))

    print(f"Fleet has {len(vehicles)} vehicles across {len(combos)} make/model/year combos\n")

    # Fetch recalls for each combo
    all_recalls = {}
    for make, model, year in sorted(combos):
        recalls = fetch_nhtsa_recalls(make, model, year)
        print(f"  {make:15s} {model:15s} {year}: {len(recalls)} recalls")
        for r in recalls:
            campaign = r["NHTSACampaignNumber"]
            if campaign not in all_recalls:
                all_recalls[campaign] = {
                    "campaignNumber": campaign,
                    "component": r["Component"],
                    "summary": r["Summary"],
                    "consequence": r.get("Consequence", ""),
                    "remedy": r.get("Remedy", ""),
                    "manufacturer": r["Manufacturer"],
                    "reportDate": r.get("ReportReceivedDate", ""),
                    "parkIt": r.get("parkIt", False),
                    "affectedVehicles": [],
                }
            # Add matching vehicles
            for v in vehicles:
                if (v.get("make", "").upper() == r["Make"].upper() and
                    v.get("model", "").upper() == r["Model"].upper()):
                    vid = v.get("vehicleId", "")
                    if vid not in [av["vehicleId"] for av in all_recalls[campaign]["affectedVehicles"]]:
                        all_recalls[campaign]["affectedVehicles"].append({
                            "vehicleId": vid,
                            "vin": v.get("vin", ""),
                            "make": v.get("make", ""),
                            "model": v.get("model", ""),
                        })

    # Filter to recalls that actually affect our fleet
    fleet_recalls = {k: v for k, v in all_recalls.items() if len(v["affectedVehicles"]) > 0}
    return fleet_recalls


def classify_severity(recall):
    """Classify recall severity based on component and parkIt flag."""
    if recall["parkIt"]:
        return "Critical"
    component = recall["component"].upper()
    if any(kw in component for kw in ["BRAKE", "STEERING", "AIR BAG", "FUEL SYSTEM"]):
        return "High"
    if any(kw in component for kw in ["ENGINE", "POWER TRAIN", "ELECTRICAL"]):
        return "Medium"
    return "Low"


def main():
    print("=" * 60)
    print("NHTSA Recall Integration")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60 + "\n")

    # Get fleet vehicles
    print("Scanning fleet vehicles...")
    vehicles = get_fleet_vehicles()
    print(f"Found {len(vehicles)} vehicles\n")

    # Fetch and match recalls
    print("Fetching NHTSA recalls...")
    fleet_recalls = match_recalls_to_fleet(vehicles)

    # Classify and sort
    for campaign, recall in fleet_recalls.items():
        recall["severity"] = classify_severity(recall)
        recall["affectedCount"] = len(recall["affectedVehicles"])

    sorted_recalls = sorted(fleet_recalls.values(), key=lambda x: (
        {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}[x["severity"]],
        -x["affectedCount"]
    ))

    # Summary
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {len(sorted_recalls)} recalls affecting fleet vehicles\n")
    for r in sorted_recalls[:20]:
        print(f"  {r['campaignNumber']:12s}  [{r['severity']:8s}]  {r['affectedCount']:2d} vehicles  {r['component'][:50]}")

    total_affected = sum(r["affectedCount"] for r in sorted_recalls)
    print(f"\n  Total vehicle-recall combinations: {total_affected}")
    print(f"  Critical: {sum(1 for r in sorted_recalls if r['severity'] == 'Critical')}")
    print(f"  High:     {sum(1 for r in sorted_recalls if r['severity'] == 'High')}")
    print(f"  Medium:   {sum(1 for r in sorted_recalls if r['severity'] == 'Medium')}")
    print(f"  Low:      {sum(1 for r in sorted_recalls if r['severity'] == 'Low')}")

    # Write output
    output = {
        "generatedAt": datetime.now().isoformat(),
        "fleetSize": len(vehicles),
        "totalRecalls": len(sorted_recalls),
        "totalAffectedVehicles": total_affected,
        "recalls": sorted_recalls,
    }

    # Write the full JSON output next to this script (same fix as the TS file
    # above — previously CWD-relative).
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_out_path = os.path.join(script_dir, OUTPUT_FILE)
    with open(json_out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nOutput written to {json_out_path}")

    # Also generate a TypeScript data file for the UI. Previously capped
    # at the top 10 recalls, but that caused some fleet recalls to silently
    # disappear from the UI (notably GM/Chevrolet recalls when the fleet
    # had only Ford matches at the top). Keep all recalls — it's still a
    # bounded dataset (NHTSA typically returns <100 per make/model/year).
    ts_recalls = []
    for r in sorted_recalls:
        ts_recalls.append({
            "id": r["campaignNumber"],
            "component": r["component"],
            "manufacturer": r["manufacturer"],
            "summary": r["summary"][:200],
            "severity": r["severity"],
            "affected": r["affectedCount"],
            "confirmed": max(1, r["affectedCount"] // 3),  # Simulate telemetry confirmation
            "population": r["affectedCount"] - max(1, r["affectedCount"] // 3),
            "scheduled": max(0, r["affectedCount"] // 4),
            "completed": 0,
            "grounded": 1 if r["severity"] == "Critical" else 0,
            "date": r["reportDate"],
            "vehicles": [v["vehicleId"] for v in r["affectedVehicles"]],
        })

    ts_output = f"""// Auto-generated from NHTSA Recalls API — {datetime.now().strftime('%Y-%m-%d %H:%M')}
// {len(sorted_recalls)} recalls found affecting {total_affected} vehicle-recall combinations

export const nhtsaRecalls = {json.dumps(ts_recalls, indent=2)};
"""

    # Write the TS file to both:
    #   1. The services/recall-integration directory (for debugging and version
    #      history — sits next to the script that generated it)
    #   2. The CMS UI frontend source tree (what the UI actually imports)
    # Previously we only wrote to CWD, which meant the Makefile's
    # `seed-nhtsa-recalls` target dropped the file at the project root and
    # the UI kept using stale data. Now both destinations are explicit and
    # resolved relative to this script's own location, not CWD.
    repo_root = os.path.normpath(os.path.join(script_dir, "..", ".."))

    ts_destinations = [
        os.path.join(script_dir, "nhtsaRecallData.ts"),
        os.path.join(
            repo_root,
            "modules",
            "cms_ui",
            "source",
            "frontend",
            "src",
            "components",
            "recall-warranty",
            "nhtsaRecallData.ts",
        ),
    ]
    for ts_file in ts_destinations:
        # Skip silently if the UI path doesn't exist (e.g., when this script
        # is run from a service-only checkout without the UI module). The
        # services/recall-integration copy always succeeds.
        parent_dir = os.path.dirname(ts_file)
        if not os.path.isdir(parent_dir):
            print(f"Skipping {ts_file} — directory does not exist")
            continue
        with open(ts_file, "w") as f:
            f.write(ts_output)
        print(f"TypeScript data written to {ts_file}")


if __name__ == "__main__":
    main()

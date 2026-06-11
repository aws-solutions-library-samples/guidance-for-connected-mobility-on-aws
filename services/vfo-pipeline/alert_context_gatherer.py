"""
Alert Context Gatherer — Lambda that enriches maintenance alerts with vehicle context
for the maintenance agent to make informed decisions.

Triggered by Step Functions. Receives a batch of alert records, returns enriched context.
"""
import boto3
import json
import os
from datetime import datetime, timezone

REGION = os.environ.get("AWS_REGION", "us-east-1")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")

ddb = boto3.resource("dynamodb", region_name=REGION)

def handler(event, context):
    """Enrich a batch of maintenance alerts with vehicle context."""
    # Handle both direct invocation {"alerts":[...]} and DDB stream records from EventBridge Pipe
    if isinstance(event, list):
        raw_records = event
    else:
        raw_records = event.get("alerts", [])

    # Unmarshal DDB stream records if needed
    alerts = []
    for record in raw_records:
        if isinstance(record, dict) and "dynamodb" in record and record.get("eventName") == "INSERT":
            img = record["dynamodb"].get("NewImage", {})
            alert = {}
            for k, v in img.items():
                if "S" in v: alert[k] = v["S"]
                elif "N" in v: alert[k] = v["N"]
                elif "BOOL" in v: alert[k] = v["BOOL"]
                else: alert[k] = str(list(v.values())[0])
            alerts.append(alert)
        elif isinstance(record, dict) and "vehicleId" in record:
            alerts.append(record)

    if not alerts:
        return {"enriched_alerts": [], "summary": "No alerts to process"}

    # Group by vehicle
    by_vehicle = {}
    for alert in alerts:
        vid = alert.get("vehicleId", "")
        if vid not in by_vehicle:
            by_vehicle[vid] = []
        by_vehicle[vid].append(alert)

    vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
    service_table = ddb.Table(f"cms-{STAGE}-storage-service-history")
    fleet_table = ddb.Table(f"cms-{STAGE}-storage-fleets")

    enriched = []
    fleet_capacity = {}

    for vid, vehicle_alerts in by_vehicle.items():
        # Get vehicle record
        vehicle = {}
        try:
            resp = vehicles_table.get_item(Key={"vehicleId": vid})
            vehicle = resp.get("Item", {})
        except Exception:
            pass

        # Get recent service history
        services = []
        try:
            resp = service_table.query(
                KeyConditionExpression="vehicleId = :v",
                ExpressionAttributeValues={":v": vid},
                ScanIndexForward=False, Limit=5
            )
            services = resp.get("Items", [])
        except Exception:
            pass

        # Get fleet capacity (cache per fleet)
        fleet_id = vehicle.get("fleetId", "")
        if fleet_id and fleet_id not in fleet_capacity:
            try:
                # Count idle vehicles in same fleet
                resp = vehicles_table.scan(
                    FilterExpression="fleetId = :f AND #s IN (:s1, :s2)",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":f": fleet_id,
                        ":s1": "active",
                        ":s2": "idle"
                    }
                )
                idle = [v for v in resp.get("Items", []) if v.get("vehicleId") != vid]
                fleet_capacity[fleet_id] = {
                    "available_vehicles": [v["vehicleId"] for v in idle[:5]],
                    "count": len(idle)
                }
            except Exception:
                fleet_capacity[fleet_id] = {"available_vehicles": [], "count": 0}

        # Build enriched context
        for alert in vehicle_alerts:
            enriched.append({
                "alert": alert,
                "vehicle": {
                    "vehicleId": vid,
                    "make": vehicle.get("make", ""),
                    "model": vehicle.get("model", ""),
                    "year": str(vehicle.get("year", "")),
                    "mileage": str(vehicle.get("odometer", vehicle.get("mileage", ""))),
                    "warrantyActive": vehicle.get("warrantyActive", False),
                    "warrantyEndDate": vehicle.get("warrantyEndDate", ""),
                    "fleetId": fleet_id,
                    "status": vehicle.get("status", ""),
                },
                "recent_services": [
                    {"type": s.get("serviceType",""), "date": s.get("serviceDate",""), "cost": str(s.get("cost",""))}
                    for s in services[:3]
                ],
                "fleet_capacity": fleet_capacity.get(fleet_id, {}),
            })

    # Build summary for agent prompt
    alert_types = {}
    for a in alerts:
        t = a.get("alertType", "unknown")
        alert_types[t] = alert_types.get(t, 0) + 1

    summary = f"{len(alerts)} alerts for {len(by_vehicle)} vehicles. "
    summary += "Types: " + ", ".join(f"{t}({c})" for t, c in alert_types.items())

    return {
        "enriched_alerts": enriched,
        "summary": summary,
        "vehicle_count": len(by_vehicle),
        "alert_count": len(alerts),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

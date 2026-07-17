"""
Decision Executor — Executes the maintenance agent's decisions.
Writes service records, updates vehicle status, logs to decision journal.
"""
import boto3
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

REGION = os.environ.get("AWS_REGION", "us-east-1")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")

ddb = boto3.resource("dynamodb", region_name=REGION)

def handler(event, context):
    """Execute agent decisions."""
    decisions = event.get("decisions", [])
    if not decisions:
        return {"executed": 0}

    service_table = ddb.Table(f"cms-{STAGE}-storage-service-history")
    vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
    journal_table = ddb.Table(f"cms-{STAGE}-decision-journal")

    executed = 0
    for decision in decisions:
        try:
            action = decision.get("action", "")
            vid = decision.get("vehicleId", "")
            now = datetime.now(timezone.utc)

            if action == "SCHEDULE_SERVICE":
                # Create service appointment
                service_table.put_item(Item={
                    "vehicleId": vid,
                    "serviceDate": decision.get("scheduledDate", now.strftime("%Y-%m-%dT%H:%M:%S")),
                    "serviceType": decision.get("serviceType", "MAINTENANCE"),
                    "status": "SCHEDULED",
                    "description": decision.get("description", ""),
                    "provider": decision.get("serviceCenter", "TBD"),
                    "cost": Decimal(str(decision.get("estimatedCost", 0))),
                    "mileageAtService": Decimal(str(decision.get("mileage", 0))),
                    "category": "REPAIR",
                    "scheduledBy": "cms-maintenance-agent",
                })

                # Update vehicle status
                vehicles_table.update_item(
                    Key={"vehicleId": vid},
                    UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "service_scheduled"}
                )

            elif action == "REASSIGN_VEHICLE":
                replacement = decision.get("replacementVehicle", "")
                if replacement:
                    vehicles_table.update_item(
                        Key={"vehicleId": replacement},
                        UpdateExpression="SET #s = :s",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":s": "active"}
                    )

            # Write to decision journal
            journal_table.put_item(Item={
                "decisionId": f"DEC-{now.strftime('%Y%m%d')}-{str(uuid.uuid4())[:8]}",
                "alertId": decision.get("alertId", ""),
                "vehicleId": vid,
                "fleetId": decision.get("fleetId", ""),
                "agentId": "cms-maintenance-agent",
                "category": decision.get("alertType", ""),
                "severity": decision.get("severity", ""),
                "decision": action,
                "reasoning": decision.get("reasoning", ""),
                "actions_taken": decision.get("actions", []),
                "estimated_cost": Decimal(str(decision.get("estimatedCost", 0))),
                "service_center": decision.get("serviceCenter", ""),
                "replacement_vehicle": decision.get("replacementVehicle", ""),
                "scheduled_date": decision.get("scheduledDate", ""),
                "outcome": "PENDING",
                "timestamp": int(now.timestamp() * 1000),
            })

            executed += 1
        except Exception as e:
            print(f"Error executing decision for {vid}: {e}")

    return {"executed": executed, "total": len(decisions)}

"""
Amazon Connect contact flow Lambda — fetches escalation context.

Called by the Connect contact flow when a call is routed from the AI agent.
Reads the escalation record from the VFO action queue and returns vehicle +
conversation context as contact attributes for the human agent's desktop.

Input (from Connect contact flow):
  Parameters.escalationId — the action queue record ID

Output (Connect contact attributes):
  vehicleId, vin, driverName, severity, summary, dtcCodes, conversationSummary
"""

import os
import json
import logging
import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

_TABLE = os.environ["ACTION_QUEUE_TABLE"]
_ddb = None


def _get_ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb").Table(_TABLE)
    return _ddb


def handler(event, context):
    """Connect invokes this with event['Details']['Parameters']."""
    params = event.get("Details", {}).get("Parameters", {})
    escalation_id = params.get("escalationId", "")

    if not escalation_id:
        log.warning("No escalationId in contact flow parameters")
        return _empty_response()

    log.info(f"Looking up escalation: {escalation_id}")

    try:
        resp = _get_ddb().get_item(Key={"actionId": escalation_id})
        item = resp.get("Item")
        if not item:
            log.warning(f"Escalation {escalation_id} not found in {_TABLE}")
            return _empty_response()

        # Extract fields for the agent desktop
        return {
            "vehicleId": str(item.get("vehicleId", "")),
            "vin": str(item.get("vin", "")),
            "driverName": str(item.get("driverName", "")),
            "driverPhone": str(item.get("driverPhone", "")),
            "severity": str(item.get("severity", "")),
            "summary": _truncate(str(item.get("summary", "")), 1024),
            "dtcCodes": str(item.get("dtcCodes", "")),
            "conversationSummary": _truncate(
                str(item.get("conversationSummary", "")), 2048
            ),
            "recommendedAction": _truncate(
                str(item.get("recommendedAction", "")), 512
            ),
            "escalationType": str(item.get("escalationType", "")),
            "createdAt": str(item.get("createdAt", "")),
        }

    except Exception as e:
        log.error(f"Error fetching escalation: {e}")
        return _empty_response()


def _empty_response():
    return {
        "vehicleId": "",
        "vin": "",
        "driverName": "",
        "driverPhone": "",
        "severity": "UNKNOWN",
        "summary": "Context unavailable",
        "dtcCodes": "",
        "conversationSummary": "",
        "recommendedAction": "",
        "escalationType": "",
        "createdAt": "",
    }


def _truncate(s: str, max_len: int) -> str:
    """Connect contact attributes have a 32KB total limit; truncate long fields."""
    return s[:max_len] if len(s) > max_len else s

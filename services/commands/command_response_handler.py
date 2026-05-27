"""
Command Response Handler — IoT rule action that processes vehicle command acks.
Triggered by IoT rules on:
  - 'cms/commands/things/+/executions/+/response/protobuf' (FWE protobuf)
  - 'cms/commands/+/response' (legacy JSON from MQTT direct simulators)
Updates command status in DDB.
"""
import json, os, boto3, time, base64
from datetime import datetime, timezone

ddb = boto3.resource('dynamodb')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
COMMANDS_TABLE = ddb.Table(os.environ.get('COMMANDS_TABLE', f'cms-{STAGE}-storage-commands'))

# FWE protobuf status enum → string
FWE_STATUS_MAP = {
    0: 'UNKNOWN',
    1: 'SUCCEEDED',
    2: 'TIMEOUT',
    4: 'FAILED',
    10: 'IN_PROGRESS',
}


def _parse_protobuf(payload_bytes):
    """Decode FWE CommandResponse protobuf."""
    import command_response_pb2 as resp_pb
    resp = resp_pb.CommandResponse()
    resp.ParseFromString(payload_bytes)
    return {
        'commandId': resp.command_id,
        'status': FWE_STATUS_MAP.get(resp.status, 'UNKNOWN'),
        'reason': resp.reason_description,
        'reasonCode': resp.reason_code,
    }


def handler(event, context):
    """Process command response from vehicle (protobuf or JSON)."""
    try:
        # Detect protobuf vs JSON payload
        # IoT rule with base64-encoded binary payload passes 'b64_payload'
        b64 = event.get('b64_payload')
        if b64:
            payload_bytes = base64.b64decode(b64)
            parsed = _parse_protobuf(payload_bytes)
            command_id = parsed['commandId']
            status = parsed['status']
            reason = parsed.get('reason', '')
            vehicle_id = event.get('vehicleId', '')
        else:
            # Legacy JSON path
            command_id = event.get('commandId')
            status = event.get('status', 'UNKNOWN')
            vehicle_id = event.get('vehicleId', '')
            reason = event.get('reason', '')

        if not command_id:
            print(f"⚠️ No commandId in response: {json.dumps(event)}")
            return

        now = datetime.now(timezone.utc)
        update_expr = 'SET #s = :s, respondedAt = :r, updatedAt = :u'
        expr_values = {
            ':s': status,
            ':r': now.isoformat(),
            ':u': int(now.timestamp() * 1000),
        }
        expr_names = {'#s': 'status'}

        if reason:
            update_expr += ', reason = :reason'
            expr_values[':reason'] = reason

        # Calculate latency
        try:
            item = COMMANDS_TABLE.get_item(Key={'commandId': command_id}).get('Item')
            if item and item.get('timestamp'):
                latency_ms = int(now.timestamp() * 1000) - int(item['timestamp'])
                update_expr += ', latencyMs = :lat'
                expr_values[':lat'] = latency_ms
        except Exception:
            pass

        COMMANDS_TABLE.update_item(
            Key={'commandId': command_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )

        print(f"✅ Command {command_id} → {status} for {vehicle_id} (reason: {reason})")

    except Exception as e:
        print(f"❌ Error processing command response: {e}")
        raise

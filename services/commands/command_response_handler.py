"""
Command Response Handler — IoT rule action that processes vehicle command acks.
Triggered by IoT rules on:
  - 'cms/commands/things/+/executions/+/response/protobuf' (FWE protobuf)
  - 'cms/commands/+/response' (legacy JSON from MQTT direct simulators)
  - 'cms/commands/things/+/executions/+/sovd/response' (SOVD JSON diagnostics)
Updates command status in DDB and, for SOVD responses, writes dtc-history rows.

SOVD topic routing rationale (Task 2.3):
  Topic-based routing is used (event['topic'] matching the SOVD sub-path) rather than
  payload-field inspection (checking command_type in the JSON body).  The IoT rule SQL
  `SELECT * FROM 'cms/commands/things/+/executions/+/sovd/response'` provides a strong
  guarantee at the broker level that only SOVD messages reach this branch; payload-field
  inspection would be spoofable by any ill-formed sidecar publishing to the topic with
  an unexpected command_type value.  The IoT rule is the authoritative discriminator.
"""
import hashlib
import json
import logging
import os
import base64
import boto3
import time
from decimal import Decimal
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ddb = boto3.resource('dynamodb')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
COMMANDS_TABLE = ddb.Table(os.environ.get('COMMANDS_TABLE', f'cms-{STAGE}-storage-commands'))
DTC_HISTORY_TABLE = ddb.Table(os.environ.get('DTC_HISTORY_TABLE', f'cms-{STAGE}-storage-dtc-history'))

# Maximum payload size to inline as `response` Map on the DDB command row.
# Matches the sidecar's own offload threshold (realtime_telemetry_simulator.py:1886).
# The `run_routine` path bypasses the sidecar's size guard (storage_uri=None, no
# threshold check), so this handler-side guard is the only thing preventing an
# oversized `run_routine` body from being written to DDB.
# NOTE: this is separate from DDB's 400 KB item limit — 80 KB is the sidecar's
# offload trigger, and we keep handler-side behaviour consistent with that contract.
_RESPONSE_SIZE_THRESHOLD_BYTES = 80 * 1024  # 81 920 bytes

# FWE protobuf status enum → string
FWE_STATUS_MAP = {
    0: 'UNKNOWN',
    1: 'SUCCEEDED',
    2: 'TIMEOUT',
    4: 'FAILED',
    10: 'IN_PROGRESS',
}

# SOVD topic suffix that the IoT rule routes on
_SOVD_TOPIC_SUFFIX = '/sovd/response'

# Terminal SOVD command statuses.  A command that has reached any of these states
# is considered settled — it must NOT be overwritten by a later non-terminal
# message (e.g. an out-of-order PROGRESS after the terminal has arrived).
# F22 (decisions.md 2026-09-04): the earlier guard covered only SUCCEEDED, leaving
# PARTIAL and FAILED strandable by a late PROGRESS.  Both are now protected.
# F21: 'SUCCEEDED' is a cross-file literal contract with the sidecar; do NOT rename.
_TERMINAL_STATUSES: frozenset = frozenset({'SUCCEEDED', 'PARTIAL', 'FAILED'})

# Rolling dedup window for dtc-history rows (24 hours in milliseconds)
_DEDUP_WINDOW_MS = 24 * 60 * 60 * 1000


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


def _extract_s3_key_from_uri(storage_uri: str) -> str:
    """Extract the S3 object key from an s3:// URI.

    R2 (spec): the DDB row stores the S3 key, not the pre-signed URL.
    Pre-signed URL generation happens on-demand at history GET route runtime,
    scoped to 15 minutes, using the caller's identity.  Storing a pre-signed
    URL would embed a bearer token with a fixed expiry in the audit record.

    FG2.2 (security-review Cycle 2 Warning #2): any URI that does not begin
    with 's3://' is rejected — return None and log a WARNING.  The caller
    must treat None as "no valid S3 storage" and MUST NOT store the raw string
    in the DDB Item's s3Key field.  This prevents a rogue sidecar from
    injecting an https:// pre-signed URL (complete with X-Amz-Signature) into
    the audit record via the storage_uri field.

    Args:
        storage_uri: e.g. 's3://cms-staging-storage-sovd-responses/VIN/corr.json'

    Returns:
        Object key string (e.g. 'VIN/corr.json'), or None if the URI is not
        a valid s3:// URI.
    """
    if storage_uri is None:
        return None

    # Suggestion cleanup 2026-09-01 (reviewer C4 Suggestion 2 / SR C3 Suggestion 2):
    # defend against non-string inputs (list, dict, int) from a rogue or
    # malformed sidecar. The top-level handler catches AttributeError but a
    # typed guard here fails cleanly with a specific WARNING and avoids the
    # implicit stack-trace log.
    if not isinstance(storage_uri, str):
        logger.warning(
            "non-string storage_uri rejected, type=%s — "
            "the sidecar contract requires an s3:// URI as a string.",
            type(storage_uri).__name__,
        )
        return None

    if not storage_uri.startswith('s3://'):
        # Determine scheme for the warning log (safe to log — we do NOT echo the full URI)
        scheme = storage_uri.split('://')[0] if '://' in storage_uri else '<no-scheme>'
        logger.warning(
            "non-s3:// storage_uri rejected, scheme=%s — "
            "refusing to persist non-S3 URI to avoid storing bearer tokens",
            scheme,
        )
        return None

    # Remove 's3://<bucket>/' prefix to isolate the object key
    # Format: s3://<bucket>/<key>
    without_scheme = storage_uri[5:]  # strip 's3://'
    slash_pos = without_scheme.find('/')
    if slash_pos >= 0:
        return without_scheme[slash_pos + 1:]
    return without_scheme


def _write_dtc_history_rows(vehicle_id: str, command_id: str, correlation_id: str,
                             components: dict, now_ms: int) -> None:
    """Write dtc-history rows for a sovd_read_dtcs response.

    For each DTC in each component, attempts a conditional PutItem.
    If a row for (vehicleId, dtcCode, source='sovd') already exists and its
    firstSeenAt is within the 24 h rolling window, falls back to UpdateItem
    incrementing occurrenceCount and updating lastSeenAt.

    Dedup window anchors on firstSeenAt, NOT lastSeenAt — this is intentional.
    First-seen anchors the row; the window collapses repeated reads within 24 h
    of the first observation into a single record.

    Args:
        vehicle_id:     VIN or vehicle identifier.
        command_id:     The commandId of the SOVD read_dtcs command.
        correlation_id: Correlation ID from the SOVD response payload.
        components:     The 'components' dict from the SOVD response.
        now_ms:         Current epoch milliseconds for firstSeenAt/lastSeenAt.
    """
    window_start_ms = now_ms - _DEDUP_WINDOW_MS

    for ecu_id, ecu_data in components.items():
        dtcs = ecu_data.get('dtcs', [])
        for dtc in dtcs:
            dtc_code = dtc.get('code', '')
            if not dtc_code:
                continue

            freeze_frame = dtc.get('freeze_frame') or {}

            # Build the base item for a fresh insert
            item = {
                'vehicleId': vehicle_id,
                'dtcCode': dtc_code,
                'source': 'sovd',
                'commandId': command_id,
                'correlationId': correlation_id,
                'ecuId': ecu_id,
                'firstSeenAt': now_ms,
                'lastSeenAt': now_ms,
                'occurrenceCount': 1,
            }
            if freeze_frame:
                item['freezeFrame'] = freeze_frame

            try:
                # Conditional insert: only succeeds if no row exists for this
                # (vehicleId, dtcCode, source) composite within the 24 h window.
                # The condition checks that either no row exists at all, or the
                # existing row's firstSeenAt is older than the dedup window.
                DTC_HISTORY_TABLE.put_item(
                    Item=item,
                    ConditionExpression=(
                        'attribute_not_exists(vehicleId) OR firstSeenAt < :window_start'
                    ),
                    ExpressionAttributeValues={
                        ':window_start': window_start_ms,
                    },
                )
            except DTC_HISTORY_TABLE.meta.client.exceptions.ConditionalCheckFailedException:
                # A recent row exists — increment occurrenceCount and update lastSeenAt
                try:
                    DTC_HISTORY_TABLE.update_item(
                        Key={'vehicleId': vehicle_id, 'dtcCode': dtc_code},
                        UpdateExpression=(
                            'SET occurrenceCount = occurrenceCount + :one, '
                            'lastSeenAt = :now'
                        ),
                        ExpressionAttributeValues={
                            ':one': 1,
                            ':now': now_ms,
                            ':src': 'sovd',
                        },
                        ConditionExpression='#src = :src',
                        ExpressionAttributeNames={'#src': 'source'},
                    )
                except Exception as update_err:
                    print(f"⚠️ Failed to increment occurrenceCount for "
                          f"{vehicle_id}/{dtc_code}: {update_err}")
            except Exception as e:
                print(f"⚠️ Failed to write dtc-history for {vehicle_id}/{dtc_code}: {e}")


def _mark_dtc_history_cleared(vehicle_id: str, components: dict, now_ms: int,
                               cleared_by: str, attestation: dict) -> None:
    """Mark dtc-history rows as CLEARED_REMOTE for a sovd_clear_dtcs response.

    This operation is UpdateItem (idempotent), NEVER DeleteItem.
    The audit trail is write-once — clearing does not remove the historical record,
    it annotates it with the clearing event.

    Args:
        vehicle_id:  VIN or vehicle identifier.
        components:  The 'components' dict from the SOVD clear response (may contain
                     the DTC codes that were cleared, or ECU keys only).
        now_ms:      Current epoch milliseconds for clearedAt.
        cleared_by:  Email from attestation.user_email.
        attestation: Full attestation sub-map from the command payload.
    """
    for ecu_id, ecu_data in components.items():
        dtcs = ecu_data.get('dtcs', [])
        for dtc in dtcs:
            dtc_code = dtc.get('code', '')
            if not dtc_code:
                continue
            try:
                DTC_HISTORY_TABLE.update_item(
                    Key={'vehicleId': vehicle_id, 'dtcCode': dtc_code},
                    UpdateExpression=(
                        'SET #st = :cleared, clearedAt = :now, '
                        'clearedBy = :by, clearedAttestation = :att'
                    ),
                    ExpressionAttributeNames={'#st': 'status'},
                    ExpressionAttributeValues={
                        ':cleared': 'CLEARED_REMOTE',
                        ':now': now_ms,
                        ':by': cleared_by,
                        ':att': attestation,
                    },
                )
            except Exception as e:
                print(f"⚠️ Failed to mark {vehicle_id}/{dtc_code} CLEARED_REMOTE: {e}")


def _persist_response_payload(command_id: str, payload: dict) -> None:
    """Write the full SOVD response payload as a `response` Map attribute on the command row.

    This write is intentionally SEPARATE from the status/respondedAt/latencyMs update
    so that a failure here (e.g. oversized payload causing `update_item` to reject)
    never takes down the terminal-status write. Today, SUCCEEDED carries no
    ConditionExpression; a failed `update_item` would lose the command's terminal status,
    which is strictly worse than silently discarding the payload.

    Size guard: the `run_routine` sim path sets storage_uri=None and skips the sidecar's
    80 KB offload check (realtime_telemetry_simulator.py:~1779). Without an independent
    handler-side guard, an oversized `run_routine` response body reaches here and may
    exceed DDB's 400 KB item limit. We apply the same 80 KB threshold the sidecar uses.

    Logging discipline: raw response body MUST NOT be logged at INFO or above — some
    routines include VIN or freeze-frame data (PII/telemetry). Log size/checksum at INFO
    only; body at DEBUG.

    Args:
        command_id: the DDB partition key for the command row.
        payload:    the full parsed SOVD JSON payload from the IoT message.
    """
    try:
        serialized = json.dumps(payload, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        logger.warning(
            "response payload for %s is not JSON-serializable (%s) — skipped",
            command_id, type(exc).__name__,
        )
        return

    size_bytes = len(serialized.encode('utf-8'))
    if size_bytes > _RESPONSE_SIZE_THRESHOLD_BYTES:
        # Log size and a truncated checksum — enough to diagnose without echoing PII.
        digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:16]
        logger.info(
            "response payload for %s too large to inline (%d bytes > %d threshold, "
            "sha256_prefix=%s) — response attr NOT written",
            command_id, size_bytes, _RESPONSE_SIZE_THRESHOLD_BYTES, digest,
        )
        return

    logger.info(
        "persisting response payload for %s (%d bytes)",
        command_id, size_bytes,
    )
    logger.debug("response payload body for %s: %s", command_id, serialized)

    # DynamoDB's resource API rejects Python floats ("Float types are not
    # supported. Use Decimal types instead."), and the except-below is
    # deliberately non-fatal — so a single float anywhere in the payload used to
    # drop the ENTIRE response attribute with nothing but a WARNING, leaving a
    # SUCCEEDED row that carries no result at all.
    #
    # That is not hypothetical: 3 of the 6 pilot routines emit floats
    # (cell_balance_check, evap_leak_test, pack_isolation_test), so none of them
    # had ever had a response persisted. It stayed hidden because the SOVD IoT
    # rule was independently stripping the float-bearing arrays before they got
    # here (issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/) —
    # fixing that rule unmasked this immediately.
    #
    # Re-parsing the already-serialized form with parse_float=Decimal converts
    # every float at any depth in one pass, and costs nothing extra: `serialized`
    # was built above for the size guard.
    payload_ddb = json.loads(serialized, parse_float=Decimal)

    try:
        COMMANDS_TABLE.update_item(
            Key={'commandId': command_id},
            UpdateExpression='SET #resp = :resp',
            ExpressionAttributeNames={'#resp': 'response'},
            ExpressionAttributeValues={':resp': payload_ddb},
        )
    except Exception as exc:
        # Log the failure but do NOT re-raise. The caller (terminal-status update) has
        # already succeeded; this write is best-effort enrichment, not load-bearing.
        logger.warning(
            "failed to write response attr for %s (%s) — terminal status was already "
            "written; this is a non-fatal enrichment failure",
            command_id, exc,
        )


def _handle_sovd_response(event: dict, payload: dict, now: datetime) -> None:
    """Process an SOVD JSON response from the sidecar.

    Called when the IoT rule routes a message from
    'cms/commands/things/+/executions/+/sovd/response' to this Lambda.

    Handles two command types:
      - sovd_read_dtcs: writes DTC rows to dtc-history with source='sovd'.
      - sovd_clear_dtcs: marks matching dtc-history rows CLEARED_REMOTE.

    Also stores the S3 object KEY (not the pre-signed URL) on the commands row
    when storage_uri is present in the payload.  Per R2 in spec, pre-signed URL
    generation is deferred to the history GET route runtime.

    Args:
        event:   The raw IoT rule event dict.
        payload: The parsed JSON payload from the MQTT message.
        now:     Current datetime (UTC) for timestamping.
    """
    now_ms = int(now.timestamp() * 1000)
    command_id = payload.get('commandId') or payload.get('correlation_id', '')
    correlation_id = payload.get('correlationId') or payload.get('correlation_id', '')
    vehicle_id = event.get('vehicleId', '') or payload.get('vehicleId', '')
    command_type = payload.get('commandType') or payload.get('command_type', '')
    components = payload.get('components') or {}
    status = payload.get('status', 'SUCCEEDED')
    storage_uri = payload.get('storageUri') or payload.get('storage_uri')
    attestation = payload.get('attestation') or {}

    if not command_id:
        print(f"⚠️ No commandId/correlation_id in SOVD response: {json.dumps(event)}")
        return

    # --- PROGRESS branch (F22) ---
    # PROGRESS messages carry per-ECU progress metadata that must be persisted onto
    # the command row so that the frontend poll path can expose it.  They must NOT
    # update the command's terminal status, respondedAt, or latencyMs — those
    # describe the terminal response, not an in-flight progress report.
    # PROGRESS must also never reach _write_dtc_history_rows; the guard is explicit
    # here (not relying on _publish_ecu_progress happening to omit command_type).
    if status == 'PROGRESS':
        progress = payload.get('progress') or {}
        if not progress:
            print(f"⚠️ PROGRESS message for {command_id} carries no progress object — ignored.")
            return

        # Latest-progress semantics: only advance if the incoming ecu_index is
        # greater than the one already stored.  This guards against out-of-order
        # delivery at QoS-1 while still persisting the first arriving ECU report.
        # The condition is: no progress stored yet  OR  new ecu_index > stored.
        progress_update_expr = 'SET #prog = :prog, updatedAt = :u'
        progress_expr_values = {
            ':prog': progress,
            ':u': now_ms,
            ':new_idx': int(progress.get('ecu_index', 0)),
        }
        progress_expr_names = {'#prog': 'progress'}
        progress_condition = (
            'attribute_not_exists(#prog) OR '
            'attribute_not_exists(#prog.ecu_index) OR '
            '#prog.ecu_index < :new_idx'
        )

        try:
            COMMANDS_TABLE.update_item(
                Key={'commandId': command_id},
                UpdateExpression=progress_update_expr,
                ExpressionAttributeNames=progress_expr_names,
                ExpressionAttributeValues=progress_expr_values,
                ConditionExpression=progress_condition,
            )
            ecu_name = progress.get('ecu_name', '?')
            ecu_index = progress.get('ecu_index', '?')
            ecu_total = progress.get('ecu_total', '?')
            print(f"📶 SOVD PROGRESS {command_id} — ECU {ecu_index}/{ecu_total}: {ecu_name}")
        except COMMANDS_TABLE.meta.client.exceptions.ConditionalCheckFailedException:
            # A later ecu_index is already stored — harmless, discard this one.
            print(f"↩️  Ignored out-of-order PROGRESS for {command_id} "
                  f"(ecu_index={progress.get('ecu_index')} already superseded).")
        except Exception as e:
            print(f"⚠️ Failed to persist PROGRESS for {command_id}: {e}")
        return

    # --- Update commands DDB row (terminal and non-PROGRESS statuses) ---
    update_expr = 'SET #s = :s, respondedAt = :r, updatedAt = :u'
    expr_values: dict = {
        ':s': status,
        ':r': now.isoformat(),
        ':u': now_ms,
    }
    expr_names: dict = {'#s': 'status'}

    # R2: store the S3 object KEY, not the pre-signed URL.
    # Pre-signed URL generation happens on-demand at _get_history read time,
    # scoped to 15 minutes, using the caller's Cognito identity.
    # Storing a pre-signed URL would embed a bearer token with a fixed expiry.
    # FG2.2: _extract_s3_key_from_uri returns None for non-s3:// URIs — skip
    # s3Key update entirely when None is returned (no bearer token in DDB row).
    if storage_uri:
        s3_key = _extract_s3_key_from_uri(storage_uri)
        if s3_key is not None:
            update_expr += ', s3Key = :s3k'
            expr_values[':s3k'] = s3_key

    # Calculate latency from command timestamp
    try:
        item = COMMANDS_TABLE.get_item(Key={'commandId': command_id}).get('Item')
        if item and item.get('timestamp'):
            latency_ms = now_ms - int(item['timestamp'])
            update_expr += ', latencyMs = :lat'
            expr_values[':lat'] = latency_ms
    except Exception:
        pass

    # Two independent idempotency rules (F21 / F22 / F23):
    #
    # Rule 1 (F22, new): a non-terminal status must not overwrite a terminal one.
    #   A slow PROGRESS arriving after PARTIAL must not demote PARTIAL.
    #   Condition: attribute not yet set, OR current value is not in _TERMINAL_STATUSES.
    #
    # Rule 2 (F21, pre-existing): a non-SUCCEEDED terminal status must not overwrite
    #   SUCCEEDED.  "A slow FAILED cannot overwrite a SUCCEEDED. Two independent
    #   responders can answer the same command; SUCCEEDED must win."
    #   This comment, written three lines above the T7.0 code that destroyed it, is
    #   the invariant — the code must match it.
    #   Condition: attribute not yet set, OR current value is not SUCCEEDED.
    #
    # Rule 3: SUCCEEDED itself carries NO condition — it is always allowed to write.
    #   This preserves SUCCEEDED-vs-PARTIAL precedence: SUCCEEDED may still overwrite
    #   PARTIAL (T6.5 v1.1 question deferred; this guard does not touch it).
    #
    # F23 root cause: T7.0 collapsed these into one membership test
    #   (`if status not in _TERMINAL_STATUSES:`), which applied Rule 1 only.
    #   PARTIAL and FAILED are terminal, so they took condition=None and wrote
    #   unconditionally — clobbering SUCCEEDED.  The fix separates the two rules.
    #
    # The conditions are built dynamically from _TERMINAL_STATUSES so that a test
    # can perturb the set and prove the guard weakens accordingly (negative control).
    condition = None
    if status == 'SUCCEEDED':
        # Rule 3: SUCCEEDED always wins — no condition needed.
        pass
    elif status in _TERMINAL_STATUSES:
        # Rule 2: terminal-but-not-SUCCEEDED may not overwrite SUCCEEDED.
        condition = 'attribute_not_exists(#s) OR #s <> :succeeded'
        expr_values[':succeeded'] = 'SUCCEEDED'
    else:
        # Rule 1: non-terminal may not overwrite any terminal status.
        terminal_placeholders = {
            f':_t{i}': v
            for i, v in enumerate(sorted(_TERMINAL_STATUSES))
        }
        in_list = ', '.join(terminal_placeholders.keys())
        condition = f'attribute_not_exists(#s) OR NOT #s IN ({in_list})'
        expr_values.update(terminal_placeholders)

    try:
        kwargs = dict(
            Key={'commandId': command_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        if condition:
            kwargs['ConditionExpression'] = condition
        COMMANDS_TABLE.update_item(**kwargs)
    except COMMANDS_TABLE.meta.client.exceptions.ConditionalCheckFailedException:
        print(f"↩️  Ignored {status} SOVD response for {command_id} — already in terminal state.")
        return

    # --- Persist response payload (G2A T2A.1) ---
    # The full SOVD response body is written as a `response` Map attribute so that
    # the session log can render it (T2A.2) and the "write to service line" flow can
    # include it in evidence.
    #
    # This is a SEPARATE write, intentionally decoupled from the terminal-status
    # update above:  the status write has already committed at this point.  If this
    # write fails (e.g. oversized payload rejected by DDB), the command still has
    # its correct terminal status — which is strictly better than today's behaviour
    # where the payload is silently discarded before the status write.
    #
    # The `run_routine` sim path sets `storage_uri=None` and skips the sidecar's
    # own 80 KB size guard, so _persist_response_payload applies its own threshold.
    # Only write when there is no sidecar-offloaded S3 key — if the sidecar already
    # stored the payload to S3 and put the key in s3Key, there is nothing to inline.
    if not storage_uri:
        _persist_response_payload(command_id, payload)

    # --- Write dtc-history rows ---
    # Explicit guard: PROGRESS is already handled and returned above, so only
    # terminal (and unknown) statuses reach here.  The command_type check further
    # scopes writes to the correct command types.
    if isinstance(components, dict):
        if command_type in ('sovd_read_dtcs', 'read_dtcs'):
            _write_dtc_history_rows(
                vehicle_id=vehicle_id,
                command_id=command_id,
                correlation_id=correlation_id,
                components=components,
                now_ms=now_ms,
            )
        elif command_type in ('sovd_clear_dtcs', 'clear_dtcs'):
            cleared_by = attestation.get('user_email', '')
            _mark_dtc_history_cleared(
                vehicle_id=vehicle_id,
                components=components,
                now_ms=now_ms,
                cleared_by=cleared_by,
                attestation=attestation,
            )

    print(f"✅ SOVD command {command_id} → {status} for {vehicle_id} "
          f"(command_type: {command_type})")


def handler(event, context):
    """Process command response from vehicle (protobuf, JSON, or SOVD JSON).

    IoT rules route messages here from three topic patterns:
      1. FWE protobuf:  'cms/commands/things/+/executions/+/response/protobuf'
      2. Legacy JSON:   'cms/commands/+/response'
      3. SOVD JSON:     'cms/commands/things/+/executions/+/sovd/response'

    SOVD routing is topic-based (event['topic'] suffix check).  The IoT rule SQL
    guarantees only SOVD messages arrive on the SOVD topic pattern, making topic
    routing more robust than payload-field inspection.
    """
    try:
        now = datetime.now(timezone.utc)

        # --- SOVD branch: detect by topic suffix ---
        topic = event.get('topic', '')
        if topic.endswith(_SOVD_TOPIC_SUFFIX):
            # SOVD JSON response path.
            # AWS IoT rule SQL 'SELECT * FROM ...' inlines all JSON payload fields
            # directly into the event dict (not nested under a 'payload' key).
            # The topic itself is injected as event['topic'] by the rule engine.
            # If a non-inline 'payload' or 'body' string is provided (e.g. in tests
            # that simulate an older rule format), parse it; otherwise use event as-is.
            raw = event.get('payload') or event.get('body')
            if raw and isinstance(raw, str) and raw.strip():
                payload = json.loads(raw)
            elif raw and isinstance(raw, dict):
                payload = raw
            else:
                # Standard IoT SELECT * path: all fields are already in event
                payload = event
            _handle_sovd_response(event, payload, now)
            return

        # Detect protobuf vs JSON payload
        # IoT rule with base64-encoded binary payload passes 'b64_payload'
        b64 = event.get('b64_payload')
        if b64:
            payload_bytes = base64.b64decode(b64)
            parsed = _parse_protobuf(payload_bytes)
            command_id = parsed['commandId']
            status = parsed['status']
            reason = parsed.get('reason', '')
            # FWE frequently rejects with a populated numeric reason_code and an
            # EMPTY reason_description — e.g. REASON_CODE_NO_DECODING_RULES_FOUND
            # is queued with "" as the description (ActuatorCommandManager.cpp:153).
            # Dropping the code left a bare FAILED with nothing to diagnose from.
            reason_code = parsed.get('reasonCode')
            vehicle_id = event.get('vehicleId', '')
        else:
            # Legacy JSON path
            command_id = event.get('commandId')
            status = event.get('status', 'UNKNOWN')
            vehicle_id = event.get('vehicleId', '')
            reason = event.get('reason', '')
            reason_code = None

        if not command_id:
            print(f"⚠️ No commandId in response: {json.dumps(event)}")
            return

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

        if reason_code is not None:
            update_expr += ', reasonCode = :rc'
            expr_values[':rc'] = int(reason_code)

        # Calculate latency
        try:
            item = COMMANDS_TABLE.get_item(Key={'commandId': command_id}).get('Item')
            if item and item.get('timestamp'):
                latency_ms = int(now.timestamp() * 1000) - int(item['timestamp'])
                update_expr += ', latencyMs = :lat'
                expr_values[':lat'] = latency_ms
        except Exception:
            pass

        # Two independent responders can answer the same command on an FWE-path
        # vehicle, and they disagree:
        #   * the fwe-simulator answers JSON on cms/commands/<vehicleId>/response
        #     with SUCCEEDED — it is the component that actually actuates (it
        #     mutates VehicleState and writes the CAN frame the agent decodes);
        #   * the FWE agent answers protobuf on
        #     cms/commands/things/<VIN>/executions/<id>/response/protobuf with
        #     FAILED / reasonCode 3 (NO_DECODING_RULES_FOUND), because FWE-native
        #     actuation is not wired yet (see the issue's § D3).
        # The agent is typically slower, so last-write-wins would overwrite a
        # truthful SUCCEEDED with a spurious FAILED and the UI would report a
        # failure for a door that did lock. Before 2026-08-04 this could not
        # happen because the protobuf request was mis-addressed and no agent ever
        # replied (§ D1); fixing the address exposed the race.
        # Rule: a non-SUCCEEDED response may not move a command out of SUCCEEDED.
        condition = None
        if status != 'SUCCEEDED':
            condition = 'attribute_not_exists(#s) OR #s <> :succeeded'
            expr_values[':succeeded'] = 'SUCCEEDED'

        try:
            kwargs = dict(
                Key={'commandId': command_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
            if condition:
                kwargs['ConditionExpression'] = condition
            COMMANDS_TABLE.update_item(**kwargs)
        except COMMANDS_TABLE.meta.client.exceptions.ConditionalCheckFailedException:
            print(f"↩️  Ignored {status} response for {command_id} — already SUCCEEDED "
                  f"(reasonCode: {reason_code}). A slower non-success responder must not "
                  f"overwrite a completed command.")
            return

        print(f"✅ Command {command_id} → {status} for {vehicle_id} "
              f"(reason: {reason!r}, reasonCode: {reason_code})")

    except Exception as e:
        print(f"❌ Error processing command response: {e}")
        raise

"""
OEM1 CheckpointStore — DynamoDB-backed, AFTER-only, post-MSK-ack.

Only AFTER references are saved (LATEST/EARLIEST/AT_TIMESTAMP raise ValueError).
Checkpoints are written only after MSK ack (msk_acked=True).
Key: (flow: bytes, shard_id: bytes) → opaque reference bytes.
"""
import base64
import enum
import os

import boto3

from config import CHECKPOINT_TABLE


class ReferenceType(enum.Enum):
    AFTER = "AFTER"
    LATEST = "LATEST"
    EARLIEST = "EARLIEST"
    AT_TIMESTAMP = "AT_TIMESTAMP"


class CheckpointStore:
    def __init__(self, table_name: str = CHECKPOINT_TABLE):
        self._table_name = table_name
        self._memory: dict[tuple, bytes] = {}  # in-process cache / test backing store

    def save(
        self,
        flow: bytes,
        shard_id: bytes,
        reference: bytes,
        reference_type: ReferenceType,
        msk_acked: bool,
    ) -> None:
        if reference_type != ReferenceType.AFTER:
            raise ValueError(
                f"Only AFTER references may be saved; got {reference_type.value}. "
                "Saving LATEST/EARLIEST/AT_TIMESTAMP violates checkpoint integrity."
            )
        if not msk_acked:
            return  # do not persist until MSK ack confirmed
        self._memory[(flow, shard_id)] = reference
        self._write_to_ddb(flow, shard_id, reference)

    def load(self, flow: bytes, shard_id: bytes) -> bytes | None:
        if (flow, shard_id) in self._memory:
            return self._memory[(flow, shard_id)]
        return self._read_from_ddb(flow, shard_id)

    def delete(self, flow: bytes, shard_id: bytes) -> None:
        self._memory.pop((flow, shard_id), None)
        key = self._make_key(flow, shard_id)
        try:
            region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
            ddb = boto3.client("dynamodb", region_name=region)
            ddb.delete_item(
                TableName=self._table_name,
                Key={"checkpoint_key": {"S": key}},
            )
        except Exception:
            pass

    def _write_to_ddb(self, flow: bytes, shard_id: bytes, reference: bytes) -> None:
        """Persist checkpoint to DynamoDB. Best-effort — in-memory store already updated."""
        key = self._make_key(flow, shard_id)
        region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
        try:
            ddb = boto3.client("dynamodb", region_name=region)
            ddb.put_item(
                TableName=self._table_name,
                Item={
                    "checkpoint_key": {"S": key},
                    "reference": {"B": reference},
                },
            )
        except Exception as exc:
            # D4 debug: was silently swallowed before; surface so we can diagnose.
            import logging
            logging.getLogger("oem1.checkpoint_store").warning(
                "ddb put_item failed for shard %s: %s: %s",
                key[:16], type(exc).__name__, exc,
            )

    def _read_from_ddb(self, flow: bytes, shard_id: bytes) -> bytes | None:
        """Read checkpoint from DynamoDB (used on cold start when in-memory cache is empty)."""
        key = self._make_key(flow, shard_id)
        region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
        try:
            ddb = boto3.client("dynamodb", region_name=region)
            resp = ddb.get_item(
                TableName=self._table_name,
                Key={"checkpoint_key": {"S": key}},
            )
            item = resp.get("Item")
            if item:
                ref = item["reference"]["B"]
                return ref if isinstance(ref, bytes) else base64.b64decode(ref)
        except Exception:
            pass
        return None

    @staticmethod
    def _make_key(flow: bytes, shard_id: bytes) -> str:
        return f"{flow.hex()}#{shard_id.hex()}"

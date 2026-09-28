"""
source_dispatch.py — pure dispatch from product source → query descriptor.

No I/O, no boto3, no os.environ. The caller resolves the returned keys.

D8 (2026-09-12): Collapses the previous two parallel maps (_SOURCE_TO_ENV,
_SOURCE_TO_KEY_FIELD) into one descriptor per source, carrying env_var,
key_field, index_name, sort_key, and sort_key_kind. Adding a new source now
requires ONE place, so a partially-specified source is a construction-time
error rather than a runtime ValidationException. The two thin-wrapper
functions (resolve_source_table / resolve_source_key_field) are retained
unchanged so existing callers and tests keep passing without modification.
"""

from __future__ import annotations

from typing import Literal, NamedTuple


class UnknownSourceError(Exception):
    """Raised when a product's 'source' value has no known table mapping."""


class SourceDescriptor(NamedTuple):
    """All the query-time information needed for a single data source.

    Fields
    ------
    env_var       : Name of the env var that holds the DynamoDB table name.
    key_field     : Partition-key attribute name used in KeyConditionExpression.
    index_name    : GSI name to pass as ``IndexName``, or ``None`` for a
                    base-table query.
    sort_key      : Sort-key attribute name (e.g. ``"timestamp"``).
    sort_key_kind : ``"epoch_ms"`` when the sort key is a 13-digit epoch-ms
                    NUMBER (same semantics as the telemetry table); ``"iso8601"``
                    for a string sort key.  Callers that need to apply a ``since``
                    filter dispatch on this field.
    """

    env_var: str
    key_field: str
    index_name: str | None
    sort_key: str
    sort_key_kind: Literal["epoch_ms", "iso8601"]


# ---------------------------------------------------------------------------
# Per-source descriptors (the single source of truth for dispatch)
# ---------------------------------------------------------------------------
#
# Verified live before encoding:
#
# telemetry — cms-staging-storage-telemetry
#   base-table query, PK=vehicleId (NUMBER sort key `timestamp`, epoch-ms)
#
# diagnostics — cms-staging-storage-maintenance-alerts (T4.1)
#   GSI vehicleId-timestamp-index, PK=vehicleId, SK=timestamp (NUMBER, epoch-ms)
#   Base table PK is alertId; the records handler MUST use the GSI.
#   vehicleId column holds BOTH raw VINs (e.g. "1FTBR3X86LKA47666") and
#   vehicleIds (e.g. "VEH-MICH-001") — see D8 / the coder must handle both.
#
# charging — cms-staging-storage-charging-sessions (T4.2)
#   base-table query, PK=vehicleId (STRING sort key `sessionStartTime`, ISO-8601)
#   Verified live: VEH-VO-001 rows carry keys like "2025-01-15T10:30:00+00:00".
#   No GSI needed — the PK is the vehicleId directly.
#
# "cs-meridian" REMOVED 2026-09-19 — was the side-loading Pattern-2 pipeline
# (spec 2026-09-11-cms-cs-meridian-ingestion), which wrote directly into
# cms-storage-telemetry, bypassing the trip/safety/maintenance/geofence
# Flink chain entirely (docs/data-products-design.md §6.3 "No side-loading").
# The correct Meridian pipeline reaches CMS canonical via Kafka
# (cs-product-meridian-ev -> OEMTelemetryProcessor -> cms-telemetry-
# preprocessed) and needs no entry here — subscription_records never reads
# that path; OEMTelemetryProcessor does, independently of this dispatch
# table. If a product's `source` field is ever `cs-meridian` again after
# this removal, resolve_source_descriptor correctly raises
# UnknownSourceError rather than silently misrouting.
#
_SOURCES: dict[str, SourceDescriptor] = {
    "telemetry": SourceDescriptor(
        env_var="TELEMETRY_TABLE_NAME",
        key_field="vehicleId",
        index_name=None,
        sort_key="timestamp",
        sort_key_kind="epoch_ms",
    ),
    "diagnostics": SourceDescriptor(
        env_var="MAINTENANCE_ALERTS_TABLE_NAME",
        key_field="vehicleId",
        index_name="vehicleId-timestamp-index",
        sort_key="timestamp",
        sort_key_kind="epoch_ms",
    ),
    "charging": SourceDescriptor(
        env_var="CHARGING_SESSIONS_TABLE_NAME",
        key_field="vehicleId",
        index_name=None,
        sort_key="sessionStartTime",
        sort_key_kind="iso8601",
    ),
}


# ---------------------------------------------------------------------------
# Primary API
# ---------------------------------------------------------------------------

def resolve_source_descriptor(product: dict) -> SourceDescriptor:
    """Return the full query descriptor for *product*'s source.

    Args:
        product: A dict that must contain a ``source`` key.

    Returns:
        ``SourceDescriptor`` for that source.

    Raises:
        UnknownSourceError: If the ``source`` key is absent or its value is
                            not a recognised source identifier.
    """
    source = product.get("source")
    if source is None:
        raise UnknownSourceError(
            f"product is missing the 'source' key: {product!r}"
        )
    descriptor = _SOURCES.get(source)
    if descriptor is None:
        raise UnknownSourceError(
            f"unknown product source {source!r}; "
            f"known sources: {list(_SOURCES)}"
        )
    return descriptor


# ---------------------------------------------------------------------------
# Thin wrappers — retained for backward compatibility with existing callers
# ---------------------------------------------------------------------------
#
# These wrappers keep the single-value API that the records handler + all
# current tests use. Meridian's own tests call ``resolve_source_key_field``
# directly, and the sync-guard test in test_source_dispatch.py checks that
# every source has BOTH an env var and a key field — both still pass because
# the descriptor contains both fields by construction.

def resolve_source_table(product: dict) -> str:
    """Return the env-var name that holds the DynamoDB table for *product*.

    Thin wrapper over `resolve_source_descriptor` retained so existing callers
    (subscription_records handler, subscription_records tests) keep working
    unchanged.

    Raises:
        UnknownSourceError: propagated from ``resolve_source_descriptor``.
    """
    return resolve_source_descriptor(product).env_var


def resolve_source_key_field(product: dict) -> str:
    """Return the DynamoDB partition-key field name for *product*'s source.

    Thin wrapper over `resolve_source_descriptor`. Callers building a
    ``KeyConditionExpression`` must use this value to name the key attribute.

    Raises:
        UnknownSourceError: propagated from ``resolve_source_descriptor``.
    """
    return resolve_source_descriptor(product).key_field

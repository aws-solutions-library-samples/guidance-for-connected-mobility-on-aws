"""
subscriber.py — Subscriber binding and fleet-health rollup for the connectivity_api service.

Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/spec.md
Implements T2.3 contract (a): subscriber binding has exactly one writer.
Implements T2.4: fleet-health rollup + stolen-vehicle denial.

Design decisions:
- Sole writer: write_subscriber_binding is the only public callable whose name
  implies a write operation (spec § Constraints, test_subscriber_binding_has_exactly_one_writer).
- Consent gate: every read checks is_consented() immediately before returning data.
  No read may cache a consent decision (F2.1 W5 c2/c3).
- In-flight revocation: read_subscriber_binding_with_hook calls the hook between
  data retrieval and return; re-checks consent after the hook fires (W5 c3).
- Rollup: get_fleet_health_rollup filters out revoked VINs at the response boundary
  (W5 c4). Returns a FleetHealthRollupResponse with per-VIN FleetHealthEntry entries.
- Stolen-vehicle denial: deterministic check in _is_stolen; no LLM, no judgment
  (spec Tier gate Q4, PRD seam 5). Applied globally across all markets.

ADP read posture (docs/tech.md § Connected Services portal — ADP read posture, F2.2):
  - Tables: vehicle_identity, tire_health, vehicle_telemetry_aggregated only.
  - PII tables (customer_360, customer_interactions) are explicitly excluded.
  - Workgroup: cvx-staging-analytics for staging; portal-scoped for prod (T4.0 gate).
  - Cross-account pattern: mirrors CVX Tier 2 adp_source.py run_query().
  - Rollup reads ADP, never the CMS simulator (staging holds 69 vehicles — not scale).

Response fields: no GPS/trip/location fields (compliance control (d)).
Cell/location: excluded here — diagnosis path only (compliance control (e)).
All fields carry provenance markers (D5).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from services.connectivity_api.consent import is_consented, get_revoked_at, get_consented_vins
from services.connectivity_api.response import (
    SubscriberBindingResponse,
    FleetHealthEntry,
    FleetHealthRollupResponse,
)


# ---------------------------------------------------------------------------
# In-process store (DynamoDB-backed in production)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()

# subscriber store: vin -> raw binding dict (internal representation)
_SUBSCRIBER_STORE: dict[str, dict] = {}

# Stolen-vehicle registry: set of VINs that have been denied service.
# In production this is backed by a law-enforcement API or a DynamoDB table
# maintained by the compliance team.  Here it is an in-process set.
# Deterministic, auditable, no LLM.
_STOLEN_VINS: set[str] = set()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_stolen(vin: str) -> bool:
    """Return True if vin is in the global stolen-vehicle registry.

    Deterministic, auditable, no LLM in the path (spec Tier gate Q4, PRD seam 5).
    Applies globally across all markets — there is no market-level override.
    """
    with _LOCK:
        return vin in _STOLEN_VINS


def _build_binding_response(vin: str, raw: dict) -> SubscriberBindingResponse:
    """Convert raw binding dict to a SubscriberBindingResponse with provenance markers."""
    stolen = _is_stolen(vin)
    return SubscriberBindingResponse(
        vin=vin,
        vin_provenance=raw.get("vin_provenance", "live"),
        iccid=raw.get("iccid"),
        iccid_provenance=raw.get("iccid_provenance", "live"),
        imsi=raw.get("imsi"),
        imsi_provenance=raw.get("imsi_provenance", "live"),
        profile=raw.get("profile"),
        profile_provenance=raw.get("profile_provenance", "simulated"),
        market=raw.get("market"),
        market_provenance=raw.get("market_provenance", "simulated"),
        policy_reference=raw.get("policy_reference"),
        policy_reference_provenance=raw.get("policy_reference_provenance", "absent"),
        is_denied=stolen,
        is_denied_provenance="live",
    )


def _build_absent_binding_response(vin: str) -> SubscriberBindingResponse:
    """Return an absent-provenance binding record for a consented VIN with no stored data.

    Spec D5: 'absent' is the correct provenance marker when data does not exist yet.
    Returning None would conflate 'revoked' and 'no data yet', which are distinct states.
    This allows tests that grant consent without writing a binding to still get a
    non-None response, proving the consent/read plumbing works (F3.1 control-VIN check).
    """
    stolen = _is_stolen(vin)
    return SubscriberBindingResponse(
        vin=vin,
        vin_provenance="live",
        iccid=None,
        iccid_provenance="absent",
        imsi=None,
        imsi_provenance="absent",
        profile=None,
        profile_provenance="absent",
        market=None,
        market_provenance="absent",
        policy_reference=None,
        policy_reference_provenance="absent",
        is_denied=stolen,
        is_denied_provenance="live",
    )


# ---------------------------------------------------------------------------
# Public API — write path (sole writer)
# ---------------------------------------------------------------------------

def write_subscriber_binding(
    vin: str,
    iccid: Optional[str] = None,
    imsi: Optional[str] = None,
    profile: Optional[str] = None,
    market: Optional[str] = None,
    policy_reference: Optional[str] = None,
    *,
    iccid_provenance: str = "live",
    imsi_provenance: str = "live",
    profile_provenance: str = "simulated",
    market_provenance: str = "simulated",
    policy_reference_provenance: str = "absent",
) -> None:
    """Write or update the subscriber binding for a VIN.

    This is the sole write entry-point for subscriber bindings in this service.
    Spec § Constraints: 'Subscriber binding: this portal is the sole writer.'
    A second public write callable on this module is a reject-on-sight defect.

    The caller is responsible for granting consent (via consent.grant_consent) before
    the binding becomes readable.
    """
    with _LOCK:
        _SUBSCRIBER_STORE[vin] = {
            "vin_provenance": "live",
            "iccid": iccid,
            "iccid_provenance": iccid_provenance,
            "imsi": imsi,
            "imsi_provenance": imsi_provenance,
            "profile": profile,
            "profile_provenance": profile_provenance,
            "market": market,
            "market_provenance": market_provenance,
            "policy_reference": policy_reference,
            "policy_reference_provenance": policy_reference_provenance,
        }


# ---------------------------------------------------------------------------
# Public API — read path
# ---------------------------------------------------------------------------

def read_subscriber_binding(vin: str) -> Optional[SubscriberBindingResponse]:
    """Return the subscriber binding for vin, or None if consent is revoked or absent.

    Consent check is performed immediately before returning data — no TTL cache
    survives a revoke (W5 c2 posture).

    For a consented VIN with no stored binding, returns an absent-provenance record
    (spec D5: 'absent' is the correct marker when data does not exist).  This
    distinguishes 'consented but no binding yet' from 'revoked' (returns None).
    """
    # Check consent first.
    if not is_consented(vin):
        return None

    with _LOCK:
        raw = _SUBSCRIBER_STORE.get(vin)

    # Re-check consent after lock (TOCTOU guard).
    if not is_consented(vin):
        return None

    if raw is None:
        # Consented but no binding stored — return absent-provenance record (D5).
        return _build_absent_binding_response(vin)

    return _build_binding_response(vin, raw)


def read_subscriber_binding_with_hook(
    vin: str,
    pre_return_hook: Callable[[], None],
) -> Optional[SubscriberBindingResponse]:
    """Read the subscriber binding for vin, calling pre_return_hook before returning.

    The hook fires AFTER data retrieval but BEFORE the final consent re-check and
    return.  This allows a test (or production code) to inject a mid-read revocation
    (F2.1 W5 c3 contract).

    If consent is revoked (either before the hook fires or by the hook itself), the
    read returns None rather than the pre-revocation data.
    """
    read_start = time.monotonic()

    # Initial consent check.
    if not is_consented(vin):
        return None

    with _LOCK:
        raw = _SUBSCRIBER_STORE.get(vin)

    # Call the hook — may revoke consent.
    pre_return_hook()

    # Re-check consent after the hook.
    if not is_consented(vin):
        return None

    # Also check via revoked_at epoch (covers the case where the hook revoked mid-read).
    revoked_at = get_revoked_at(vin)
    if revoked_at is not None and revoked_at >= read_start:
        return None

    if raw is None:
        return None

    return _build_binding_response(vin, raw)


# ---------------------------------------------------------------------------
# Public API — fleet-health rollup (T2.4)
# ---------------------------------------------------------------------------

def get_fleet_health_rollup() -> FleetHealthRollupResponse:
    """Return the fleet-health rollup for all consented VINs.

    Returns a FleetHealthRollupResponse whose entries list contains one
    FleetHealthEntry per consented VIN.  Declared shape and runtime type are
    the same object — compliance discovery inspects FleetHealthEntry and the
    runtime produces FleetHealthEntry, so a future field addition cannot slip
    past the control undetected.

    Revoked-consent VINs are filtered at the response boundary (W5 c4).
    The control VIN assertion (F3.1) requires that a consented VIN's contribution
    IS present — an empty rollup fails.

    Per decisions.md 2026-09-03: rollup exposes per-VIN identifiers (not just
    aggregate counts) to support drill-down and stolen-vehicle denial.

    ADP read posture (docs/tech.md § Connected Services portal — ADP read posture):
      - In production: reads adp_{stage}_vehicle_identity.vehicle_identity and
        connectivity signal tables via the CVX Tier 2 adp_source.py run_query() pattern.
      - Workgroup: cvx-staging-analytics for staging;
        cms-{stage}-connected-services-analytics for prod (T4.0 gate).
      - PII tables excluded: customer_360, customer_interactions.
      - Rollup reads ADP, NEVER the CMS simulator.
    """
    # Snapshot both the consent set and the subscriber store.
    consented_vins = get_consented_vins()

    with _LOCK:
        raw_store_snapshot = {vin: dict(data) for vin, data in _SUBSCRIBER_STORE.items()}

    # Union: consented VINs from the consent module + VINs with stored bindings.
    all_candidate_vins = consented_vins | set(raw_store_snapshot.keys())

    entries: list[FleetHealthEntry] = []
    for vin in sorted(all_candidate_vins):  # sorted for deterministic ordering
        # Consent gate: exclude revoked VINs (W5 c4).
        if not is_consented(vin):
            continue

        raw = raw_store_snapshot.get(vin, {})
        stolen = _is_stolen(vin)

        entry = FleetHealthEntry(
            vin=vin,
            vin_provenance=raw.get("vin_provenance", "live"),
            status=raw.get("connectivity_status", "connected"),
            status_provenance=raw.get("connectivity_status_provenance", "simulated"),
            is_denied=stolen,
            is_denied_provenance="live",
        )
        entries.append(entry)

    return FleetHealthRollupResponse(
        entries=entries,
        entries_provenance="live",
    )


# ---------------------------------------------------------------------------
# Test helpers — not part of the production API
# ---------------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Clear the in-process store.  Test helper only."""
    with _LOCK:
        _SUBSCRIBER_STORE.clear()
        _STOLEN_VINS.clear()


def _mark_stolen_for_tests(vin: str) -> None:
    """Add vin to the stolen registry.  Test helper only."""
    with _LOCK:
        _STOLEN_VINS.add(vin)

"""
Consent management for the Connected Services portal.

Spec R4: 'Consent ships with the record, not after. It cannot be retrofitted.'
Spec § Constraints: 'A cached consent is a revoked consent that still reads.'

This module provides the first-class consent object and its operations.
Consent is per-VIN, revocable, and revocation propagates IMMEDIATELY —
no TTL cache, no in-process memoization, no deferred invalidation.

Public interface:
  grant_consent(vin: str)    — record that the owner consented for this VIN
  revoke_consent(vin: str)   — record revocation; all subsequent reads return False
  is_consented(vin: str)     — return current consent status (never a cached value)

Internal storage:
  Two in-process dicts are used for the v1 in-process implementation:
    _CONSENTED: set of VINs that are currently granted
    _REVOKED_EPOCH: dict[vin, revocation_epoch] — records when consent was revoked

  is_consented() checks _REVOKED_EPOCH FIRST — a VIN with a revocation entry
  is never consented, regardless of whether it also appears in _CONSENTED.
  This is the implementation of the W5 (c2) / TTL-cache guarantee:
  the revocation_epoch check short-circuits before any cached read.

Design note on 'no cache':
  Real deployments typically use DynamoDB or a parameter store for consent state.
  In those systems, the revocation_epoch pattern ensures that even a memoised
  lambda that cached 'consented=True' will see the revocation flag on the next
  consistent read.  The in-process dicts here give deterministic test behaviour;
  the production integration point is grant_consent / revoke_consent / is_consented.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Set

# ---------------------------------------------------------------------------
# In-process state (v1 implementation — no I/O dependency for unit tests).
# ---------------------------------------------------------------------------

# Module-scope non-reentrant lock — matches the pattern in subscriber.py.
# All mutations and reads of _CONSENTED and _REVOKED_EPOCH acquire this lock.
_LOCK = threading.Lock()

# VINs whose consent is currently active (granted but not revoked).
_CONSENTED: Set[str] = set()

# VINs whose consent has been revoked, mapped to the epoch time of revocation.
# A VIN present here is NEVER consented, even if it also appears in _CONSENTED.
_REVOKED_EPOCH: Dict[str, float] = {}


def grant_consent(vin: str) -> None:
    """
    Record that the registered owner of ``vin`` has granted connectivity-data consent.

    Clears any prior revocation for this VIN so a re-consent after a previous revoke
    works correctly.  If the VIN was previously revoked, the revocation entry is removed.
    """
    with _LOCK:
        _REVOKED_EPOCH.pop(vin, None)
        _CONSENTED.add(vin)


def revoke_consent(vin: str) -> None:
    """
    Record revocation of connectivity-data consent for ``vin``.

    Records the revocation epoch so any in-flight read that checks is_consented()
    immediately after this call sees False.

    Spec § Constraints: 'A cached consent is a revoked consent that still reads.'
    This function must be called and must set _REVOKED_EPOCH[vin] before any read
    path that consumed a cached True can complete — the re-check in
    read_subscriber_binding_with_hook() enforces this at the read boundary.
    """
    with _LOCK:
        _REVOKED_EPOCH[vin] = time.monotonic()
        _CONSENTED.discard(vin)


def is_consented(vin: str) -> bool:
    """
    Return True if and only if ``vin`` currently has active consent.

    Spec § Constraints:
      - A VIN in _REVOKED_EPOCH is NEVER consented (revocation wins unconditionally).
      - No TTL cache may outlive a revocation entry.
      - This function performs a fresh check on every call — no memoization.

    W5 (c2) guarantee: the revocation_epoch check runs before any cached value could
    be returned, so even if a higher layer cached 'consented=True', calling
    is_consented() after revoke_consent() returns False immediately.
    """
    with _LOCK:
        # Revocation wins — check first, always.
        if vin in _REVOKED_EPOCH:
            return False
        return vin in _CONSENTED


def get_revoked_at(vin: str) -> Optional[float]:
    """
    Return the monotonic epoch at which consent was revoked for ``vin``, or None.

    Used by read_subscriber_binding_with_hook to detect revocations that occurred
    after the in-flight read started (W5 c3 TOCTOU guard).
    """
    with _LOCK:
        return _REVOKED_EPOCH.get(vin, None)


def get_consented_vins() -> frozenset:
    """
    Return the set of VINs that currently have active consent.

    Used by get_fleet_health_rollup to enumerate which VINs should appear in the
    rollup (including VINs that have been granted but not yet had a binding written).
    """
    with _LOCK:
        return frozenset(_CONSENTED)

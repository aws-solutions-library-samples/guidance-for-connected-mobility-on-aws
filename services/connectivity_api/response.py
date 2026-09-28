"""
response.py — Response-shape provenance validation for the connectivity_api service.

Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/spec.md § D5
Implements T2.3 contract (f): every response field carries a live|simulated|absent marker.

Design:
- VALID_PROVENANCE_MARKERS — the exact set D5 specifies.  'measured' is valid in
  fleet_intelligence but NOT here (spec D5: "There is no fourth case.").
- validate_response_provenance — verifies that every non-provenance key in a response
  dict has a corresponding '<key>_provenance' entry whose value is in VALID_PROVENANCE_MARKERS.

Constraints (spec D5):
  - Exactly three marker values: live | simulated | absent.
  - No component may render a value whose marker it did not receive.
  - 'absent' renders an explicit empty state (not null-rendered-as-blank).
  - There is no fourth provenance case.

Response shapes used by this service — all must be dataclasses, TypedDict subclasses,
or Pydantic BaseModel subclasses so _discover_response_shapes() picks them up.
None may contain trip/GPS/location fields (controls (d) and (e) in test_contracts.py).
"""
from __future__ import annotations

import dataclasses
from typing import Optional


# D5: exactly three values.  No fourth case.
VALID_PROVENANCE_MARKERS: frozenset[str] = frozenset({"live", "simulated", "absent"})

# The suffix appended to a field name to form its provenance key.
_PROVENANCE_SUFFIX = "_provenance"


def validate_response_provenance(response: dict) -> None:
    """Validate that every data field in *response* has a valid provenance marker.

    Rules:
      1. For every key K that does not end with '_provenance', there must be a key
         K + '_provenance' present in the dict.
      2. The value of K + '_provenance' must be in VALID_PROVENANCE_MARKERS.

    Raises ValueError if either rule is violated.

    D5: 'No component may render a value whose marker it did not receive.'
    """
    data_keys = [k for k in response if not k.endswith(_PROVENANCE_SUFFIX)]
    for key in data_keys:
        provenance_key = key + _PROVENANCE_SUFFIX
        if provenance_key not in response:
            raise ValueError(
                f"Response field '{key}' has no provenance marker '{provenance_key}'. "
                f"Spec D5: every field must carry a live|simulated|absent marker."
            )
        marker = response[provenance_key]
        if marker not in VALID_PROVENANCE_MARKERS:
            raise ValueError(
                f"Response field '{key}' has invalid provenance marker '{marker}'. "
                f"Valid markers: {sorted(VALID_PROVENANCE_MARKERS)}. "
                f"Spec D5: 'There is no fourth case.' ('measured' is valid in "
                f"fleet_intelligence but NOT in the connected-services portal.)"
            )


# ---------------------------------------------------------------------------
# Response shapes — compliant with controls (d) and (e).
# No GPS/trip/location fields; all fields carry provenance markers.
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SubscriberBindingResponse:
    """Full subscriber binding for a single VIN.

    Spec § Design: 'VIN ↔ ICCID ↔ IMSI ↔ profile ↔ market ↔ policy refs' as one record.
    All fields carry provenance markers (D5).

    Fields deliberately excluded (control (d)):
      - No GPS/latitude/longitude/location field.
      - No trip/route/journey/waypoint field.
      - No odometer/mileage field.

    Cell/network location fields are excluded here; they appear only in DiagnosisResponse
    (control (e) — diagnosis path only).
    """
    vin: str
    vin_provenance: str              # live | simulated | absent
    iccid: Optional[str]
    iccid_provenance: str
    imsi: Optional[str]
    imsi_provenance: str
    profile: Optional[str]
    profile_provenance: str
    market: Optional[str]
    market_provenance: str
    policy_reference: Optional[str]
    policy_reference_provenance: str
    # Stolen-vehicle denial flag — deterministic, set by the denial seam (T2.4).
    # Not a GPS field.  Absence of the vehicle-theft status is fine (absent provenance).
    is_denied: bool
    is_denied_provenance: str


@dataclasses.dataclass
class FleetHealthEntry:
    """Per-VIN fleet-health record in the rollup.

    Carries connectivity status and provenance for each contributing VIN.
    Subject to consent: a VIN with revoked consent is fully absent from the rollup.

    Decisions.md 2026-09-03: "the rollup shape includes per-VIN entries … not just
    aggregate counts" — necessary for stolen-vehicle denial drill-down and for the
    F3.1 control-VIN assertion.

    No GPS/trip/location fields (control (d)).
    Cell/network location excluded — diagnosis path only (control (e)).
    """
    vin: str
    vin_provenance: str              # live | simulated | absent
    status: str                      # "connected" | "degraded" | "ntn_fallback" | "unreachable"
    status_provenance: str
    # Denial flag — deterministic seam (T2.4 spec constraint Q4, PRD seam 5).
    is_denied: bool
    is_denied_provenance: str


@dataclasses.dataclass
class FleetHealthRollupResponse:
    """Fleet-health rollup — population-scale view.

    Contains per-VIN entries (list[FleetHealthEntry]) for all consented VINs.
    Revoked-consent VINs are fully absent — not zeroed, not listed as 'absent'.

    Decisions.md 2026-09-03: 'A VIN whose consent is revoked is fully absent from
    the rollup, not zeroed.'

    No GPS/trip/location fields.  No opaque (dict[str,Any]) fields.
    """
    entries: list   # list[FleetHealthEntry]; typed as list to avoid Generic issues
    entries_provenance: str          # live | simulated | absent

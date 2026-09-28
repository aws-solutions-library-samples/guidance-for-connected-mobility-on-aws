"""
diagnosis.py — Connectivity-diagnosis response shapes for the connectivity_api service.

Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/spec.md § Constraints
Implements T2.4 compliance control (e):
  'Network-derived cell location is permitted for connectivity diagnosis only
  and must not become a location backdoor. Also test-enforced.'

DiagnosisResponse is the ONLY response shape in this service that carries a
cell-location field.  All other response shapes live in response.py and must
NOT carry any cell/location field (test_cell_location_only_on_diagnosis_path
enforces this via discovery).

How test_cell_location_only_on_diagnosis_path identifies DiagnosisResponse:
  - The test imports this module via _import_diagnosis() which resolves
    services.connectivity_api.diagnosis.
  - It then calls _get(diagnosis_mod, "DiagnosisResponse") to obtain the class
    by name.
  - The class is identified by module location (services.connectivity_api.diagnosis)
    and class name ("DiagnosisResponse") — not by a registry.
  - Discovery (_discover_response_shapes) walks the full package, finds
    DiagnosisResponse, and the test EXCLUDES it by identity (s is not diagnosis_shape)
    when checking non-diagnosis shapes for cell fields.

Cell-location fields allowed here (CELL_TERMS from test_contracts.py):
  cell, cell_id, cell_location, mcc, mnc, lac, rnc, enodeb, gnodeb,
  tower, signal_location, network_location.

All provenance markers D5-compliant (live | simulated | absent).
No GPS/trip fields — control (d) still applies to this shape.
"""
from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass
class DiagnosisResponse:
    """Connectivity-diagnosis result for a single VIN.

    The ONLY response shape in services.connectivity_api that carries
    a cell-location field.  All cell/network-location data is confined to
    this shape (spec § Constraints, control (e)).

    Cell fields present:
      cell_id — the serving cell identifier (e.g. eNodeB / gNodeB global ID)
      mcc     — Mobile Country Code (3 digits)
      mnc     — Mobile Network Code (2–3 digits)

    These three fields are the minimum necessary to identify the serving cell
    for connectivity diagnosis. Raw geographic coordinates are excluded (no
    lat/lon/GPS fields — control (d) also applies).

    Provenance markers: all fields carry live|simulated|absent per D5.
    """
    vin: str
    vin_provenance: str

    # Connectivity status at time of diagnosis.
    status: str                      # "connected" | "degraded" | "unreachable"
    status_provenance: str

    # Cell-location fields — diagnosis path only.
    # CELL_TERMS match: cell_id (cell), mcc, mnc.
    cell_id: Optional[str]           # serving cell ID; e.g. "12345678"
    cell_id_provenance: str
    mcc: Optional[str]               # Mobile Country Code; e.g. "310"
    mcc_provenance: str
    mnc: Optional[str]               # Mobile Network Code; e.g. "260"
    mnc_provenance: str

    # Diagnostic notes — free text, no structured location.
    diagnostic_notes: Optional[str]
    diagnostic_notes_provenance: str

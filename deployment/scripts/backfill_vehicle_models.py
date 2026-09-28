# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vehicle model assignment, brand conversion, and body-style normalisation.

Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform/spec.md`
      T5.1a (vehicleType normaliser, F12), T5.4 (model assignment + brand conversion,
      D24, C10, C12, C14).

DELIVERY SPLIT
--------------
T5.1a ships `normalise_vehicle_type` — the read-time normaliser that collapses the 8
distinct casings of 4 body styles measured in live staging data (F12). The data-fix
backfill is a separate script (`normalise_vehicle_type_backfill.py`), following the
same 2-phase / verify-live discipline as `consolidate_default_model_manifest.py`.

T5.4 ships `assign_model_line`, `convert_fleet_brands`, `resolve_diagnosis_state`, and
the `run()` CLI entrypoint that drives the full model-assignment and brand-conversion
migration (D24, C10, C12, C14).

vehicleType NORMALISATION (F12)
--------------------------------
Live staging `cms-staging-storage-vehicles` measured 2026-09-03:

    Pickup ×2 / pickup ×1  →  'pickup'
    SUV ×7    / suv ×2     →  'suv'
    Sedan ×2  / sedan ×4   →  'sedan'
    Van ×1    / van ×2     →  'van'
    (absent) ×48           →  ''

Eight distinct casings for four body styles. D24 keys model assignment on
body style + powertrain; a casing mismatch silently skips the vehicle, and the
backfill reports success while assigning nothing — the exact failure mode § R9
warns about and the 'Brand scrub gap' demonstrates.

Canonical set (4 values): 'pickup', 'suv', 'sedan', 'van'

The normalisation rule is `.strip().lower()`. No synonyms are needed for body
styles (unlike `signal_group`, where 'Engine'/'powertrain' are different words that
mean the same domain). This mirrors `normalise_signal_group` in
`services/simulation/signal_attribution.py` — same strip+lower pattern, same
location relative to the function that consumes it.

NEGATIVE CONTROL REQUIREMENT (DX32)
-------------------------------------
DX32 requires an explicit negative control: an un-normalised lookup must be shown
to miss rows a normalised one finds, demonstrating that the defect would persist
without normalisation. See `tests/test_vehicle_brand_conversion.py:test_dx32_*`.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Import _classify_vehicle_local from backfill_vehicle_classification.
# This is the single source of classification derivation logic — never copy it.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from backfill_vehicle_classification import (  # noqa: E402
    _classify_vehicle_local,
    _ddb_str,
    _scan_all_vehicles,
)

# ---------------------------------------------------------------------------
# powertrain_profiles — fuel-type to profile label mapping.
# ---------------------------------------------------------------------------
from powertrain_profiles import get_profile_for_fuel_type  # noqa: E402

# ---------------------------------------------------------------------------
# Canonical body-style set (F12)
# ---------------------------------------------------------------------------

#: The 4 canonical body-style values after normalisation.
#: Every value returned by `normalise_vehicle_type` is in this set (or "").
CANONICAL_BODY_STYLES = frozenset({"pickup", "suv", "sedan", "van"})


def normalise_vehicle_type(raw: Optional[str]) -> str:
    """Canonicalise a `vehicleType` attribute value (F12).

    Strips whitespace and lowercases. Returns '' for None/blank — callers
    must treat an empty body style as unassignable rather than guessing.

    This is the single read-time normaliser for `vehicleType`. The one-time
    data-fix counterpart is `normalise_vehicle_type_backfill.py`. T5.4's
    `assign_model_line` must call this before any body-style keyed lookup;
    if it does its own lowercasing instead, F12 reappears on the next field
    that has the same defect.

    Examples
    --------
    >>> normalise_vehicle_type('SUV')
    'suv'
    >>> normalise_vehicle_type('suv')
    'suv'
    >>> normalise_vehicle_type('Pickup')
    'pickup'
    >>> normalise_vehicle_type(None)
    ''
    """
    if not raw:
        return ""
    return str(raw).strip().lower()



# ---------------------------------------------------------------------------
# Model manifest name constants — MERIDIAN-* line names per seed_model_manifests.py.
# The model manifest names are the keys in the cms-{stage}-model-manifest table.
# These strings are the canonical model-line identifiers.  They are stable.
# ---------------------------------------------------------------------------

# Mapping: (canonical_body_style, powertrain_profile) → model manifest name.
#
# Per D24: powertrain wins when body and powertrain conflict.  The only
# hybrid line is Azimuth (pickup/HYBRID), so any vehicle with fuelType=hybrid
# resolves to MERIDIAN-AZIMUTH regardless of body style.
#
# Profile labels match powertrain_profiles.py constants exactly.
# Two BEV SUV lines exist (Windrose, Trailwind); in the absence of a year
# discriminator at this layer, Trailwind is preferred (2023 is the CES demo EV
# and the majority of the staging fleet vehicles are 2023 model year).
_MODEL_LINE_MAP: Dict[Tuple[str, str], str] = {
    # EV lines
    ("suv",    "EV"):           "MERIDIAN-TRAILWIND",
    ("sedan",  "EV"):           "MERIDIAN-CRESTWIND",
    ("van",    "EV"):           "MERIDIAN-ZEPHYR",
    ("pickup", "EV"):           "MERIDIAN-WINDROSE",  # no EV pickup in D22 table;
                                                       # Windrose is the other BEV SUV
                                                       # — kept as closest fallback
    # Hybrid line — Azimuth is the ONLY hybrid line; powertrain wins in all conflicts.
    ("suv",    "HYBRID"):       "MERIDIAN-AZIMUTH",
    ("sedan",  "HYBRID"):       "MERIDIAN-AZIMUTH",
    ("van",    "HYBRID"):       "MERIDIAN-AZIMUTH",  # DX33: powertrain wins over van body
    ("pickup", "HYBRID"):       "MERIDIAN-AZIMUTH",
    # ICE gasoline lines
    ("sedan",  "ICE_GASOLINE"): "MERIDIAN-MISTRAL",
    ("suv",    "ICE_GASOLINE"): "MERIDIAN-MISTRAL",  # no ICE-gasoline SUV in D22;
                                                      # Mistral is the only gasoline line
    ("van",    "ICE_GASOLINE"): "MERIDIAN-MISTRAL",
    ("pickup", "ICE_GASOLINE"): "MERIDIAN-MISTRAL",
    # ICE diesel lines
    ("pickup", "ICE_DIESEL"):   "MERIDIAN-SIROCCO",
    ("sedan",  "ICE_DIESEL"):   "MERIDIAN-SIROCCO",  # no diesel sedan in D22;
                                                      # Sirocco is the only diesel line
    ("van",    "ICE_DIESEL"):   "MERIDIAN-SIROCCO",
    ("suv",    "ICE_DIESEL"):   "MERIDIAN-SIROCCO",
}

# D24 vehicle-ID-to-manifest explicit override table.
# Used by convert_fleet_brands and as a pre-computed lookup in assign_model_line
# when the vehicleId is one of the 9 DemoMotors/AcmeAuto rows or the 2 Meridian
# vehicles with wrong Ford model assignments.
#
# VEH-MRDN-0015 and VEH-VO-001 are Meridian vehicles that carried a Ford model
# string wrongly.  They are NOT Ford make vehicles and NOT cloud-telemetry.
# Do NOT confuse them with the 48 Ford (make='Ford') OEM1 demo vehicles.
_VEH_ID_TO_MANIFEST: Dict[str, str] = {
    "VEH-1780003031":  "MERIDIAN-AZIMUTH",     # DemoMotors Trailhead  SUV/hybrid
    "VEH-1780081115":  "MERIDIAN-AZIMUTH",     # DemoMotors Trailhead  SUV/hybrid
    "VEH-DEMO-PUB-005": "MERIDIAN-AZIMUTH",   # DemoMotors Trailhead  suv/hybrid
    "VEH-DEMO-PUB-001": "MERIDIAN-CRESTWIND", # DemoMotors Voyager    sedan/electric
    "VEH-DEMO-PUB-002": "MERIDIAN-CRESTWIND", # DemoMotors Voyager    sedan/electric
    "VEH-DEMO-PUB-003": "MERIDIAN-ZEPHYR",    # AcmeAuto Carrier      van/electric
    "VEH-DEMO-PUB-004": "MERIDIAN-AZIMUTH",   # AcmeAuto Carrier      van/hybrid (DX33)
    "VEH-ENT-001":      "MERIDIAN-MISTRAL",   # DemoMotors Voyager    sedan/gasoline
    "VEH-MICH-001":     "MERIDIAN-SIROCCO",   # AcmeAuto Carrier HD   pickup/diesel
    # Meridian vehicles with wrong Ford model assignments (per task description).
    "VEH-MRDN-0015":     "MERIDIAN-TRAILWIND", # Meridian make, wrongly Ford model
    "VEH-VO-001":       "MERIDIAN-CRESTWIND", # Meridian make, wrongly Ford model
}

# Make-to-Meridian conversion table for convert_fleet_brands.
# These are the brand substitutions for the 9 DemoMotors/AcmeAuto vehicles.
# vehicleId is NEVER rewritten (D24 / bucket B).
_BRAND_CONVERSION: Dict[str, Dict[str, str]] = {
    "VEH-1780003031":  {"make": "Meridian", "model": "Azimuth"},
    "VEH-1780081115":  {"make": "Meridian", "model": "Azimuth"},
    "VEH-DEMO-PUB-005": {"make": "Meridian", "model": "Azimuth"},
    "VEH-DEMO-PUB-001": {"make": "Meridian", "model": "Crestwind"},
    "VEH-DEMO-PUB-002": {"make": "Meridian", "model": "Crestwind"},
    "VEH-DEMO-PUB-003": {"make": "Meridian", "model": "Zephyr"},
    "VEH-DEMO-PUB-004": {"make": "Meridian", "model": "Azimuth"},
    "VEH-ENT-001":      {"make": "Meridian", "model": "Mistral"},
    "VEH-MICH-001":     {"make": "Meridian", "model": "Sirocco"},
    # Meridian vehicles with wrong Ford model strings (reassign model, keep make).
    "VEH-MRDN-0015":     {"make": "Meridian", "model": "Trailwind"},
    "VEH-VO-001":       {"make": "Meridian", "model": "Crestwind"},
}

# Model-line-name → MERIDIAN-* manifest lookup.  Used by assign_model_line to
# preserve an existing Meridian vehicle's model line rather than collapsing it
# onto a body+powertrain default. Case-insensitive on input (values are the exact
# manifest names from seed_model_manifests.py MODEL_MANIFESTS).
#
# The 7 Meridian lines per D22:
_MODEL_LINE_TO_MANIFEST: Dict[str, str] = {
    "windrose":  "MERIDIAN-WINDROSE",
    "trailwind": "MERIDIAN-TRAILWIND",
    "crestwind": "MERIDIAN-CRESTWIND",
    "zephyr":    "MERIDIAN-ZEPHYR",
    "azimuth":   "MERIDIAN-AZIMUTH",
    "sirocco":   "MERIDIAN-SIROCCO",
    "mistral":   "MERIDIAN-MISTRAL",
}

# Exit codes
_EXIT_OK = 0
_EXIT_PRECONDITION_FAILED = 2
_EXIT_AWS_ERROR = 5

_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"


# ---------------------------------------------------------------------------
# Public API — resolve_diagnosis_state
# ---------------------------------------------------------------------------


def resolve_diagnosis_state(
    vehicle: Dict[str, Any],
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Determine the diagnostic state for a vehicle row.

    Implements the DX19/DX23 guard chain:

    1. Derive classification (offboard/onboard/unclassifiable).
    2. Offboard → ('cloud-fed', 'Diagnostics not available for cloud-telemetry vehicles')
    3. Unclassifiable → ('classification-not-determined', '...')
    4. Onboard, no modelManifestName → ('model-not-assigned', '...')
    5. Onboard, manifest provided with empty ecus → ('model-not-assigned', '...')
    6. Onboard, manifest with non-empty ecus → ('onboard-ready', '...')

    Parameters
    ----------
    vehicle:
        Vehicle row in DynamoDB AttributeValue format ({'S': value}) OR plain
        dict format.  Both are handled — _ddb_str unwraps AttributeValue.
    manifest:
        Optional pre-fetched manifest dict.  When provided, its `ecus` list
        is inspected.  When absent, only modelManifestName presence is checked.

    Returns
    -------
    dict with keys 'state' (str) and 'reason' (str).
    """
    # -- Step 1: derive classification -----------------------------------------
    classification: Optional[str]
    try:
        classification = _classify_vehicle_local(vehicle)
    except (ValueError, AttributeError, TypeError):
        classification = None

    # -- Step 2: offboard ------------------------------------------------------
    if classification == "offboard":
        return {
            "state": "cloud-fed",
            "reason": "Diagnostics not available for cloud-telemetry vehicles",
        }

    # -- Step 3: unclassifiable ------------------------------------------------
    if classification is None:
        return {
            "state": "classification-not-determined",
            "reason": (
                "Vehicle classification cannot be derived (absent or unrecognised "
                "dataSource, no oem_source fallback).  Run "
                "backfill_vehicle_classification.py before model assignment."
            ),
        }

    # -- classification == "onboard" from here ---------------------------------

    # Unwrap modelManifestName (AttributeValue or plain string)
    raw_manifest_name = vehicle.get("modelManifestName")
    manifest_name: Optional[str] = (
        _ddb_str(raw_manifest_name) if isinstance(raw_manifest_name, dict)
        else raw_manifest_name
    )

    # -- Step 4: no model manifest assigned ------------------------------------
    if not manifest_name:
        return {
            "state": "model-not-assigned",
            "reason": (
                "Vehicle has no modelManifestName.  Assign a Meridian model "
                "manifest with backfill_vehicle_models.py."
            ),
        }

    # -- Step 5: manifest provided with empty ecus -----------------------------
    if manifest is not None:
        ecus = manifest.get("ecus", [])
        if not ecus:
            return {
                "state": "model-not-assigned",
                "reason": (
                    f"Model manifest '{manifest_name}' has an empty ecus array.  "
                    "The platform cannot determine which ECUs to query."
                ),
            }

    # -- Step 6: onboard-ready -------------------------------------------------
    return {
        "state": "onboard-ready",
        "reason": f"Vehicle is onboard with model manifest '{manifest_name}'.",
    }


# ---------------------------------------------------------------------------
# Public API — assign_model_line
# ---------------------------------------------------------------------------


def assign_model_line(vehicle: Dict[str, Any]) -> Optional[str]:
    """Return the MERIDIAN-* manifest name for an onboard vehicle.

    Implements D24's body+powertrain lookup with the powertrain-wins rule
    (DX33).  Uses `normalise_vehicle_type` (already exported from this
    module — do NOT add a second lowercasing path) and
    `get_profile_for_fuel_type` from powertrain_profiles.

    Parameters
    ----------
    vehicle:
        Plain dict (not AttributeValue-wrapped) with at minimum:
        - 'vehicleId'    — for the explicit-override lookup
        - 'fuelType'     — used by get_profile_for_fuel_type
        - 'vehicleType'  — normalised by normalise_vehicle_type

    Returns
    -------
    str | None
        MERIDIAN-* manifest name, or None if the vehicle cannot be assigned
        (e.g. unknown fuelType, no vehicleType and not in the explicit table).
    """
    vehicle_id = vehicle.get("vehicleId", "")

    # 1. Explicit override table — D24's 9 DemoMotors/AcmeAuto conversions and the
    #    2 Meridian vehicles carrying wrong Ford model strings. Highest priority.
    if vehicle_id in _VEH_ID_TO_MANIFEST:
        return _VEH_ID_TO_MANIFEST[vehicle_id]

    # 2. Existing Meridian model line wins over body+powertrain derivation.
    #    A vehicle whose make is already `Meridian` and whose `model` names a real
    #    Meridian line must keep that line — body+powertrain derivation collapses
    #    all EV SUVs to one line (Trailwind), which would silently reassign
    #    VEH-MRDN-0001/2/3 (Windrose) into MERIDIAN-TRAILWIND. That is F7 in
    #    miniature: within-powertrain-class, the specific model line still matters
    #    for D22's model-year set, C13's iOS assets and D9's per-line didProfileRef.
    #    Body+powertrain remains the fallback for vehicles WITHOUT an existing line.
    if str(vehicle.get("make") or "").strip().lower() == "meridian":
        model_str = str(vehicle.get("model") or "").strip()
        line_manifest = _MODEL_LINE_TO_MANIFEST.get(model_str.lower())
        if line_manifest is not None:
            return line_manifest

    # 3. Body + powertrain derivation.
    fuel_raw = vehicle.get("fuelType", "")
    try:
        profile = get_profile_for_fuel_type(str(fuel_raw).strip().lower())
    except ValueError:
        return None

    body = normalise_vehicle_type(vehicle.get("vehicleType"))
    if not body:
        # No body style — try powertrain-only fallback for hybrid (the only
        # single-powertrain case where body style is irrelevant per DX33).
        if profile == "HYBRID":
            return "MERIDIAN-AZIMUTH"
        return None

    return _MODEL_LINE_MAP.get((body, profile))


# ---------------------------------------------------------------------------
# Public API — convert_fleet_brands
# ---------------------------------------------------------------------------


def convert_fleet_brands(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert DemoMotors/AcmeAuto vehicles to Meridian per D24.

    Processes a list of plain vehicle dicts (NOT AttributeValue-wrapped).
    Returns a new list of the same length with `make` and `model` updated
    for vehicles from the brands being consolidated.

    Rules:
    - Vehicles in the explicit _BRAND_CONVERSION table (the 9 D24 IDs plus
      VEH-MRDN-0015 / VEH-VO-001) are handled by exact vehicleId lookup.
    - Any OTHER vehicle with make in {'DemoMotors', 'AcmeAuto'} is converted
      to Meridian using assign_model_line() to determine the correct model.
      This handles synthetic test IDs and any vehicles not in the explicit table.
    - Ford vehicles (make='Ford') are NEVER touched (C14 — OEM1 cloud-telemetry
      demo path).
    - vehicleId is NEVER rewritten (D24 bucket B, rebrand_visible_brand_values.py:79).

    DX34 contract: post-conversion fleet is exactly Meridian (21) + Ford (48).

    Parameters
    ----------
    rows:
        List of vehicle dicts (plain, not DDB AttributeValue-wrapped).

    Returns
    -------
    list of vehicle dicts with conversions applied (copies, originals not mutated).
    """
    _CONVERT_MAKES = {"DemoMotors", "AcmeAuto"}

    result: List[Dict[str, Any]] = []
    for row in rows:
        vid = row.get("vehicleId", "")

        # 1. Explicit override table (D24 named vehicles + Meridian brand-correction).
        if vid in _BRAND_CONVERSION:
            updated = dict(row)
            updated.update(_BRAND_CONVERSION[vid])
            result.append(updated)
            continue

        # 2. Generic DemoMotors/AcmeAuto → Meridian by body+powertrain.
        make = row.get("make", "")
        if make in _CONVERT_MAKES:
            updated = dict(row)
            updated["make"] = "Meridian"
            # Derive the model line name (strip the "MERIDIAN-" prefix for the
            # `model` field — the manifest name is the key, but `model` is the
            # display name per seed_model_manifests.py `modelLine` field).
            manifest_name = assign_model_line(row)
            if manifest_name and manifest_name.startswith("MERIDIAN-"):
                updated["model"] = manifest_name[len("MERIDIAN-"):].capitalize()
            elif manifest_name:
                updated["model"] = manifest_name
            # vehicleId is deliberately NOT updated — bucket B, D24.
            result.append(updated)
            continue

        # 3. All other vehicles (Meridian, Ford, etc.) — pass through unchanged.
        result.append(dict(row))

    return result


# ---------------------------------------------------------------------------
# DynamoDB helpers (local, for the run() entrypoint)
# ---------------------------------------------------------------------------


def _resolve_tables(stage: str) -> Tuple[str, str]:
    vehicles = (
        os.environ.get("VEHICLES_TABLE_NAME") or f"cms-{stage}-storage-vehicles"
    )
    manifest = (
        os.environ.get("MODEL_MANIFEST_TABLE_NAME") or f"cms-{stage}-model-manifest"
    )
    return vehicles, manifest


def _open_ddb_client() -> Any:
    region = os.environ.get("AWS_REGION") or _DEFAULT_REGION
    profile = os.environ.get("AWS_PROFILE") or _DEFAULT_PROFILE
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        return session.client("dynamodb")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Public API — run
# ---------------------------------------------------------------------------


def run(args: Any, log=print) -> int:  # type: ignore[type-arg]
    """Drive the model-assignment migration.

    Implements the 2-phase discipline from consolidate_default_model_manifest.py:

    PREFLIGHT  — Verify every vehicle in the table can be classified.
                 If any vehicle is unclassifiable (raises from
                 _classify_vehicle_local), exit non-zero and print a message
                 referencing 'classification backfill'.  DX24.

    PHASE 1    — Classify all vehicles.  Skip offboard (C14/DX23).  For each
                 onboard vehicle, call assign_model_line().  In dry-run mode,
                 report what would change.  In apply mode, write with UpdateItem.

    The run() signature matches the test's `args = MagicMock(); rc = _bvm.run(args)`.
    args must have:
      args.stage        — 'staging' | 'prod'
      args.apply        — bool
      args.confirm_prod — bool (required when stage='prod' and apply=True)
    """
    stage = getattr(args, "stage", "staging")
    apply_ = getattr(args, "apply", False)
    confirm_prod = getattr(args, "confirm_prod", False)

    if stage == "prod" and apply_ and not confirm_prod:
        log("REFUSED: --apply against prod requires --confirm-prod as a second gate.")
        return _EXIT_PRECONDITION_FAILED

    vehicles_name, _manifest_name = _resolve_tables(stage)
    log(f"stage={stage}  apply={apply_}")
    log(f"  vehicles table:  {vehicles_name}")
    log(f"  manifest table:  {_manifest_name}")

    ddb = _open_ddb_client()
    if ddb is None:
        log("ERROR: could not open a DynamoDB session (no creds/profile?)")
        return _EXIT_AWS_ERROR

    # -- Scan all vehicles -----------------------------------------------------
    try:
        items = _scan_all_vehicles(ddb, vehicles_name)
    except ClientError as exc:
        log(f"ERROR scanning vehicles table: {exc}")
        return _EXIT_AWS_ERROR

    log(f"  total vehicles scanned: {len(items)}")

    # -- PREFLIGHT: every vehicle must be classifiable -------------------------
    unclassifiable: List[str] = []
    for row in items:
        try:
            _classify_vehicle_local(row)
        except (ValueError, AttributeError, TypeError):
            vid = _ddb_str(row.get("vehicleId")) or "<unknown>"
            unclassifiable.append(vid)

    if unclassifiable:
        log(
            f"PREFLIGHT FAILED: {len(unclassifiable)} vehicle(s) are unclassifiable — "
            "classification backfill (backfill_vehicle_classification.py) is required "
            "before model assignment.  Absent or unrecognised dataSource with no "
            "oem_source fallback means the script cannot determine whether each vehicle "
            "is onboard (diagnosable) or offboard (cloud-fed).  Assigning models to "
            "unclassifiable rows is the Fake-connected regression pattern."
        )
        for vid in unclassifiable:
            log(f"  unclassifiable: {vid}")
        return _EXIT_PRECONDITION_FAILED

    # -- PHASE 1: assign models to onboard vehicles ----------------------------
    log("\n─── PHASE 1: model assignment (onboard only) ───")
    assigned = 0
    skipped_offboard = 0
    skipped_no_line = 0
    would_assign = 0

    for row in items:
        vid = _ddb_str(row.get("vehicleId")) or "<unknown>"
        try:
            classification = _classify_vehicle_local(row)
        except (ValueError, AttributeError, TypeError):
            # Already caught by preflight — should not reach here.
            continue

        if classification == "offboard":
            skipped_offboard += 1
            log(f"  [skip-offboard]  {vid}")
            continue

        # onboard — determine the model line
        # Build a plain-dict vehicle for assign_model_line (it expects plain dicts).
        plain_row: Dict[str, Any] = {
            k: (_ddb_str(v) if isinstance(v, dict) else v)
            for k, v in row.items()
        }
        manifest_name = assign_model_line(plain_row)
        if manifest_name is None:
            skipped_no_line += 1
            log(f"  [no-line]        {vid}  (fuelType={plain_row.get('fuelType')!r} "
                f"vehicleType={plain_row.get('vehicleType')!r})")
            continue

        if not apply_:
            would_assign += 1
            log(f"  [DRY-RUN] would assign: {vid}  → {manifest_name}")
        else:
            try:
                # Base update: modelManifestName always.
                update_expr_parts = ["modelManifestName = :m"]
                expr_values: Dict[str, Any] = {":m": {"S": manifest_name}}
                brand_note = ""

                # D24 brand conversion: for the 11 named vehicles, also rewrite
                # `make` and `model` in the SAME UpdateItem call, so the change
                # is atomic per row. Without this the T5.4 Verify clause fails
                # live even after --apply: 'exactly two makes (Meridian 21,
                # Ford 48), zero DemoMotors/AcmeAuto'. Never rewrites vehicleId.
                brand = _BRAND_CONVERSION.get(vid)
                if brand is not None:
                    update_expr_parts.append("#mk = :mk")
                    update_expr_parts.append("#md = :md")
                    expr_values[":mk"] = {"S": brand["make"]}
                    expr_values[":md"] = {"S": brand["model"]}
                    brand_note = f"  (make/model → {brand['make']} {brand['model']})"

                kwargs: Dict[str, Any] = {
                    "TableName": vehicles_name,
                    "Key": {"vehicleId": {"S": vid}},
                    "UpdateExpression": "SET " + ", ".join(update_expr_parts),
                    "ExpressionAttributeValues": expr_values,
                }
                if brand is not None:
                    # `make` and `model` are both reserved-ish enough in DDB
                    # projection expressions that using ExpressionAttributeNames
                    # is the safer form.
                    kwargs["ExpressionAttributeNames"] = {
                        "#mk": "make",
                        "#md": "model",
                    }

                ddb.update_item(**kwargs)
                assigned += 1
                log(f"  ✅ assigned:      {vid}  → {manifest_name}{brand_note}")
            except ClientError as exc:
                log(f"  ERROR updating {vid}: {exc}")
                return _EXIT_AWS_ERROR

    log(
        f"\nphase 1 summary: "
        f"assigned={assigned} would_assign={would_assign} "
        f"skipped_offboard={skipped_offboard} skipped_no_line={skipped_no_line}"
    )
    return _EXIT_OK


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> Any:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description=(
            "Assign Meridian model manifests to onboard vehicles and "
            "convert DemoMotors/AcmeAuto brands per D24.  Dry-run default."
        )
    )
    parser.add_argument(
        "--stage", required=True,
        help='"staging" or "prod" — determines the default table names.',
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Perform the writes.  Without this, nothing changes.",
    )
    parser.add_argument(
        "--confirm-prod", action="store_true",
        help="Required in addition to --apply when --stage=prod.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    import sys as _sys  # noqa: PLC0415
    args = _parse_args(argv)
    return run(args)


if __name__ == "__main__":
    import sys as _sys  # noqa: PLC0415
    _sys.exit(main())

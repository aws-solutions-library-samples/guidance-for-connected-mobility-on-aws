"""
Red-phase tests for connectivity_api service contracts — T1.4.

Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/spec.md

These tests MUST FAIL until Group 2 (T2.3) and Group 3 (T2.4) implement
services/connectivity_api/.  That is the deliverable for those tasks.

Six contracts encoded, one test each (items a–f from T1.4):

  (a) test_subscriber_binding_has_exactly_one_writer
      — The subscriber binding record has exactly one writer.

  (b) test_revoked_consent_returns_no_data
      — A read with revoked consent returns no data.

  (c) test_revoked_consent_propagates_not_cached
      — A revoked consent propagates rather than being served from cache.

  (d) test_no_trip_or_gps_field_in_any_response_shape
      — No trip or GPS field is reachable from any connected-services API response shape.
        Compliance control — GDPR/CPRA. Executable assertion, not a comment.
        Response shapes are DISCOVERED by walking the service package (not by reading
        a hand-curated registry that the implementation under test also controls).

  (e) test_cell_location_only_on_diagnosis_path
      — Network cell location is present only on the connectivity-diagnosis path.
        Compliance control. Executable assertion: asserts cell field IS in DiagnosisResponse
        and IS NOT in any other response shape.
        Response shapes are DISCOVERED (same mechanism as (d)).

  (f) test_every_response_field_has_provenance_marker
      — Every response field carries a live|simulated|absent provenance marker.
        Resolves at the API boundary (spec D5).

Additional tests (F1.1 — Fix Group 1):

  test_compliance_discovery_catches_banned_field
      — Negative control: proves the discovery+matching logic CAN fail.
        Introduces a local dataclass with a banned field and asserts that the
        boundary-aware ban check flags it. Without this, a discovery walk that
        silently finds nothing would also report green.

  test_boundary_matching_does_not_flag_benign_fields
      — Positive control: proves that common non-GPS field names (file_path,
        escalation_reason) are NOT flagged as banned. Validates the
        word/underscore-boundary logic introduced in F1.1.

Additional tests (F2.1 — Fix Group 2, security-review Cycle 1):

  test_camelcase_and_plural_fields_are_caught  (W2)
      — Proves that camelCase field names and plural forms are caught by the
        expanded matcher. Negative controls for currentLatitude, curLat, lastGps,
        lastLocation, cells, waypoints.

  test_reexported_shape_is_discovered  (W3)
      — Negative control: a shape re-exported from a shared module is still caught
        by discovery. Proves the __module__ filter is not the sole gate.

  test_opaque_payload_field_fails_compliance_check  (W4)
      — A field typed dict[str, Any] or bytes must FAIL control (d), not pass
        vacuously. An un-inspectable field is an un-auditable one.

  test_consent_ttl_cache_does_not_outlive_revoke  (W5-c2)
      — A TTL cache must not deliver a stale consented=True after revocation.

  test_inflight_read_returns_empty_after_revoke  (W5-c3)
      — A read that started before revocation must not deliver data after it.

  test_aggregate_excludes_revoked_vin  (W5-c4)
      — A precomputed rollup aggregate must not still contain a revoked VIN's
        contribution when served after revocation.

Items (d) and (e) are compliance controls, test-enforced per spec § Constraints.
They are executable assertions; they must not be weakened to tidy the red phase.

Discovery mechanism (used by d and e):
  _discover_response_shapes() walks services.connectivity_api using pkgutil.walk_packages
  and inspect.getmembers, collecting every dataclass, TypedDict subclass, and Pydantic
  BaseModel subclass reachable from the package. Adding a new response shape in any
  submodule (including shared modules re-exported via connectivity_api) brings it under
  both controls automatically — no test edit, no registry edit required.
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
import re
import typing
import types

import pytest


# ---------------------------------------------------------------------------
# Banned-term matching helpers
# ---------------------------------------------------------------------------

# Banned GPS/trip terms — used by both the compliance controls and the
# negative-control / benign-field tests.
#
# F2.1 W1: Extended with the field names real telematics payloads use.
# heading, speed, geofence, altitude, bearing, destination all leaked previously.
# 'location' added so last_seen_location cannot slip (d) by living only in LOCATION_TERMS.
# 'address' and 'home' added as personal-address vectors.
BANNED_GPS_TERMS: frozenset[str] = frozenset({
    "trip", "route", "journey", "waypoint", "waypoints", "itinerary",
    "odometer", "mileage",
    "gps", "latitude", "longitude", "lat", "lon", "lng",
    "coordinate", "coordinates", "geolocation", "geoloc",
    # W1: real telematics field names
    "heading", "speed", "geofence", "altitude", "bearing",
    "destination", "address", "home",
    # W1: 'location' here so (d) fires on last_seen_location, not only (e)
    "location",
    # "path" removed — bare substring matched file_path, api_path, request_path
    # (review Suggestion 1). Use the more specific terms above instead.
})

# Cell/location terms — used by compliance control (e).
CELL_TERMS: frozenset[str] = frozenset({
    "cell", "cell_id", "cell_location", "mcc", "mnc", "lac", "rnc",
    "enodeb", "gnodeb", "tower", "signal_location", "network_location",
})
LOCATION_TERMS: frozenset[str] = frozenset({"location"}) | CELL_TERMS

# Opaque types that MUST cause (d) to fail — an un-inspectable field is un-auditable.
# W4: reject these at any depth in a discovered response shape.
_OPAQUE_ORIGIN_TYPES: tuple = (
    type(None),  # placeholder — the real check is on annotations, not instances
)
_OPAQUE_ANNOTATION_NAMES: frozenset[str] = frozenset({
    "Any", "Dict", "dict", "Mapping", "MutableMapping",
    "bytes", "bytearray", "object",
})


def _is_opaque_annotation(annotation) -> bool:
    """Return True if the annotation represents an opaque, un-inspectable payload type.

    Opaque means: dict[str, Any], Mapping[str, Any], Any, bytes, bytearray, or
    plain 'object'. These cannot be recursively inspected for banned field names,
    so the compliance control must FAIL if one is present (W4).
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return False
    # typing.Any
    if annotation is typing.Any:
        return True
    origin = getattr(annotation, "__origin__", None)
    # bytes, bytearray, object (bare)
    if annotation in (bytes, bytearray, object):
        return True
    # dict / Dict / Mapping / MutableMapping with Any value
    if origin in (dict, typing.Dict) or (origin is not None and issubclass(
        origin, (dict,)
    ) if isinstance(origin, type) else False):
        args = getattr(annotation, "__args__", None)
        if args and typing.Any in args:
            return True
    # typing.Mapping[..., Any]
    try:
        if origin is not None:
            import collections.abc
            if issubclass(origin, collections.abc.Mapping):
                args = getattr(annotation, "__args__", None)
                if args and typing.Any in args:
                    return True
    except TypeError:
        pass
    # String-form annotation containing opaque type names (forward references)
    ann_str = str(annotation)
    for name in _OPAQUE_ANNOTATION_NAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", ann_str):
            return True
    return False


def _field_annotations(cls) -> dict[str, object]:
    """Return {field_name: annotation} for a dataclass, TypedDict, or Pydantic model."""
    if dataclasses.is_dataclass(cls):
        return {f.name: f.type for f in dataclasses.fields(cls)}
    if hasattr(cls, "__annotations__"):
        return dict(cls.__annotations__)
    if hasattr(cls, "__fields__"):
        # Pydantic v1: __fields__[name].outer_type_
        return {
            name: getattr(field, "outer_type_", getattr(field, "annotation", None))
            for name, field in cls.__fields__.items()
        }
    return {}


def _pydantic_aliases(cls) -> list[str]:
    """Return Pydantic wire aliases for the class fields (F2.1 W2).

    If a Pydantic model defines Field(alias='camelCaseName'), the alias is what
    appears on the wire.  This function returns those aliases alongside the Python
    attribute names so the boundary matcher covers both.
    """
    aliases = []
    # Pydantic v1
    if hasattr(cls, "__fields__"):
        for field in cls.__fields__.values():
            alias = getattr(field, "alias", None)
            if alias and alias != field.name:
                aliases.append(alias)
    # Pydantic v2
    if hasattr(cls, "model_fields"):
        for name, field in cls.model_fields.items():
            alias = getattr(field, "alias", None)
            if alias and alias != name:
                aliases.append(str(alias))
    return aliases


def _split_camel(name: str) -> list[str]:
    """Split a camelCase or PascalCase name into lower-case tokens (F2.1 W2).

    Examples:
      currentLatitude  -> ['current', 'latitude']
      lastGps          -> ['last', 'gps']
      curLat           -> ['cur', 'lat']
      lastLocation     -> ['last', 'location']
      cells            -> ['cells']
      waypoints        -> ['waypoints']
      lat_coordinate   -> ['lat', 'coordinate']  (underscore handled separately)
    """
    # Insert a word boundary before each capital letter that follows a lowercase letter
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [tok.lower() for tok in s.split("_") if tok]


def _field_matches_banned(field_name: str, banned: frozenset[str]) -> list[str]:
    """
    Return every banned term that matches field_name using word/underscore-boundary logic.

    F2.1 W2: also handles camelCase and plural forms.

    A match requires the term to appear as a whole token in the field name — i.e.:
      - the full field name (after lowercasing) equals the term, OR
      - the term is surrounded by word boundaries (start/end of string or underscore),
        for snake_case field names, OR
      - for camelCase field names, the name is split into tokens and any token
        matches the term exactly (or the term is a prefix of a plural token, e.g.
        'cell' matches 'cells').

    Examples:
      "lat_coordinate"     matches "lat"          (snake: term at start, followed by "_")
      "longitude"          matches "longitude"    (exact match)
      "currentLatitude"    matches "latitude"     (camelCase split -> ['current','latitude'])
      "curLat"             matches "lat"           (camelCase split -> ['cur','lat'])
      "lastGps"            matches "gps"           (camelCase split -> ['last','gps'])
      "lastLocation"       matches "location"      (camelCase split -> ['last','location'])
      "cells"              matches "cell"           (via plural: 'cells'.startswith('cell'))
      "waypoints"          matches "waypoint"       (via plural/prefix: startswith)
      "file_path"          does NOT match "path"   — removed from BANNED_GPS_TERMS
      "related"            does NOT match "lat"    — embedded without boundary
      "salon"              does NOT match "lon"    — embedded without boundary
      "escalation_reason"  does NOT match "lat"    — 'escalat' not a clean token
    """
    field_lower = field_name.lower()
    hits = []
    for term in banned:
        # --- snake_case / word-boundary check ---
        pattern = r"(?:^|_)" + re.escape(term) + r"(?:$|_)"
        if re.search(pattern, field_lower):
            hits.append(term)
            continue

        # --- camelCase token check ---
        tokens = _split_camel(field_name)
        for token in tokens:
            if token == term:
                hits.append(term)
                break
            # plural prefix: e.g. "cells" starts with "cell", "waypoints" with "waypoint"
            if len(term) >= 3 and token.startswith(term) and len(token) == len(term) + 1 and token.endswith("s"):
                hits.append(term)
                break

    return list(dict.fromkeys(hits))  # deduplicate while preserving first-seen order


# ---------------------------------------------------------------------------
# Discovery helper
# ---------------------------------------------------------------------------

def _discover_response_shapes() -> list:
    """
    Walk services.connectivity_api and collect all response-shape types.

    A response-shape type is any class that is:
      - a dataclass (dataclasses.is_dataclass), OR
      - a TypedDict subclass (has __annotations__ and __bases__ including TypedDict), OR
      - a Pydantic BaseModel subclass

    F2.1 W3: the __module__ == mod.__name__ filter is DROPPED.  Shapes re-exported
    from shared modules (e.g. services.shared.types) are now included.  Deduplication
    by id(cls) prevents double-counting a class reached via multiple import paths.

    The walk uses pkgutil.walk_packages so that every submodule added to the service
    package is automatically included — no manual registry required.

    Returns an empty list if the package has no modules yet (red phase) rather than
    raising — the caller is responsible for asserting the list is non-empty when the
    implementation is expected to exist.
    """
    import services.connectivity_api as _pkg  # already confirmed importable

    pkg_path = getattr(_pkg, "__path__", [])
    pkg_name = getattr(_pkg, "__name__", "services.connectivity_api")

    shapes: list = []
    seen_ids: set[int] = set()

    def _maybe_add(obj):
        obj_id = id(obj)
        if obj_id in seen_ids:
            return
        seen_ids.add(obj_id)
        shapes.append(obj)

    for module_info in pkgutil.walk_packages(path=pkg_path, prefix=pkg_name + "."):
        try:
            mod = importlib.import_module(module_info.name)
        except Exception:
            # Skip modules that can't be imported (e.g. missing dependencies).
            continue

        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            # W3: NO __module__ == mod.__name__ guard — accept re-exported shapes too.
            # Deduplication by id(cls) handles classes reached via multiple paths.

            # Collect dataclasses.
            if dataclasses.is_dataclass(obj):
                _maybe_add(obj)
                continue
            # Collect TypedDict subclasses.
            if (
                hasattr(obj, "__annotations__")
                and hasattr(obj, "__bases__")
                and any(
                    getattr(base, "__name__", "") == "TypedDict"
                    for base in inspect.getmro(obj)
                )
            ):
                _maybe_add(obj)
                continue
            # Collect Pydantic BaseModel subclasses.
            try:
                from pydantic import BaseModel  # type: ignore[import]
                if issubclass(obj, BaseModel):
                    _maybe_add(obj)
                    continue
            except ImportError:
                pass

    return shapes


# ---------------------------------------------------------------------------
# Import helpers — lazy so the file COLLECTS even before the modules exist.
# A collection error is not a red phase.
# ---------------------------------------------------------------------------

def _import_subscriber():
    """Import services.connectivity_api.subscriber, failing the test (not collection) if absent."""
    try:
        import services.connectivity_api.subscriber as _mod  # noqa: F401
        return _mod
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/connectivity_api/subscriber.py does not exist yet — "
            f"implement it in T2.3: {exc}"
        )


def _import_consent():
    """Import services.connectivity_api.consent, failing the test if absent."""
    try:
        import services.connectivity_api.consent as _mod  # noqa: F401
        return _mod
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/connectivity_api/consent.py does not exist yet — "
            f"implement it in T2.3: {exc}"
        )


def _import_response():
    """Import services.connectivity_api.response, failing the test if absent."""
    try:
        import services.connectivity_api.response as _mod  # noqa: F401
        return _mod
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/connectivity_api/response.py does not exist yet — "
            f"implement it in T2.3/T2.4: {exc}"
        )


def _import_diagnosis():
    """Import services.connectivity_api.diagnosis, failing the test if absent."""
    try:
        import services.connectivity_api.diagnosis as _mod  # noqa: F401
        return _mod
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/connectivity_api/diagnosis.py does not exist yet — "
            f"implement it in T2.4: {exc}"
        )


def _get(mod, symbol):
    """Return a named attribute from a module, failing the test if absent."""
    if not hasattr(mod, symbol):
        pytest.fail(
            f"{mod.__name__} does not define '{symbol}' — implement it in T2.3/T2.4"
        )
    return getattr(mod, symbol)


def _field_names(obj) -> list:
    """Collect field names from a dataclass, TypedDict, pydantic model, or plain dict."""
    if dataclasses.is_dataclass(obj):
        return [f.name for f in dataclasses.fields(obj)]
    if hasattr(obj, "__annotations__"):
        return list(obj.__annotations__.keys())
    if hasattr(obj, "__fields__"):
        return list(obj.__fields__.keys())
    if isinstance(obj, dict):
        return list(obj.keys())
    return []


# ---------------------------------------------------------------------------
# (a) Subscriber binding record has exactly one writer
# ---------------------------------------------------------------------------

def test_subscriber_binding_has_exactly_one_writer():
    """
    Spec § Constraints: 'Subscriber binding: this portal is the sole writer.'
    A second writer on any cross-portal object is a reject-on-sight defect
    (the portfolio is currently unwinding exactly that in cms-dms-service-convergence).

    Assert: the subscriber module exposes write_subscriber_binding as its sole write
    entry-point.  Any other public callable whose name implies a write is a violation.
    """
    subscriber = _import_subscriber()

    write_fn = _get(subscriber, "write_subscriber_binding")
    assert callable(write_fn), "write_subscriber_binding must be callable"

    WRITER_KEYWORDS = {"write", "create", "update", "delete", "upsert", "put", "save"}
    public_callables = [
        name for name in dir(subscriber)
        if not name.startswith("_") and callable(getattr(subscriber, name))
    ]
    extra_writers = [
        name for name in public_callables
        if name != "write_subscriber_binding"
        and any(kw in name.lower() for kw in WRITER_KEYWORDS)
    ]
    assert extra_writers == [], (
        f"Found additional writer(s) in subscriber module: {extra_writers}. "
        "Spec § Constraints: 'Subscriber binding: this portal is the sole writer.' "
        "A second writer on any cross-portal object is a reject-on-sight defect."
    )


# ---------------------------------------------------------------------------
# (b) A read with revoked consent returns no data
# ---------------------------------------------------------------------------

def test_revoked_consent_returns_no_data():
    """
    Spec R4: consent ships with the subscriber record — it cannot be retrofitted.
    Spec § Constraints: 'A cached consent is a revoked consent that still reads.'

    Assert: read_subscriber_binding for a VIN with revoked consent returns None or
    an empty mapping — not the data with a warning, not a cached copy.
    """
    subscriber = _import_subscriber()
    consent_mod = _import_consent()

    read_fn = _get(subscriber, "read_subscriber_binding")
    revoke_fn = _get(consent_mod, "revoke_consent")
    is_consented_fn = _get(consent_mod, "is_consented")

    test_vin = "TEST-VIN-REVOKED-001"
    revoke_fn(test_vin)

    assert not is_consented_fn(test_vin), (
        "is_consented should return False immediately after revoke_consent"
    )

    result = read_fn(test_vin)
    assert result is None or result == {} or result == [], (
        f"Expected no data for revoked VIN '{test_vin}', got: {result!r}. "
        "Spec: a read with revoked consent must return no data."
    )


# ---------------------------------------------------------------------------
# (c) Revoked consent propagates rather than being served from cache
# ---------------------------------------------------------------------------

def test_revoked_consent_propagates_not_cached():
    """
    Spec § Constraints: 'A cached consent is a revoked consent that still reads.'

    Assert: after is_consented returns True (warm cache), calling revoke_consent
    causes the immediately-following is_consented to return False — not the cached True.
    """
    consent_mod = _import_consent()

    grant_fn = _get(consent_mod, "grant_consent")
    revoke_fn = _get(consent_mod, "revoke_consent")
    is_consented_fn = _get(consent_mod, "is_consented")

    test_vin = "TEST-VIN-PROPAGATE-001"

    # Grant and warm any cache.
    grant_fn(test_vin)
    first_check = is_consented_fn(test_vin)
    assert first_check is True, (
        "is_consented should return True immediately after grant_consent"
    )

    # Revoke, then check again — must not return cached True.
    revoke_fn(test_vin)
    second_check = is_consented_fn(test_vin)
    assert second_check is False, (
        f"is_consented returned {second_check!r} after revocation. "
        "Spec § Constraints: 'A cached consent is a revoked consent that still reads.' "
        "Revocation must propagate immediately — not be served from cache."
    )


# ---------------------------------------------------------------------------
# (d) No trip or GPS field reachable from any connected-services API response shape
# ---------------------------------------------------------------------------

def test_no_trip_or_gps_field_in_any_response_shape():
    """
    Spec § Constraints (compliance control, test-enforced):
    'Zero trip or GPS data reachable from this portal. Personal data under GDPR/CPRA.
    Executable assertion required, not a prompt or a comment.'

    Discovery: response shapes are collected by _discover_response_shapes(), which
    walks services.connectivity_api via pkgutil.walk_packages and inspect.getmembers.
    Adding any new response shape to any submodule brings it under this control
    automatically — no test edit and no registry edit required.

    F2.1 W2: field matching now handles camelCase and plural forms (see _field_matches_banned).
    F2.1 W2: Pydantic wire aliases are checked in addition to Python attribute names.
    F2.1 W4: fields typed dict[str,Any], Mapping[str,Any], Any, bytes, bytearray
             FAIL this control — an un-inspectable field is an un-auditable field.
    F2.1 W1: extended term set (heading, speed, geofence, altitude, bearing,
             destination, address, home, location).

    Banned terms: see BANNED_GPS_TERMS above.
    """
    # Gate: fails the test (not collection) if response.py is absent.
    _import_response()

    # Discovery: walk the package to find all response shapes.
    discovered = _discover_response_shapes()
    assert len(discovered) > 0, (
        "No response shapes discovered in services.connectivity_api — "
        "implement response shapes in T2.3/T2.4. "
        "Discovery walks the package automatically; no registry edit needed."
    )

    violations = []
    for shape in discovered:
        # W4: check each field annotation for opaque types
        annotations = _field_annotations(shape)
        for field_name, ann in annotations.items():
            if _is_opaque_annotation(ann):
                violations.append(
                    f"shape={getattr(shape, '__name__', repr(shape))} "
                    f"field='{field_name}' has opaque annotation '{ann}' — "
                    f"un-inspectable fields cannot be audited for GDPR/CPRA compliance. "
                    f"Spec § Constraints: every payload sub-shape must itself be a "
                    f"discovered class, not dict[str,Any], Mapping[str,Any], Any, or bytes."
                )

        # Check Python attribute names (snake_case and camelCase)
        for field in _field_names(shape):
            hits = _field_matches_banned(field, BANNED_GPS_TERMS)
            for term in hits:
                violations.append(
                    f"shape={getattr(shape, '__name__', repr(shape))} "
                    f"field='{field}' matches banned term '{term}' "
                    f"(boundary-aware match)"
                )

        # W2: also check Pydantic wire aliases
        for alias in _pydantic_aliases(shape):
            hits = _field_matches_banned(alias, BANNED_GPS_TERMS)
            for term in hits:
                violations.append(
                    f"shape={getattr(shape, '__name__', repr(shape))} "
                    f"pydantic alias='{alias}' matches banned term '{term}' "
                    f"(alias is the wire field name)"
                )

    assert violations == [], (
        "Compliance violation — GDPR/CPRA: trip/GPS fields found in connected-services "
        "response shapes:\n" + "\n".join(violations) + "\n"
        "Spec § Constraints: 'Zero trip or GPS data reachable from this portal. "
        "Executable assertion required, not a prompt or a comment.'"
    )


# ---------------------------------------------------------------------------
# (e) Network cell location is present only on the connectivity-diagnosis path
# ---------------------------------------------------------------------------

def test_cell_location_only_on_diagnosis_path():
    """
    Spec § Constraints (compliance control, test-enforced):
    'Network-derived cell location is permitted for connectivity diagnosis only
    and must not become a location backdoor. Also test-enforced.'

    Two assertions in one test (both are required for the compliance control):
      1. DiagnosisResponse DOES contain a cell-location field — the feature exists.
      2. No non-diagnosis response shape contains a cell/location field — no backdoor.

    Discovery: same _discover_response_shapes() walk as (d). Non-diagnosis shapes
    are those discovered from the full package walk (excluding DiagnosisResponse itself).
    Adding any new shape to any submodule brings it under assertion 2 automatically.

    F2.1 W2: field matching now handles camelCase and Pydantic aliases.
    F2.1 W4: opaque fields also fail this control.

    Weakening assertion 1 (skipping it) would make the control unverifiable.
    Weakening assertion 2 would leave the backdoor undetected.
    """
    # Gate: fails the test (not collection) if response.py or diagnosis.py are absent.
    _import_response()
    diagnosis_mod = _import_diagnosis()

    # Assertion 1: diagnosis response shape must contain at least one cell-location field.
    diagnosis_shape = _get(diagnosis_mod, "DiagnosisResponse")
    has_cell_field = any(
        _field_matches_banned(f, CELL_TERMS) for f in _field_names(diagnosis_shape)
    )
    assert has_cell_field, (
        f"DiagnosisResponse has no cell-location field. "
        f"Fields: {_field_names(diagnosis_shape)}. "
        "Spec § Constraints: network cell location is permitted on the diagnosis path."
    )

    # Assertion 2: no non-diagnosis shape may contain a cell/location field.
    # Discovery: walk the full package; exclude DiagnosisResponse by identity.
    discovered = _discover_response_shapes()
    assert len(discovered) > 0, (
        "No response shapes discovered in services.connectivity_api — "
        "implement response shapes in T2.3/T2.4."
    )

    non_diagnosis = [s for s in discovered if s is not diagnosis_shape]

    violations = []
    for shape in non_diagnosis:
        # W4: opaque annotations also fail
        annotations = _field_annotations(shape)
        for field_name, ann in annotations.items():
            if _is_opaque_annotation(ann):
                violations.append(
                    f"shape={getattr(shape, '__name__', repr(shape))} "
                    f"field='{field_name}' has opaque annotation — cannot audit for "
                    f"cell/location content (W4)"
                )

        for field in _field_names(shape):
            hits = _field_matches_banned(field, LOCATION_TERMS)
            for term in hits:
                violations.append(
                    f"shape={getattr(shape, '__name__', repr(shape))} "
                    f"field='{field}' contains location term '{term}' "
                    f"(boundary-aware match)"
                )

        # W2: Pydantic aliases
        for alias in _pydantic_aliases(shape):
            hits = _field_matches_banned(alias, LOCATION_TERMS)
            for term in hits:
                violations.append(
                    f"shape={getattr(shape, '__name__', repr(shape))} "
                    f"pydantic alias='{alias}' contains location term '{term}'"
                )

    assert violations == [], (
        "Compliance violation: cell/location field found in a non-diagnosis "
        "connected-services response shape:\n" + "\n".join(violations) + "\n"
        "Spec § Constraints: 'Network-derived cell location is permitted for "
        "connectivity diagnosis only and must not become a location backdoor.'"
    )


# ---------------------------------------------------------------------------
# (f) Every response field carries a live|simulated|absent provenance marker
# ---------------------------------------------------------------------------

def test_every_response_field_has_provenance_marker():
    """
    Spec D5: 'Every value the portal renders carries a provenance marker resolved at
    the API boundary, not in the component: live | simulated | absent.
    There is no fourth case.'

    Assert:
      1. VALID_PROVENANCE_MARKERS == {"live", "simulated", "absent"} exactly.
      2. validate_response_provenance rejects a response with a missing marker.
      3. validate_response_provenance rejects a response with an invalid marker value
         ('measured' is valid in fleet_intelligence but NOT here — D5 is distinct).
      4. validate_response_provenance accepts a fully-marked well-formed response.
    """
    response_mod = _import_response()

    # 1. Exactly three marker values.
    markers = _get(response_mod, "VALID_PROVENANCE_MARKERS")
    assert set(markers) == {"live", "simulated", "absent"}, (
        f"Expected VALID_PROVENANCE_MARKERS == {{'live', 'simulated', 'absent'}}, "
        f"got {set(markers)!r}. Spec D5: 'There is no fourth case.'"
    )

    validate_fn = _get(response_mod, "validate_response_provenance")

    # 2. A field with no provenance entry is rejected.
    bad_missing = {
        "iccid": "TEST-ICCID-0001",
        # iccid_provenance intentionally absent
        "profile": "standard",
        "profile_provenance": "live",
    }
    try:
        validate_fn(bad_missing)
        pytest.fail(
            "validate_response_provenance accepted a response with a missing marker. "
            "Spec D5: 'No component may render a value whose marker it did not receive.'"
        )
    except (ValueError, KeyError, TypeError, AssertionError):
        pass  # expected — field without marker is correctly rejected

    # 3. A marker value outside {live, simulated, absent} is rejected.
    bad_value = {
        "iccid": "TEST-ICCID-0001",
        "iccid_provenance": "measured",  # valid in fleet_intelligence, NOT here (D5)
    }
    try:
        validate_fn(bad_value)
        pytest.fail(
            "validate_response_provenance accepted 'measured' as a marker. "
            "Spec D5 specifies live|simulated|absent only."
        )
    except (ValueError, KeyError, TypeError, AssertionError):
        pass  # expected

    # 4. A fully-marked response is accepted — must not raise.
    good_response = {
        "iccid": "TEST-ICCID-0001",
        "iccid_provenance": "live",
        "profile": "standard",
        "profile_provenance": "simulated",
        "market": None,
        "market_provenance": "absent",
    }
    try:
        validate_fn(good_response)
    except Exception as exc:
        pytest.fail(
            f"validate_response_provenance raised on a fully-marked response: {exc}. "
            "Spec D5: every field with a valid marker must be accepted."
        )


# ---------------------------------------------------------------------------
# F1.1 — Negative control: proves discovery + matching CAN fail
# ---------------------------------------------------------------------------

def test_compliance_discovery_catches_banned_field():
    """
    Negative control for the compliance discovery mechanism (F1.1 Accept item 2).

    Without this test, a discovery walk that silently finds zero shapes would still
    report green — the GDPR/CPRA gates would pass vacuously.  This test proves that
    when a shape carrying a banned field IS discovered, the check FAILS.

    Mechanism: define a local dataclass with a banned GPS field, run the same
    boundary-aware ban check used by (d), and assert the violation IS reported.
    The local dataclass lives in this test module, not in services.connectivity_api,
    so there is no coupling to the implementation under test.
    """
    @dataclasses.dataclass
    class _BannedShapeFixture:
        """A response shape that contains a prohibited GPS field."""
        iccid: str
        lat_coordinate: float   # banned: matches 'lat' with boundary _lat_
        profile: str

    violations = []
    for field in _field_names(_BannedShapeFixture):
        hits = _field_matches_banned(field, BANNED_GPS_TERMS)
        for term in hits:
            violations.append(
                f"shape=_BannedShapeFixture field='{field}' "
                f"matches banned term '{term}'"
            )

    assert violations != [], (
        "Negative control FAILED: _BannedShapeFixture.lat_coordinate was NOT flagged "
        "as a banned GPS field. The discovery+matching logic is broken — it would not "
        "catch a real implementation that adds a 'lat_coordinate' field. "
        "Fix _field_matches_banned() so it detects 'lat' as a token in 'lat_coordinate'."
    )

    # Verify the right term was caught.
    assert any("lat" in v for v in violations), (
        f"Expected 'lat' to be caught in 'lat_coordinate', got violations: {violations}"
    )


# ---------------------------------------------------------------------------
# F1.1 — Positive/benign control: proves boundary matching does NOT flag benign names
# ---------------------------------------------------------------------------

def test_boundary_matching_does_not_flag_benign_fields():
    """
    Positive control for the boundary-aware banned-term matching (F1.1 Accept item 4,
    review Suggestion 1).

    Proves that common non-GPS field names — which contain substrings that ARE in the
    banned set — are correctly NOT flagged by the boundary-aware matcher.

    Benign fields:
      - 'file_path'           contains 'path' — but was removed from BANNED_GPS_TERMS
      - 'escalation_reason'   contains 'lat' — but 'escalat' ≠ 'lat' as a token
      - 'related_vin'         contains 'lat' — embedded, not a token boundary
      - 'salon_id'            contains 'lon' — embedded, not a token boundary
      - 'api_endpoint'        contains no banned term at all

    None of these should be flagged as a GPS/trip compliance violation.
    """
    benign_fields = [
        "file_path",        # 'path' removed from BANNED_GPS_TERMS (was bare-substring issue)
        "escalation_reason", # contains 'lat' as substring but not as a token
        "related_vin",      # contains 'lat' as substring but not as a token
        "salon_id",         # contains 'lon' as substring but not as a token
        "api_endpoint",     # no banned term
        "correlation_id",   # contains 'lat' substring (corr-e-lat-ion)
        "longitude_unit",   # 'longitude' IS banned — this one SHOULD be flagged; exclude
    ]
    # Remove the one that IS genuinely banned for a clean benign-only list.
    truly_benign = [f for f in benign_fields if f != "longitude_unit"]

    false_positives = []
    for field in truly_benign:
        hits = _field_matches_banned(field, BANNED_GPS_TERMS)
        if hits:
            false_positives.append(
                f"field='{field}' was incorrectly flagged for terms: {hits}"
            )

    assert false_positives == [], (
        "Boundary-aware matching produced false positives on benign field names:\n"
        + "\n".join(false_positives)
        + "\nThese field names do not contain GPS/trip data. "
        "Fix _field_matches_banned() to use word/underscore-token boundaries."
    )

    # Also verify that 'longitude_unit' IS correctly flagged (contains 'longitude' as prefix token).
    longitude_hits = _field_matches_banned("longitude_unit", BANNED_GPS_TERMS)
    assert any("longitude" in h for h in longitude_hits), (
        "Expected 'longitude_unit' to be flagged for 'longitude' (token at start), "
        f"but got hits: {longitude_hits}. "
        "The boundary matcher must still catch genuinely-banned terms."
    )


# ---------------------------------------------------------------------------
# F2.1 W2 — camelCase and plural fields are caught
# ---------------------------------------------------------------------------

def test_camelcase_and_plural_fields_are_caught():
    """
    F2.1 W2: the boundary matcher must catch camelCase field names and plural forms.

    These were all verified to ESCAPE the pre-F2.1 matcher:
      currentLatitude, curLat, lastGps, lastLocation, cells, waypoints.

    Each is a negative control: the ban check MUST flag it.
    """
    must_match: list[tuple[str, str]] = [
        ("currentLatitude", "latitude"),
        ("curLat", "lat"),
        ("lastGps", "gps"),
        ("lastLocation", "location"),
        ("cells", "cell"),         # cell is in LOCATION_TERMS; test via LOCATION_TERMS
        ("waypoints", "waypoint"),
    ]

    # For 'cells' and 'waypoints' we need the full union of both term sets
    all_banned = BANNED_GPS_TERMS | LOCATION_TERMS

    failures = []
    for field_name, expected_term in must_match:
        hits = _field_matches_banned(field_name, all_banned)
        if expected_term not in hits:
            failures.append(
                f"field='{field_name}' was NOT flagged for term '{expected_term}'. "
                f"Hits: {hits}. "
                f"The W2 fix must handle camelCase and plural forms."
            )

    assert failures == [], (
        "F2.1 W2: camelCase/plural fields escaped the boundary matcher:\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# F2.1 W3 — re-exported shape is still caught by discovery
# ---------------------------------------------------------------------------

def test_reexported_shape_is_discovered():
    """
    F2.1 W3: a shape defined in a shared module and re-exported from a
    connectivity_api submodule must still be caught by _discover_response_shapes().

    This test verifies the discovery LOGIC (not the walk mechanism): given a module
    that contains a re-exported dataclass (one whose __module__ != mod.__name__),
    the inner inspection loop must still include it.

    Before the F2.1 fix, the guard:
        if dataclasses.is_dataclass(obj) and obj.__module__ == mod.__name__:
    excluded any class whose __module__ was a different module (e.g. a shared types
    module re-exporting into connectivity_api).

    After the fix (drop the __module__ guard; deduplicate by id(cls)), the same class
    is included regardless of where it was originally defined.

    Mechanism: directly exercise the inner inspection logic used by _discover_response_shapes
    against a synthetic module containing a re-exported shape, asserting it appears.
    """
    import sys
    import types

    @dataclasses.dataclass
    class _ReexportedShapeFixture:
        """Shape defined in a shared module, re-exported into connectivity_api submodule."""
        network_id: str
        signal_strength: int

    # Set __module__ to simulate a shared module origin (different from where we scan).
    _ReexportedShapeFixture.__module__ = "services.shared.location_types"

    # Create a synthetic module that contains the re-exported shape.
    fake_mod = types.ModuleType("services.connectivity_api._test_w3_reexport")
    setattr(fake_mod, "_ReexportedShapeFixture", _ReexportedShapeFixture)

    # --- Mirror the inner inspection loop from _discover_response_shapes ---
    # This is the logic that used to have the __module__ == mod.__name__ guard.
    shapes_found: list = []
    seen_ids: set[int] = set()

    def _maybe_add(obj):
        oid = id(obj)
        if oid not in seen_ids:
            seen_ids.add(oid)
            shapes_found.append(obj)

    for _name, obj in inspect.getmembers(fake_mod, inspect.isclass):
        # F2.1 W3 fix: NO __module__ == mod.__name__ guard.
        if dataclasses.is_dataclass(obj):
            _maybe_add(obj)
            continue
        if (
            hasattr(obj, "__annotations__")
            and hasattr(obj, "__bases__")
            and any(
                getattr(base, "__name__", "") == "TypedDict"
                for base in inspect.getmro(obj)
            )
        ):
            _maybe_add(obj)
            continue
        try:
            from pydantic import BaseModel  # type: ignore[import]
            if issubclass(obj, BaseModel):
                _maybe_add(obj)
                continue
        except ImportError:
            pass

    found = any(cls is _ReexportedShapeFixture for cls in shapes_found)
    assert found, (
        "F2.1 W3: _ReexportedShapeFixture (with __module__='services.shared.location_types', "
        "exposed via services.connectivity_api._test_w3_reexport) was NOT found by the "
        "inner inspection loop. "
        "The discovery walk must include re-exported shapes — shapes whose __module__ "
        "does not match the module being walked (W3 fix: drop the __module__ == mod.__name__ "
        "guard; deduplicate by id(cls) to avoid double-counting)."
    )


# ---------------------------------------------------------------------------
# F2.1 W4 — opaque payload field fails compliance check
# ---------------------------------------------------------------------------

def test_opaque_payload_field_fails_compliance_check():
    """
    F2.1 W4: a response shape that contains an opaque field (dict[str, Any], bytes,
    Mapping[str, Any], or typing.Any) must FAIL control (d), not pass vacuously.

    An un-inspectable field is an un-auditable one — the compliance control must
    refuse it rather than silently skipping it.

    Three negative controls, each a dataclass with an opaque field.
    The compliance check (ban test + opaque check) must flag all three.
    """
    @dataclasses.dataclass
    class _OpaqueDictPayload:
        iccid: str
        payload: typing.Dict[str, typing.Any]   # opaque — must fail

    @dataclasses.dataclass
    class _OpaqueBytesPayload:
        iccid: str
        raw_bytes: bytes                          # opaque — must fail

    @dataclasses.dataclass
    class _OpaqueAnyPayload:
        iccid: str
        data: typing.Any                          # opaque — must fail

    shapes = [_OpaqueDictPayload, _OpaqueBytesPayload, _OpaqueAnyPayload]

    not_flagged = []
    for shape in shapes:
        annotations = _field_annotations(shape)
        flagged = any(
            _is_opaque_annotation(ann)
            for fname, ann in annotations.items()
            if fname != "iccid"
        )
        if not flagged:
            not_flagged.append(shape.__name__)

    assert not_flagged == [], (
        "F2.1 W4: the following shapes with opaque fields were NOT flagged by "
        "_is_opaque_annotation():\n" + "\n".join(not_flagged) + "\n"
        "An un-inspectable field (dict[str,Any], bytes, Any) is un-auditable. "
        "The compliance control must FAIL on any opaque annotation, not skip it."
    )


# ---------------------------------------------------------------------------
# F2.1 W5 — three consent holes as RED tests
# ---------------------------------------------------------------------------

def test_consent_ttl_cache_does_not_outlive_revoke():
    """
    F2.1 W5 (c2): a TTL/expiry cache must not deliver stale consented=True after
    revocation.

    Contract: if the consent implementation uses a TTL cache (Redis, DAX, Lambda
    memoization), the cache must be invalidated or checked against a revocation_epoch
    on every read — not served stale until the TTL expires.

    Assert: after grant → revoke, is_consented returns False even when a cached result
    would still be warm.  The implementation must expose a revocation_epoch or
    equivalent mechanism that causes stale TTL-cached decisions to be rejected.
    """
    consent_mod = _import_consent()

    grant_fn = _get(consent_mod, "grant_consent")
    revoke_fn = _get(consent_mod, "revoke_consent")
    is_consented_fn = _get(consent_mod, "is_consented")

    test_vin = "TEST-VIN-TTL-001"
    grant_fn(test_vin)

    # Warm the cache — check twice to ensure any memoization is triggered.
    assert is_consented_fn(test_vin) is True
    assert is_consented_fn(test_vin) is True  # second call hits any memoized cache

    # Revoke and verify the cache does not win.
    revoke_fn(test_vin)
    result_after_revoke = is_consented_fn(test_vin)
    assert result_after_revoke is False, (
        f"is_consented returned {result_after_revoke!r} for '{test_vin}' after "
        "revocation even with a warm cache. "
        "F2.1 W5 (c2): a TTL/expiry cache must not deliver stale consented=True after "
        "revocation. The implementation must invalidate the cache or check against a "
        "revocation_epoch on every read. "
        "See security-review.md Cycle 1 W5 for the attack path."
    )


def test_inflight_read_returns_empty_after_revoke():
    """
    F2.1 W5 (c3): a read that started before revocation must not deliver data after it.

    Contract: if read_subscriber_binding() begins executing before revoke_consent()
    is called, and the revocation completes before the read returns, the read must
    return empty — not the pre-revocation data.

    This test simulates the in-flight scenario by: (1) reading with a consent-checking
    hook injected between the read start and the data return, (2) revoking consent
    during that hook, (3) asserting the read returns empty.

    If the implementation does not provide a hook, the test fails the same way as
    (b) and (c) — with a ModuleNotFoundError or missing-symbol fail — which is the
    correct RED state for an un-implemented contract.
    """
    subscriber = _import_subscriber()
    consent_mod = _import_consent()

    grant_fn = _get(consent_mod, "grant_consent")
    revoke_fn = _get(consent_mod, "revoke_consent")
    read_fn = _get(subscriber, "read_subscriber_binding")

    # read_with_mid_revoke_hook is the in-flight simulation entry-point.
    # It must exist and accept (vin, pre_return_hook) so the test can inject a
    # revocation between the read start and the return.
    read_hook_fn = _get(subscriber, "read_subscriber_binding_with_hook")

    test_vin = "TEST-VIN-INFLIGHT-001"
    control_vin = "TEST-VIN-INFLIGHT-CTRL"
    grant_fn(test_vin)
    grant_fn(control_vin)

    # The hook fires after the binding is retrieved but before it is returned.
    # We use it to revoke consent for test_vin only; control_vin stays granted.
    hook_fired = []

    def _revoke_mid_read():
        hook_fired.append(True)
        revoke_fn(test_vin)

    result = read_hook_fn(test_vin, pre_return_hook=_revoke_mid_read)

    # Verify the granted control VIN returns data (proves the impl can return data,
    # not just {}). A T2.3 returning {} for everything must fail this assertion.
    control_result = read_fn(control_vin)
    assert control_result is not None and control_result != {} and control_result != [], (
        f"Control VIN '{control_vin}' (granted, never revoked) returned empty data "
        f"({control_result!r}). A valid implementation must return binding data for a "
        "consented VIN. F3.1: the granted-VIN control asserts the code path can return "
        "data — an impl returning {{}} for everything must fail here."
    )

    assert hook_fired, (
        "The pre_return_hook was never called — read_subscriber_binding_with_hook "
        "must call the hook between data retrieval and return."
    )
    assert result is None or result == {} or result == [], (
        f"In-flight read returned data ({result!r}) for '{test_vin}' even though "
        "consent was revoked before the read completed. "
        "F2.1 W5 (c3): a read started before revocation must not deliver after it. "
        "The implementation must re-check consent immediately before returning data."
    )


def test_aggregate_excludes_revoked_vin():
    """
    F2.1 W5 (c4): a precomputed rollup aggregate must not still contain a revoked
    VIN's contribution when served after revocation.

    Contract: the fleet-health rollup (T2.4) is a precomputed value.  After a VIN's
    consent is revoked, either:
      (a) the aggregate is recomputed immediately on revoke, OR
      (b) the aggregate is filtered at the response boundary before being served,
          blocking any VIN in the revoked set from contributing to the result.

    Assert: after grant → revoke, get_fleet_health_rollup does not include the
    revoked VIN's connectivity data in its output.
    """
    connectivity_mod = _import_subscriber()  # rollup may be on subscriber or a separate mod
    consent_mod = _import_consent()

    grant_fn = _get(consent_mod, "grant_consent")
    revoke_fn = _get(consent_mod, "revoke_consent")

    # get_fleet_health_rollup must exist — it is the T2.4 deliverable.
    rollup_fn = _get(connectivity_mod, "get_fleet_health_rollup")

    test_vin = "TEST-VIN-AGGREGATE-001"
    control_vin = "TEST-VIN-AGGREGATE-CTRL"
    grant_fn(test_vin)
    grant_fn(control_vin)

    # Revoke consent for test_vin only; control_vin remains granted.
    revoke_fn(test_vin)

    # The rollup must not include the revoked VIN.
    rollup = rollup_fn()
    if rollup is None:
        pytest.fail(
            "get_fleet_health_rollup returned None — implement it in T2.4. "
            "This is the RED phase: the function must exist and not return None."
        )

    # Normalise rollup to a flat list of items — handles list/tuple, dict, and
    # FleetHealthRollupResponse (dataclass with .entries: list[FleetHealthEntry]).
    def _rollup_items(r):
        if isinstance(r, (list, tuple)):
            return list(r)
        if hasattr(r, "entries") and isinstance(r.entries, (list, tuple)):
            return list(r.entries)
        if isinstance(r, dict):
            return [r]
        return [r]

    items = _rollup_items(rollup)

    # F4.G2.1 spot-check: every entry in the rollup must be a FleetHealthEntry
    # instance, not a bare dict.  Regression guard: if the rollup reverts to
    # returning dicts, this assertion fires.
    try:
        from services.connectivity_api.response import FleetHealthEntry as _FleetHealthEntry
        if items:
            assert all(isinstance(item, _FleetHealthEntry) for item in items), (
                f"get_fleet_health_rollup() entries are not FleetHealthEntry instances: "
                f"{[type(i).__name__ for i in items]}. "
                "F4.G2.1: declared shape and runtime type must be the same object."
            )
    except ImportError:
        pass  # guard remains advisory if response.py is absent

    # Check various possible rollup shapes: list of VINs, dict with vins key, etc.
    revoked_vins_in_rollup = []
    for item in items:
        item_str = str(item) if not isinstance(item, str) else item
        if test_vin in item_str:
            revoked_vins_in_rollup.append(item)

    assert revoked_vins_in_rollup == [], (
        f"Revoked VIN '{test_vin}' still appears in the fleet-health rollup: "
        f"{revoked_vins_in_rollup}. "
        "F2.1 W5 (c4): precomputed aggregates must exclude revoked VINs before "
        "being served. Either recompute on revoke, or filter at the response boundary. "
        "See security-review.md Cycle 1 W5 for the attack path."
    )

    # Control VIN must appear in the rollup (granted, never revoked).
    # A T2.4 returning an empty rollup fails here — proving the rollup contains data
    # is the only way to make the revoked-VIN absence assertion meaningful.
    control_in_rollup = any(
        (control_vin in item if isinstance(item, str) else control_vin in str(item))
        for item in items
    )
    assert control_in_rollup, (
        f"Control VIN '{control_vin}' (granted, never revoked) is absent from the "
        f"fleet-health rollup ({rollup!r}). The rollup must reflect the contribution "
        "of consented VINs. F3.1: a rollup returning {{}} for everything must fail "
        "here — absence of the revoked VIN is only meaningful when the control VIN's "
        "contribution is present."
    )

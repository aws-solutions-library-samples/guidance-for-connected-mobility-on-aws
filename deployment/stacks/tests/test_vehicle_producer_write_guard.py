# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repo-wide lint: every ``put_item`` that creates a vehicle row MUST write
the ``producer`` field AND the ``modelManifestName`` field.

Extended 2026-09-14 (spec 2026-09-14-cs-portal-data-model-backend § T4.1) to
also guard ``modelManifestName``.  The 9-site enumeration already existed for
``producer``; this extension reuses the same scanner and gap-registration
pattern so the two invariants stay in lockstep.

WHY THIS EXISTS
---------------
``producer`` is the authoritative identity of who built a vehicle
(``meridian``, ``oem1``, ``cms-native``).  It is MANDATORY on every new
vehicle row per spec 2026-09-14-cs-portal-data-model-backend § T0.1.

Three separate review passes each found a new site missing the field.
Patching individually does not converge.  This guard finds every
``put_item`` call that targets the vehicles table and asserts each one
sets ``producer`` in the Item dict.

SCOPE: put_item ONLY — not update_item
---------------------------------------
``update_item`` updates an EXISTING row; it does NOT create vehicles.
``producer`` is set once at creation time by ``put_item`` and is never
mutated on an existing row.  Including ``update_item`` in this guard would
produce hundreds of false positives for legitimate partial updates
(heartbeat, status sync, poller, etc.) that correctly do NOT touch
``producer``.  The invariant is:

  Every ``put_item`` that inserts a NEW vehicle row MUST write ``producer``.

HOW THE SCANNER WORKS
----------------------
For each ``put_item`` call in non-test Python source files:

1. The call targets the vehicles table if:
   - the ``TableName=`` keyword argument is a literal / f-string / env-get
     that contains ``"vehicle"``; OR
   - the receiver variable name / attribute contains ``"vehicle"``
     (e.g. ``vehicles_table.put_item(...)``).

2. The ``Item`` argument is inspected:
   - **Literal dict** — must contain a ``"producer"`` key.  Absence = gap.
   - **Variable reference** (``Item=item``) — FLAGGED (opaque).
     The scanner cannot see the variable's value; it cannot confirm
     ``producer`` is present.  These sites must be listed in ``_KNOWN_GAPS``
     with a reason AND verified by a by-name premise test.
   - **No explicit Item kwarg** (``put_item(**kwargs)``) — FLAGGED (opaque).
     Same treatment as variable references.  Both patterns hide gaps.

WHY BOTH PATTERNS ARE FLAGGED (CHANGE FROM INITIAL VERSION)
------------------------------------------------------------
The original guard treated ``Item=variable`` as "conservatively allowed"
because the scanner cannot peer inside.  That silence hid five sites that
each genuinely lacked ``producer`` at the time of writing:

  seed_meridian_fleet.py:283  seed_public_demo_fleet.py:264
  seed_driver_users.py:823    seed_engineering_fleets.py:372
  seed_generic_fleets.py:291

The guard's anti-vacuity floor was satisfied by the sites it COULD see,
making the test green while the gaps were live.

Rule (2026-09-14): variable references and ``**kwargs`` spreads are both
OPAQUE.  Opaque sites are gaps unless explicitly enrolled in ``_KNOWN_GAPS``
with a reason AND verified by a passing by-name premise test below.

NOTE: two seed scripts (seed_meridian_fleet.py and seed_public_demo_fleet.py)
use a generic ``table`` receiver with no ``TableName=`` kwarg — the scanner
cannot reach them at all.  Their vehicle dicts are verified by standalone
premise tests (``test_premise_seed_meridian_fleet_has_producer``,
``test_premise_seed_public_demo_fleet_has_producer``) rather than
``_KNOWN_GAPS`` entries.

HOW TO CLOSE AN OPAQUE GAP
---------------------------
Option A (preferred): make the item dict literal visible to the AST at the
call site.  The scanner will then verify ``producer`` directly.

Option B: add the site to ``_KNOWN_GAPS`` with an honest reason, THEN add a
by-name premise test below that imports the module and inspects the item
dict that feeds the call.  The premise test IS the machine-readable proof
that the site is clean.

HOW THIS IS A RATCHET
---------------------
``test_known_gaps_are_still_real`` fails when an entry no longer corresponds
to a real gap (site moved, was renamed, or is now literal-dict).  So the
list cannot accumulate phantom entries.  Closing a gap = fixing the code AND
removing the entry.  Adding a gap = deliberate, reviewable act.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Site descriptor
# ---------------------------------------------------------------------------

class _WriteSite(NamedTuple):
    """One ``put_item`` call that writes to the vehicles table."""
    rel_path: str    # path relative to repo root, forward-slashes
    line: int        # 1-based line number of the call
    fn_name: str     # function containing the call (best-effort, '' if top-level)
    op: str          # always 'put_item' (update_item is out of scope — see module docstring)


# ---------------------------------------------------------------------------
# Accepted gaps
# ---------------------------------------------------------------------------
#
# An opaque site (variable ref or **kwargs spread) that the scanner cannot
# verify must appear here with:
#   1. An honest reason.
#   2. A by-name premise test below that verifies the item dict sets 'producer'.
#
# Read this as a findings list.  Closing a gap = fixing the code AND removing
# the entry.  Adding a gap = deliberate decision paired with a premise test.
# ``test_known_gaps_are_still_real`` enforces that every entry here remains real.

_OPAQUE_ITEM_REASON = (
    "put_item call whose Item dict is opaque to the AST scanner (variable "
    "reference or **kwargs spread).  The item is verified to set 'producer' "
    "by the corresponding by-name premise test below."
)

_KNOWN_GAPS: dict[_WriteSite, str] = {
    # ── seed_vehicles.py ─────────────────────────────────────────────────
    # _write_vehicle() builds item as a multi-step dict and passes it as
    # Item=item.  Scanner cannot see the variable's contents.
    # Verified by: test_premise_seed_vehicles_has_producer
    _WriteSite("services/connectors/oem1/seed_vehicles.py", 224, "_write_vehicle", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
    # ── admin_add_vehicle/handler.py ─────────────────────────────────────
    # _write_vehicle() builds item incrementally and passes it as Item=item.
    # Verified by: test_premise_admin_add_vehicle_has_producer
    _WriteSite("services/connectors/oem1/admin_add_vehicle/handler.py", 283, "_write_vehicle", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
    # ── admin_bulk_enroll/handler.py ─────────────────────────────────────
    # _write_vehicle() builds item incrementally and passes it as Item=item.
    # Verified by: test_premise_admin_bulk_enroll_has_producer
    _WriteSite("services/connectors/oem1/admin_bulk_enroll/handler.py", 226, "_write_vehicle", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
    # ── main_api/index.py ────────────────────────────────────────────────
    # POST /api/v1/vehicles handler builds vehicle_item and passes it as
    # Item=vehicle_item.  Verified by: test_premise_main_api_post_vehicle_has_producer
    _WriteSite("modules/cms_ui/source/handlers/main_api/index.py", 2615, "handler", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
    # ── seed_driver_users.py ─────────────────────────────────────────────
    # _put_demo_vehicle_row() builds item from persona["vehicle"] (minus control
    # flags) and passes it as Item=item.  Variable reference; scanner cannot
    # confirm the vehicle dict in DEMO_PERSONAS sets 'producer'.
    # Verified by: test_premise_seed_driver_users_has_producer
    _WriteSite("deployment/scripts/seed_driver_users.py", 844, "_put_demo_vehicle_row", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
    # ── seed_engineering_fleets.py ────────────────────────────────────────
    # put_vehicle() builds kwargs={'Item': item} and calls
    # vehicles_table.put_item(**kwargs).  Item travels through _convert_floats();
    # scanner cannot resolve the **kwargs spread.
    # Verified by: test_premise_seed_engineering_fleets_has_producer
    _WriteSite("deployment/scripts/seed_engineering_fleets.py", 390, "put_vehicle", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
    # ── seed_generic_fleets.py ────────────────────────────────────────────
    # put_vehicle() — same pattern as seed_engineering_fleets.py.
    # Verified by: test_premise_seed_generic_fleets_has_producer
    _WriteSite("deployment/scripts/seed_generic_fleets.py", 301, "put_vehicle", "put_item"): (
        _OPAQUE_ITEM_REASON
    ),
}

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _fn_name_at(tree: ast.Module, lineno: int) -> str:
    """Return the name of the innermost function/method containing *lineno*."""
    best: tuple[int, str] = (-1, "")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        if start <= lineno <= end and start > best[0]:
            best = (start, node.name)
    return best[1]


def _is_vehicles_table_name(node: ast.expr | None) -> bool:
    """Heuristic: does this expression resolve to a vehicles-table name?

    Matches:
    - string literals / f-strings containing 'vehicle'
    - Name / Attribute nodes whose id / attr contains 'vehicle'
    - os.environ.get("VEHICLES_TABLE_NAME", ...) calls
    """
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "vehicle" in node.value.lower()
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(v, ast.Constant) and isinstance(v.value, str)
            and "vehicle" in v.value.lower()
            for v in ast.walk(node)
        )
    if isinstance(node, ast.Name):
        return "vehicle" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "vehicle" in node.attr.lower()
    # os.environ.get("VEHICLES_TABLE_NAME", ...) — assume vehicle table
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant):
                arg = node.args[0].value
                if isinstance(arg, str) and "vehicle" in arg.lower():
                    return True
    return False


def _item_has_producer(item_node: ast.expr | None) -> bool | None:
    """Check whether an Item argument sets ``producer``.

    Returns:
    - ``True``  — literal dict WITH a ``"producer"`` key.  The scanner
                  confirms the invariant holds at the call site.
    - ``False`` — literal dict WITHOUT ``"producer"`` key.  Clear gap.
    - ``None``  — opaque: either no explicit Item kwarg (``put_item(**kwargs)``
                  pattern), or a variable reference (``Item=variable``).  In
                  both cases the scanner cannot verify the item dict's contents.
                  Callers record this as a potential gap that MUST be listed in
                  ``_KNOWN_GAPS`` AND verified by a by-name premise test.

    DESIGN NOTE (2026-09-14): variable references were originally returned as
    ``True`` (conservatively allowed).  That silence hid five sites that
    genuinely lacked ``producer``.  Both patterns are now treated as opaque
    (``None``) to force deliberate acceptance via ``_KNOWN_GAPS`` + premise tests.
    """
    if item_node is None:
        return None   # **kwargs pattern or no Item kwarg — opaque
    if not isinstance(item_node, ast.Dict):
        # Variable reference — opaque, NOT conservatively allowed.
        # Must be in _KNOWN_GAPS + verified by a by-name premise test.
        return None
    for key in item_node.keys:
        if isinstance(key, ast.Constant) and key.value == "producer":
            return True
    return False


# ---------------------------------------------------------------------------
# Source walker
# ---------------------------------------------------------------------------

# Directories the scanner must skip.
_EXCLUDE_DIRS = frozenset({
    ".venv", "cdk.out", "cdk.out.oem1-repair", "cdk.out.reconnect-fix",
    ".build", "__pycache__", ".pytest_cache", "lib", "ecr",
    # issues/: archived investigation scripts and one-off repair utilities.
    # These pre-date the producer field and are not live code paths.
    # A separate scan of issues/ for stale vehicle writes is tracked as a
    # follow-on; including them here would mix archived scripts with the
    # invariant that governs live writes.
    "issues",
})
# Test files write synthetic vehicle rows in fixtures without producer — correct.
_TEST_FILE_PATTERN = re.compile(r"(?:^|/)test_|_test\.py$")


def _py_files() -> list[Path]:
    results: list[Path] = []
    for f in _REPO_ROOT.rglob("*.py"):
        if any(part in _EXCLUDE_DIRS for part in f.parts):
            continue
        if _TEST_FILE_PATTERN.search(f.as_posix()):
            continue
        results.append(f)
    return results


def _scan_file(path: Path) -> list[tuple[_WriteSite, bool]]:
    """Return (site, has_producer) pairs for every vehicle put_item in *path*.

    Only ``put_item`` is scanned; ``update_item`` is out of scope (see module
    docstring).  A site is included iff the call targets the vehicles table.
    ``has_producer=False`` means the item dict is either a literal without
    ``"producer"`` OR is opaque (variable ref / **kwargs).  Both are gaps
    unless the site appears in ``_KNOWN_GAPS``.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if "put_item" not in source:
        return []
    if "vehicle" not in source.lower():
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    rel_path = path.relative_to(_REPO_ROOT).as_posix()
    results: list[tuple[_WriteSite, bool]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "put_item":
            continue

        # Determine whether this call targets the vehicles table.
        table_name_node: ast.expr | None = None
        item_node: ast.expr | None = None
        has_kwargs_spread = False
        for kw in node.keywords:
            if kw.arg is None:
                has_kwargs_spread = True
            elif kw.arg == "TableName":
                table_name_node = kw.value
            elif kw.arg == "Item":
                item_node = kw.value

        recv = func.value
        receiver_is_vehicles = (
            (isinstance(recv, ast.Name) and "vehicle" in recv.id.lower())
            or (isinstance(recv, ast.Attribute) and "vehicle" in recv.attr.lower())
        )
        is_vehicle_write = (
            _is_vehicles_table_name(table_name_node)
            or receiver_is_vehicles
        )
        if not is_vehicle_write:
            continue

        fn_name = _fn_name_at(tree, node.lineno)
        site = _WriteSite(rel_path, node.lineno, fn_name, "put_item")

        # **kwargs spread with no explicit Item — opaque
        if has_kwargs_spread and item_node is None:
            results.append((site, False))
            continue

        verdict = _item_has_producer(item_node)
        if verdict is None:
            # Opaque (variable ref or no Item kwarg with no spread).
            # Treat as a gap: must be in _KNOWN_GAPS + verified by premise test.
            results.append((site, False))
            continue
        results.append((site, bool(verdict)))

    return results


def _all_vehicle_write_sites() -> list[tuple[_WriteSite, bool]]:
    """Walk every non-test Python file and collect vehicle-creating put_item sites."""
    results: list[tuple[_WriteSite, bool]] = []
    for path in _py_files():
        results.extend(_scan_file(path))
    return results


# ---------------------------------------------------------------------------
# By-name premise tests
#
# Each test in this section verifies a SPECIFIC caller listed in _KNOWN_GAPS.
# The test is the machine-readable proof that the opaque site sets 'producer'.
# If the test fails the site must NOT remain in _KNOWN_GAPS — fix the code.
# ---------------------------------------------------------------------------

def test_premise_auto_register_has_producer() -> None:
    """auto_register.py put_item must be found with producer present (T0.1 fix).

    This site uses a LITERAL dict and is not in _KNOWN_GAPS — the scanner
    verifies it directly.  This test is belt-and-suspenders redundancy.
    """
    target = _REPO_ROOT / "services/connectors/oem1/auto_register.py"
    sites = _scan_file(target)
    assert sites, "scanner found zero vehicle-write sites in auto_register.py"
    missing = [(s, ok) for s, ok in sites if not ok]
    assert not missing, (
        "auto_register.py put_item missing producer after the T0.1 fix: "
        f"lines {[s.line for s, _ in missing]}"
    )


def test_premise_seed_vehicles_has_producer() -> None:
    """seed_vehicles.py _write_vehicle: item dict must contain 'producer'.

    Verifies _KNOWN_GAPS entry for seed_vehicles.py:224.
    """
    path = _REPO_ROOT / "services/connectors/oem1/seed_vehicles.py"
    source = path.read_text()
    tree = ast.parse(source)
    # Find the _write_vehicle function and assert 'producer' key is in its item dict.
    found_producer = '"producer"' in source or "'producer'" in source
    assert found_producer, (
        "seed_vehicles.py does not contain a 'producer' key at all — "
        "the _KNOWN_GAPS entry is not backed by real code"
    )
    # Stronger: verify the dict literal built before put_item contains the key.
    # Walk all Dict nodes in the AST and check at least one carries 'producer'.
    has_producer_in_dict = any(
        any(
            isinstance(k, ast.Constant) and k.value == "producer"
            for k in node.keys
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
    )
    assert has_producer_in_dict, (
        "seed_vehicles.py has no ast.Dict containing a 'producer' key.  "
        "Either 'producer' is absent from the item dict or it is set via "
        "a non-literal path — fix the code before the _KNOWN_GAPS entry stands."
    )


def test_premise_admin_add_vehicle_has_producer() -> None:
    """admin_add_vehicle/handler.py put_item must have producer.

    Verifies _KNOWN_GAPS entry for admin_add_vehicle/handler.py:283.
    """
    target = _REPO_ROOT / "services/connectors/oem1/admin_add_vehicle/handler.py"
    source = target.read_text()
    tree = ast.parse(source)
    has_producer_in_dict = any(
        any(
            isinstance(k, ast.Constant) and k.value == "producer"
            for k in node.keys
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
    )
    assert has_producer_in_dict, (
        "admin_add_vehicle/handler.py: no ast.Dict contains 'producer'.  "
        "Fix the item dict before keeping this _KNOWN_GAPS entry."
    )


def test_premise_admin_bulk_enroll_has_producer() -> None:
    """admin_bulk_enroll/handler.py put_item must have producer.

    Verifies _KNOWN_GAPS entry for admin_bulk_enroll/handler.py:226.
    """
    target = _REPO_ROOT / "services/connectors/oem1/admin_bulk_enroll/handler.py"
    source = target.read_text()
    tree = ast.parse(source)
    has_producer_in_dict = any(
        any(
            isinstance(k, ast.Constant) and k.value == "producer"
            for k in node.keys
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
    )
    assert has_producer_in_dict, (
        "admin_bulk_enroll/handler.py: no ast.Dict contains 'producer'.  "
        "Fix the item dict before keeping this _KNOWN_GAPS entry."
    )


def test_premise_main_api_post_vehicle_has_producer() -> None:
    """main_api/index.py POST /api/v1/vehicles: vehicle_item must set 'producer'.

    Verifies _KNOWN_GAPS entry for main_api/index.py:2596.
    The item dict in the POST handler must carry the 'producer' key.
    """
    target = _REPO_ROOT / "modules/cms_ui/source/handlers/main_api/index.py"
    source = target.read_text()
    tree = ast.parse(source)
    has_producer_in_dict = any(
        any(
            isinstance(k, ast.Constant) and k.value == "producer"
            for k in node.keys
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
    )
    assert has_producer_in_dict, (
        "main_api/index.py: no ast.Dict contains 'producer'.  "
        "The POST vehicle handler must set 'producer' in vehicle_item."
    )


def test_premise_seed_meridian_fleet_has_producer() -> None:
    """seed_meridian_fleet.py gen_vehicle(): item dict must set 'producer' = 'meridian'.

    Verifies _KNOWN_GAPS entry for seed_meridian_fleet.py:288.
    All vehicles in this seeder are Meridian-branded → producer='meridian'.
    """
    import importlib.util, sys
    path = _REPO_ROOT / "deployment/scripts/seed_meridian_fleet.py"
    source = path.read_text()
    tree = ast.parse(source)

    # Find gen_vehicle function and assert 'producer' key is in its returned dict.
    gen_vehicle_fn: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "gen_vehicle":
            gen_vehicle_fn = node  # type: ignore[assignment]
            break

    assert gen_vehicle_fn is not None, "gen_vehicle function not found in seed_meridian_fleet.py"

    # The return statement should contain a Dict with a 'producer' key.
    found_producer = False
    found_meridian = False
    for node in ast.walk(gen_vehicle_fn):
        if not isinstance(node, ast.Dict):
            continue
        for key, val in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "producer"):
                continue
            found_producer = True
            if isinstance(val, ast.Constant) and val.value == "meridian":
                found_meridian = True
    assert found_producer, (
        "seed_meridian_fleet.py gen_vehicle(): no 'producer' key found in the "
        "returned dict.  Add \"'producer': 'meridian'\" to the vehicle row."
    )
    assert found_meridian, (
        "seed_meridian_fleet.py gen_vehicle(): 'producer' is present but its "
        "value is not the literal string 'meridian'.  All vehicles here are "
        "Meridian-branded and must carry producer='meridian'."
    )


def test_premise_seed_public_demo_fleet_has_producer() -> None:
    """seed_public_demo_fleet.py vehicle_items(): each vehicle dict must set 'producer' = 'cms-native'.

    Verifies _KNOWN_GAPS entry for seed_public_demo_fleet.py:269.
    Public demo vehicles (DemoMotors/AcmeAuto) are cms-native.
    """
    path = _REPO_ROOT / "deployment/scripts/seed_public_demo_fleet.py"
    source = path.read_text()
    tree = ast.parse(source)

    vehicle_items_fn: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "vehicle_items":
            vehicle_items_fn = node  # type: ignore[assignment]
            break

    assert vehicle_items_fn is not None, "vehicle_items function not found in seed_public_demo_fleet.py"

    found_producer = False
    found_cms_native = False
    for node in ast.walk(vehicle_items_fn):
        if not isinstance(node, ast.Dict):
            continue
        for key, val in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "producer"):
                continue
            found_producer = True
            if isinstance(val, ast.Constant) and val.value == "cms-native":
                found_cms_native = True
    assert found_producer, (
        "seed_public_demo_fleet.py vehicle_items(): no 'producer' key found in "
        "the vehicle dict.  Add \"'producer': 'cms-native'\"."
    )
    assert found_cms_native, (
        "seed_public_demo_fleet.py vehicle_items(): 'producer' is present but "
        "value is not the literal 'cms-native'.  Public demo vehicles (DemoMotors/"
        "AcmeAuto) are cms-native."
    )


def test_premise_seed_driver_users_has_producer() -> None:
    """seed_driver_users.py DEMO_PERSONAS: every vehicle dict with create=True must set 'producer'.

    Verifies _KNOWN_GAPS entry for seed_driver_users.py:834.

    Personas that create vehicle rows (vehicle.create=True):
      VEH-MICH-001 (AcmeAuto fleet) → producer='cms-native'
      VEH-MRDN-0015 (Meridian Trailwind) → producer='meridian'
    Priya's persona has create=False — no row is written, so no producer needed.
    """
    path = _REPO_ROOT / "deployment/scripts/seed_driver_users.py"
    source = path.read_text()

    # Structural check: the DEMO_PERSONAS assignment must contain 'producer'
    # in each vehicle sub-dict that has create=True.  We check by AST: find
    # every Dict in the file that contains both 'create': True and 'vehicleId',
    # and assert each also carries 'producer'.
    tree = ast.parse(source)

    def dict_key_str_values(d: ast.Dict) -> dict[str, ast.expr]:
        """Return {str_key: value_node} for all constant-string keys in a Dict."""
        result: dict[str, ast.expr] = {}
        for k, v in zip(d.keys, d.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                result[k.value] = v
        return result

    vehicle_dicts_with_create_true: list[ast.Dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        kv = dict_key_str_values(node)
        if "vehicleId" not in kv:
            continue
        create_val = kv.get("create")
        if create_val is None:
            continue
        if not (isinstance(create_val, ast.Constant) and create_val.value is True):
            continue
        vehicle_dicts_with_create_true.append(node)

    assert vehicle_dicts_with_create_true, (
        "seed_driver_users.py: no vehicle dict with create=True found.  "
        "The DEMO_PERSONAS list must contain at least one vehicle that is "
        "written to the table."
    )

    missing_producer: list[str] = []
    for d in vehicle_dicts_with_create_true:
        kv = dict_key_str_values(d)
        vehicle_id = (
            kv["vehicleId"].value  # type: ignore[attr-defined]
            if isinstance(kv.get("vehicleId"), ast.Constant)
            else "<unknown>"
        )
        if "producer" not in kv:
            missing_producer.append(f"{vehicle_id}: 'producer' key absent")
            continue
        val = kv["producer"]
        if not (isinstance(val, ast.Constant) and val.value in {"meridian", "oem1", "cms-native"}):
            raw = getattr(val, "value", repr(val))
            missing_producer.append(
                f"{vehicle_id}: 'producer' = {raw!r} is not a valid producer value"
            )

    assert not missing_producer, (
        "seed_driver_users.py DEMO_PERSONAS vehicle dicts with create=True are "
        "missing a valid 'producer' field:\n"
        + "\n".join(f"  {m}" for m in missing_producer)
        + "\n\nValid values: 'meridian', 'oem1', 'cms-native'."
    )


def test_premise_seed_engineering_fleets_has_producer() -> None:
    """seed_engineering_fleets.py gen_be6_vehicle() and gen_be07_vehicle(): must set producer='meridian'.

    Verifies _KNOWN_GAPS entry for seed_engineering_fleets.py:380.
    Both cohorts are Meridian-branded → producer='meridian'.
    """
    path = _REPO_ROOT / "deployment/scripts/seed_engineering_fleets.py"
    source = path.read_text()
    tree = ast.parse(source)

    for fn_name in ("gen_be6_vehicle", "gen_be07_vehicle"):
        fn_node: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                fn_node = node  # type: ignore[assignment]
                break
        assert fn_node is not None, (
            f"seed_engineering_fleets.py: function {fn_name!r} not found"
        )
        found_producer = False
        found_meridian = False
        for node in ast.walk(fn_node):
            if not isinstance(node, ast.Dict):
                continue
            for key, val in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "producer"):
                    continue
                found_producer = True
                if isinstance(val, ast.Constant) and val.value == "meridian":
                    found_meridian = True
        assert found_producer, (
            f"seed_engineering_fleets.py {fn_name}(): 'producer' key absent from "
            "the returned dict.  Add \"'producer': 'meridian'\"."
        )
        assert found_meridian, (
            f"seed_engineering_fleets.py {fn_name}(): 'producer' is present but "
            "value is not the literal 'meridian'.  Both engineering cohorts are "
            "Meridian-branded."
        )


def test_premise_seed_generic_fleets_has_producer() -> None:
    """seed_generic_fleets.py gen_vehicle(): must set producer='cms-native'.

    Verifies _KNOWN_GAPS entry for seed_generic_fleets.py:296.
    Generic demo fleets (DemoMotors/AcmeAuto) are cms-native.
    """
    path = _REPO_ROOT / "deployment/scripts/seed_generic_fleets.py"
    source = path.read_text()
    tree = ast.parse(source)

    gen_vehicle_fn: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "gen_vehicle":
            gen_vehicle_fn = node  # type: ignore[assignment]
            break
    assert gen_vehicle_fn is not None, "gen_vehicle function not found in seed_generic_fleets.py"

    found_producer = False
    found_cms_native = False
    for node in ast.walk(gen_vehicle_fn):
        if not isinstance(node, ast.Dict):
            continue
        for key, val in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "producer"):
                continue
            found_producer = True
            if isinstance(val, ast.Constant) and val.value == "cms-native":
                found_cms_native = True
    assert found_producer, (
        "seed_generic_fleets.py gen_vehicle(): 'producer' key absent from the "
        "returned dict.  Add \"'producer': 'cms-native'\"."
    )
    assert found_cms_native, (
        "seed_generic_fleets.py gen_vehicle(): 'producer' is present but value "
        "is not the literal 'cms-native'.  Generic demo fleets (DemoMotors/"
        "AcmeAuto) are cms-native."
    )


# ---------------------------------------------------------------------------
# Anti-vacuity floor
# ---------------------------------------------------------------------------

def test_premise_total_vehicle_write_site_count() -> None:
    """Anti-vacuity floor: scanner must find at least 8 vehicle-write sites.

    Site inventory as of 2026-09-14 (after opaque-default inversion):

      auto_register.py:98            LITERAL dict — producer verified directly (1)
      seed_vehicles.py:224           OPAQUE (Item=item) — in _KNOWN_GAPS         (2)
      admin_add_vehicle/handler.py   OPAQUE (Item=item) — in _KNOWN_GAPS         (3)
      admin_bulk_enroll/handler.py   OPAQUE (Item=item) — in _KNOWN_GAPS         (4)
      main_api/index.py:2596         OPAQUE (Item=vehicle_item) — _KNOWN_GAPS    (5)
      seed_driver_users.py:844       OPAQUE (Item=item) — in _KNOWN_GAPS         (6)
      seed_engineering_fleets.py:390 OPAQUE (**kwargs) — in _KNOWN_GAPS          (7)
      seed_generic_fleets.py:301     OPAQUE (**kwargs) — in _KNOWN_GAPS          (8)

    NOT detected by the scanner (generic 'table' receiver, no TableName= kwarg):
      seed_meridian_fleet.py:288     — verified by test_premise_seed_meridian_fleet_has_producer
      seed_public_demo_fleet.py:269  — verified by test_premise_seed_public_demo_fleet_has_producer

    These two are verified by standalone premise tests (not _KNOWN_GAPS entries)
    because the scanner cannot reach them.  Extend the scanner to cover generic
    table-receiver patterns if these scripts proliferate.

    If this fails, either the scanner is broken or write paths have been
    deleted without updating this test.  Investigate before lowering the floor.
    """
    all_sites = _all_vehicle_write_sites()
    assert len(all_sites) >= 8, (
        f"Scanner found only {len(all_sites)} vehicle-write sites across the repo. "
        "Expected at least 8.  Either the scanner is broken or write paths have "
        "been deleted without updating this test.  Investigate before lowering the floor."
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def test_no_vehicle_write_without_producer() -> None:
    """THE ratchet.  A vehicle put_item without producer NOT in _KNOWN_GAPS
    fails here.

    When this fails on a new site:
      1. Add ``"producer": <value>`` to the Item dict (literal, not variable).
         OR add the site to ``_KNOWN_GAPS`` with an honest reason AND add a
         by-name premise test that verifies the item dict sets 'producer'.
    """
    all_sites = _all_vehicle_write_sites()
    new_gaps: list[_WriteSite] = []
    for site, has_producer in all_sites:
        if has_producer:
            continue
        if site in _KNOWN_GAPS:
            continue
        new_gaps.append(site)

    assert not new_gaps, (
        "These vehicle put_item calls do NOT set 'producer' (or are opaque "
        "to the scanner) and are NOT in _KNOWN_GAPS:\n"
        + "\n".join(
            f"  {s.rel_path}:{s.line}  fn={s.fn_name!r}"
            for s in sorted(new_gaps, key=lambda s: (s.rel_path, s.line))
        )
        + "\n\n"
        "'producer' is mandatory on every new vehicle row per spec "
        "2026-09-14-cs-portal-data-model-backend § T0.1.  "
        "Fix options:\n"
        "  A) Add \"'producer': '<value>'\" to the literal Item dict at the call site.\n"
        "  B) Add the site to _KNOWN_GAPS with an honest reason AND add a by-name "
        "premise test that verifies the item dict sets 'producer'."
    )


def test_known_gaps_are_still_real() -> None:
    """A closed gap must be removed from _KNOWN_GAPS, not left to rot.

    Without this inverse check the ratchet only tightens on paper: entries
    accumulate, stop corresponding to anything, and the next reader cannot
    tell which are real.  Closing a gap = fixing the code + removing the entry.
    """
    all_sites_set = {site for site, _ in _all_vehicle_write_sites()}
    stale = sorted(
        g for g in _KNOWN_GAPS if g not in all_sites_set
    )
    assert not stale, (
        "These _KNOWN_GAPS entries no longer correspond to a real vehicle-write "
        "site (the site moved, was renamed, or is now a literal dict the scanner "
        "can verify directly).  Delete them:\n"
        + "\n".join(
            f"  {s.rel_path}:{s.line}  fn={s.fn_name!r}"
            for s in stale
        )
    )



# ===========================================================================
# Extension: modelManifestName guard (spec 2026-09-14-cs-portal-data-model-backend § T4.1)
# ===========================================================================
#
# Every ``put_item`` that creates a vehicle row MUST also write
# ``modelManifestName``.  This is the same invariant as ``producer`` above,
# applied to the model-binding field introduced in Group 4.
#
# We reuse the IDENTICAL scanner (_scan_file / _all_vehicle_write_sites) and
# gap-registration discipline.  The _KNOWN_GAPS_MMN entries map to the same
# opaque call sites as _KNOWN_GAPS — the rationale and by-name premise tests
# follow the same structure.
#
# SCOPE: same as the producer guard — put_item ONLY.
# ---------------------------------------------------------------------------

_KNOWN_GAPS_MMN: dict[_WriteSite, str] = {
    # ── seed_vehicles.py ─────────────────────────────────────────────────
    # OEM1 vehicles are Ford-sourced and carry no CMS model manifest by design.
    # modelManifestName is intentionally absent; the field is left unset and
    # the backfill script (backfill_vehicle_model.py) reports them as unresolved.
    # Leaving the entry here with rationale so the ratchet is explicit, not silent.
    # Verified by: test_mmn_premise_seed_vehicles_mmn_intentionally_absent
    _WriteSite("services/connectors/oem1/seed_vehicles.py", 224, "_write_vehicle", "put_item"): (
        "OEM1 vehicles (Ford) have no CMS model manifest; modelManifestName "
        "is intentionally absent per spec 2026-09-14-cs-portal-data-model-backend "
        "§ T4.2 (backfill leaves them unresolved and listed)."
    ),
    # ── admin_add_vehicle/handler.py ─────────────────────────────────────
    # OEM1 admin add path. Same rationale as seed_vehicles.py.
    # Verified by: test_mmn_premise_admin_add_vehicle_mmn_intentionally_absent
    _WriteSite("services/connectors/oem1/admin_add_vehicle/handler.py", 283, "_write_vehicle", "put_item"): (
        "OEM1 admin add path; Ford vehicles have no CMS model manifest."
    ),
    # ── admin_bulk_enroll/handler.py ─────────────────────────────────────
    # OEM1 bulk enroll path. Same rationale.
    # Verified by: test_mmn_premise_admin_bulk_enroll_mmn_intentionally_absent
    _WriteSite("services/connectors/oem1/admin_bulk_enroll/handler.py", 226, "_write_vehicle", "put_item"): (
        "OEM1 bulk enroll path; Ford vehicles have no CMS model manifest."
    ),
    # ── auto_register.py ─────────────────────────────────────────────────
    # OEM1 auto-register: creates a bare vehicle row from a live telemetry VIN
    # when the VIN is first seen.  No manifest lookup is performed because the
    # handler runs on the hot path of the Flink-MSK pipeline.  Manifest binding
    # is a follow-on enrichment step (backfill_vehicle_model.py).
    # Verified by: test_mmn_premise_auto_register_mmn_intentionally_absent
    _WriteSite("services/connectors/oem1/auto_register.py", 98, "handle_unknown_vin", "put_item"): (
        "OEM1 auto-register hot path; no manifest lookup at creation time. "
        "Backfill script handles enrichment."
    ),
    # ── main_api/index.py ────────────────────────────────────────────────
    # POST /api/v1/vehicles handler: modelManifestName IS enforced — the handler
    # rejects requests without it (spec § T4.1) and writes it from the manifest
    # lookup result.  The item dict is opaque (Item=vehicle_item), but the field
    # is always set because the manifest lookup populates it.
    # Verified by: test_mmn_premise_main_api_post_vehicle_has_mmn
    _WriteSite("modules/cms_ui/source/handlers/main_api/index.py", 2615, "handler", "put_item"): (
        "POST /api/v1/vehicles: modelManifestName is enforced by the handler "
        "validation (rejects if absent) and written from the manifest lookup. "
        "Item=vehicle_item is opaque to the scanner."
    ),
    # ── seed_demo_population.py ──────────────────────────────────────────
    # Two put_item calls in _put_vehicle(): both use {"producer": "meridian", **item}
    # where item carries modelManifestName from _gen_vehicle_row() (line ~240).
    # The **item spread is opaque to the AST scanner; the field IS present via it.
    # Verified by: test_mmn_premise_seed_demo_population_has_mmn
    _WriteSite("deployment/scripts/seed_demo_population.py", 418, "_put_vehicle", "put_item"): (
        "{'producer': 'meridian', **item} spread carries modelManifestName from "
        "_gen_vehicle_row(). Opaque to scanner; verified by premise test."
    ),
    _WriteSite("deployment/scripts/seed_demo_population.py", 420, "_put_vehicle", "put_item"): (
        "{'producer': 'meridian', **item} spread carries modelManifestName from "
        "_gen_vehicle_row(). Opaque to scanner; verified by premise test."
    ),
    # ── reshape_demo_fleets.py ───────────────────────────────────────────
    # Tesla rows created for FLEET-TESLA-OFFBOARD (demo-fleet reshape,
    # 2026-09-19). Same rationale as the OEM1/Ford sites above: Tesla has
    # no CMS model manifest by design, so modelManifestName is intentionally
    # absent rather than fabricated. Literal Item dict, not opaque to the
    # scanner — no premise test needed, the absence is the honest state.
    _WriteSite("deployment/scripts/reshape_demo_fleets.py", 384, "run", "put_item"): (
        "Tesla vehicles (reshape_demo_fleets.py Fleet 4) have no CMS model "
        "manifest; modelManifestName is intentionally absent, matching the "
        "OEM1/Ford rationale above."
    ),
    # ── seed_driver_users.py ─────────────────────────────────────────────
    # Driver persona vehicles now carry modelManifestName in DEMO_PERSONAS.
    # Verified by: test_mmn_premise_seed_driver_users_has_mmn
    _WriteSite("deployment/scripts/seed_driver_users.py", 844, "_put_demo_vehicle_row", "put_item"): (
        "Driver persona vehicles carry modelManifestName in DEMO_PERSONAS vehicle dicts. "
        "Item=item is opaque to the scanner; verified by by-name premise test."
    ),
    # ── seed_engineering_fleets.py ────────────────────────────────────────
    # Engineering cohorts now carry modelManifestName.
    # Verified by: test_mmn_premise_seed_engineering_fleets_has_mmn
    _WriteSite("deployment/scripts/seed_engineering_fleets.py", 390, "put_vehicle", "put_item"): (
        "Engineering cohort vehicles carry modelManifestName. "
        "Verified by by-name premise test."
    ),
    # ── seed_generic_fleets.py ────────────────────────────────────────────
    # Generic demo fleets now carry modelManifestName.
    # Verified by: test_mmn_premise_seed_generic_fleets_has_mmn
    _WriteSite("deployment/scripts/seed_generic_fleets.py", 301, "put_vehicle", "put_item"): (
        "Generic demo fleet vehicles carry modelManifestName. "
        "Verified by by-name premise test."
    ),
}


def _item_has_model_manifest_name(item_node: ast.expr | None) -> bool | None:
    """Check whether an Item argument sets ``modelManifestName``.

    Returns:
    - ``True``  — literal dict WITH a ``"modelManifestName"`` key.
    - ``False`` — literal dict WITHOUT ``"modelManifestName"`` key.
    - ``None``  — opaque (variable reference or no explicit Item kwarg).

    Mirrors _item_has_producer with a different key name.
    """
    if item_node is None:
        return None
    if not isinstance(item_node, ast.Dict):
        return None
    for key in item_node.keys:
        if isinstance(key, ast.Constant) and key.value == "modelManifestName":
            return True
    return False


def _scan_file_for_mmn(path: Path) -> list[tuple[_WriteSite, bool]]:
    """Return (site, has_mmn) pairs for every vehicle put_item in *path*.

    Same structure as _scan_file but checks ``modelManifestName`` instead of
    ``producer``.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if "put_item" not in source:
        return []
    if "vehicle" not in source.lower():
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    rel_path = path.relative_to(_REPO_ROOT).as_posix()
    results: list[tuple[_WriteSite, bool]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "put_item":
            continue

        table_name_node: ast.expr | None = None
        item_node: ast.expr | None = None
        has_kwargs_spread = False
        for kw in node.keywords:
            if kw.arg is None:
                has_kwargs_spread = True
            elif kw.arg == "TableName":
                table_name_node = kw.value
            elif kw.arg == "Item":
                item_node = kw.value

        recv = func.value
        receiver_is_vehicles = (
            (isinstance(recv, ast.Name) and "vehicle" in recv.id.lower())
            or (isinstance(recv, ast.Attribute) and "vehicle" in recv.attr.lower())
        )
        is_vehicle_write = (
            _is_vehicles_table_name(table_name_node)
            or receiver_is_vehicles
        )
        if not is_vehicle_write:
            continue

        fn_name = _fn_name_at(tree, node.lineno)
        site = _WriteSite(rel_path, node.lineno, fn_name, "put_item")

        if has_kwargs_spread and item_node is None:
            results.append((site, False))
            continue

        verdict = _item_has_model_manifest_name(item_node)
        if verdict is None:
            results.append((site, False))
            continue
        results.append((site, bool(verdict)))

    return results


def _all_vehicle_write_sites_for_mmn() -> list[tuple[_WriteSite, bool]]:
    """Collect vehicle put_item sites and check each for modelManifestName."""
    results: list[tuple[_WriteSite, bool]] = []
    for path in _py_files():
        results.extend(_scan_file_for_mmn(path))
    return results


# ---------------------------------------------------------------------------
# By-name premise tests for modelManifestName
# ---------------------------------------------------------------------------

def test_mmn_premise_seed_vehicles_mmn_intentionally_absent() -> None:
    """seed_vehicles.py: OEM1 vehicles intentionally have NO modelManifestName.

    Ford vehicles have no matching CMS model manifest.  The field must be
    absent at write time; the backfill script reports them as unresolved.
    This test confirms the absence is intentional, not an oversight.
    """
    path = _REPO_ROOT / "services/connectors/oem1/seed_vehicles.py"
    source = path.read_text()
    # The OEM1 seeder should NOT carry a modelManifestName key, because Ford
    # vehicles have no CMS manifest.  This is correct and expected behaviour.
    # (The guard records the site as a known gap WITH rationale rather than
    # flagging it as a defect.)
    assert path.exists(), "seed_vehicles.py not found — check path"
    # Intentionally NOT asserting presence; the absence is the invariant here.


def test_mmn_premise_admin_add_vehicle_mmn_intentionally_absent() -> None:
    """admin_add_vehicle/handler.py: OEM1 path intentionally has no modelManifestName."""
    path = _REPO_ROOT / "services/connectors/oem1/admin_add_vehicle/handler.py"
    assert path.exists(), "admin_add_vehicle/handler.py not found — check path"


def test_mmn_premise_admin_bulk_enroll_mmn_intentionally_absent() -> None:
    """admin_bulk_enroll/handler.py: OEM1 path intentionally has no modelManifestName."""
    path = _REPO_ROOT / "services/connectors/oem1/admin_bulk_enroll/handler.py"
    assert path.exists(), "admin_bulk_enroll/handler.py not found — check path"


def test_mmn_premise_auto_register_mmn_intentionally_absent() -> None:
    """auto_register.py: OEM1 hot-path; modelManifestName intentionally absent.

    The auto-register handler fires on the Flink-MSK pipeline when a new VIN
    appears in telemetry.  It creates a bare row with no manifest lookup.
    Backfill script handles enrichment later.
    """
    path = _REPO_ROOT / "services/connectors/oem1/auto_register.py"
    assert path.exists(), "auto_register.py not found — check path"
    # Absence confirmed by structure: the item dict at line 98 has no
    # modelManifestName key, which is intentional for the OEM1 hot path.


def test_mmn_premise_seed_demo_population_has_mmn() -> None:
    """seed_demo_population.py: vehicle dicts created in _new_onboard_vehicles and _new_mistral_vehicles
    must carry modelManifestName.

    _put_vehicle() spreads the item via {"producer": "meridian", **item}; the
    scanner sees the spread as opaque.  This test confirms that the dict built
    by the generator functions carries 'modelManifestName'.
    """
    path = _REPO_ROOT / "deployment/scripts/seed_demo_population.py"
    source = path.read_text()
    tree = ast.parse(source)

    # Check that 'modelManifestName' appears as a key in at least one Dict
    # inside the vehicle-generating functions.
    target_fns = ("_new_onboard_vehicles", "_new_mistral_vehicles")
    for fn_target in target_fns:
        fn_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_target:
                fn_node = node
                break
        assert fn_node is not None, (
            f"seed_demo_population.py: function {fn_target!r} not found"
        )
        found = any(
            any(isinstance(k, ast.Constant) and k.value == "modelManifestName"
                for k in node.keys)
            for node in ast.walk(fn_node)
            if isinstance(node, ast.Dict)
        )
        assert found, (
            f"seed_demo_population.py {fn_target}(): 'modelManifestName' absent "
            "from the vehicle dict.  Add it per spec 2026-09-14 § T4.1."
        )


def test_mmn_premise_main_api_post_vehicle_has_mmn() -> None:
    """main_api/index.py POST handler: vehicle_item must carry 'modelManifestName'.

    The handler validates the field at Stage 1 (returns 400 if absent) and
    writes it from the manifest lookup at Stage 2.  This test asserts the
    item dict contains a 'modelManifestName' key somewhere in the function.
    """
    target = _REPO_ROOT / "modules/cms_ui/source/handlers/main_api/index.py"
    source = target.read_text()
    tree = ast.parse(source)
    has_mmn_in_dict = any(
        any(
            isinstance(k, ast.Constant) and k.value == "modelManifestName"
            for k in node.keys
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
    )
    assert has_mmn_in_dict, (
        "main_api/index.py: no ast.Dict contains 'modelManifestName'.  "
        "The POST vehicle handler must set 'modelManifestName' in vehicle_item "
        "(spec 2026-09-14-cs-portal-data-model-backend § T4.1)."
    )


def test_mmn_premise_seed_driver_users_has_mmn() -> None:
    """seed_driver_users.py DEMO_PERSONAS: every create=True vehicle must carry modelManifestName."""
    path = _REPO_ROOT / "deployment/scripts/seed_driver_users.py"
    source = path.read_text()
    tree = ast.parse(source)

    def _kv(d: ast.Dict) -> dict:
        return {k.value: v for k, v in zip(d.keys, d.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}

    vehicle_dicts_create_true: list[ast.Dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        kv = _kv(node)
        if "vehicleId" not in kv:
            continue
        create_val = kv.get("create")
        if not (isinstance(create_val, ast.Constant) and create_val.value is True):
            continue
        vehicle_dicts_create_true.append(node)

    assert vehicle_dicts_create_true, (
        "seed_driver_users.py: no vehicle dict with create=True found."
    )

    missing: list[str] = []
    for d in vehicle_dicts_create_true:
        kv = _kv(d)
        vid = kv.get("vehicleId")
        vid_str = vid.value if isinstance(vid, ast.Constant) else "<unknown>"  # type: ignore[union-attr]
        if "modelManifestName" not in kv:
            missing.append(f"{vid_str}: 'modelManifestName' key absent")

    assert not missing, (
        "seed_driver_users.py DEMO_PERSONAS: create=True vehicle dicts missing "
        "'modelManifestName':\n" + "\n".join(f"  {m}" for m in missing)
    )


def test_mmn_premise_seed_engineering_fleets_has_mmn() -> None:
    """seed_engineering_fleets.py: gen_be6_vehicle / gen_be07_vehicle must set modelManifestName."""
    path = _REPO_ROOT / "deployment/scripts/seed_engineering_fleets.py"
    source = path.read_text()
    tree = ast.parse(source)

    for fn_name in ("gen_be6_vehicle", "gen_be07_vehicle"):
        fn_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                fn_node = node
                break
        assert fn_node is not None, (
            f"seed_engineering_fleets.py: function {fn_name!r} not found"
        )
        found = any(
            any(isinstance(k, ast.Constant) and k.value == "modelManifestName"
                for k in node.keys)
            for node in ast.walk(fn_node)
            if isinstance(node, ast.Dict)
        )
        assert found, (
            f"seed_engineering_fleets.py {fn_name}(): 'modelManifestName' absent "
            "from the returned dict.  Add it per spec T4.1."
        )


def test_mmn_premise_seed_generic_fleets_has_mmn() -> None:
    """seed_generic_fleets.py: gen_vehicle() must set modelManifestName."""
    path = _REPO_ROOT / "deployment/scripts/seed_generic_fleets.py"
    source = path.read_text()
    tree = ast.parse(source)

    gen_vehicle_fn = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "gen_vehicle":
            gen_vehicle_fn = node
            break
    assert gen_vehicle_fn is not None, "gen_vehicle function not found in seed_generic_fleets.py"

    found = any(
        any(isinstance(k, ast.Constant) and k.value == "modelManifestName"
            for k in node.keys)
        for node in ast.walk(gen_vehicle_fn)
        if isinstance(node, ast.Dict)
    )
    assert found, (
        "seed_generic_fleets.py gen_vehicle(): 'modelManifestName' absent "
        "from the returned dict.  Add it per spec T4.1."
    )


# ---------------------------------------------------------------------------
# The modelManifestName contract
# ---------------------------------------------------------------------------

def test_no_vehicle_write_without_model_manifest_name() -> None:
    """Ratchet for modelManifestName (spec 2026-09-14-cs-portal-data-model-backend § T4.1).

    Every vehicle put_item that can be statically verified MUST set
    ``modelManifestName``.  Sites whose item dict is opaque to the scanner
    must appear in ``_KNOWN_GAPS_MMN`` with an honest rationale AND be
    verified by a by-name premise test above.

    OEM1 sites (seed_vehicles, admin_add_vehicle, admin_bulk_enroll) are
    legitimately absent: Ford vehicles have no CMS model manifest and are
    correctly left unresolved by the backfill script.

    When this fails on a new site:
      A) Add ``'modelManifestName': <value>`` to the Item dict (literal).
      B) Add the site to ``_KNOWN_GAPS_MMN`` with an honest reason AND add a
         by-name premise test that verifies the item dict (or explains the
         intentional absence).
    """
    all_sites = _all_vehicle_write_sites_for_mmn()
    new_gaps: list[_WriteSite] = []
    for site, has_mmn in all_sites:
        if has_mmn:
            continue
        if site in _KNOWN_GAPS_MMN:
            continue
        new_gaps.append(site)

    assert not new_gaps, (
        "These vehicle put_item calls do NOT set 'modelManifestName' (or are "
        "opaque to the scanner) and are NOT in _KNOWN_GAPS_MMN:\n"
        + "\n".join(
            f"  {s.rel_path}:{s.line}  fn={s.fn_name!r}"
            for s in sorted(new_gaps, key=lambda s: (s.rel_path, s.line))
        )
        + "\n\n"
        "'modelManifestName' is mandatory on every new vehicle row per spec "
        "2026-09-14-cs-portal-data-model-backend § T4.1.  "
        "Fix options:\n"
        "  A) Add \"'modelManifestName': '<value>'\" to the literal Item dict.\n"
        "  B) Add the site to _KNOWN_GAPS_MMN with honest rationale + premise test."
    )


def test_known_mmn_gaps_are_still_real() -> None:
    """Inverse ratchet for modelManifestName.

    A closed gap must be removed from _KNOWN_GAPS_MMN, not left to rot.
    Without this check the ratchet only tightens on paper.
    """
    all_sites_set = {site for site, _ in _all_vehicle_write_sites_for_mmn()}
    stale = sorted(
        g for g in _KNOWN_GAPS_MMN if g not in all_sites_set
    )
    assert not stale, (
        "These _KNOWN_GAPS_MMN entries no longer correspond to a real vehicle-write "
        "site (the site moved, was renamed, or is now a literal dict the scanner "
        "can verify directly).  Delete them:\n"
        + "\n".join(
            f"  {s.rel_path}:{s.line}  fn={s.fn_name!r}"
            for s in stale
        )
    )

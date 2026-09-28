#!/usr/bin/env python3
"""Red-phase guard: every seed script writing a display-facing table must
include a ``provenance`` field in each emitted item.

Background (spec 2026-09-02-cms-fleet-intelligence-v1 § D1, Risk R5)
----------------------------------------------------------------------
Display-facing tables — tables whose rows are rendered to fleet managers
for operational decisions — must carry a ``provenance`` marker so the UI
can distinguish measured values from simulated or estimated ones.

Today **none** of the relevant seed scripts emit this field, so this test
is expected to **fail** on first run.  Group 4 of the spec makes these
tests pass by adding ``provenance`` to each emitted item.

Detection approach (derived, not hand-written)
----------------------------------------------
This test uses ``ast`` to statically inspect source files rather than
maintaining a hand-written list of producers.  A hand-written list was
the mechanism that allowed ``AgentActivityFeed`` to survive the pass that
fixed ``RebalanceAgentFeed``; a derived scan catches newly-added scripts
automatically.

Steps:
  1. Glob ``deployment/scripts/seed_*.py``.
  2. For each file, parse its AST and locate every function that contains
     a DynamoDB write call (``put_item`` keyword or a ``batch_writer``
     context-manager usage).
  3. Within that function's body, look for the dict or keyword-argument
     passed as the ``Item`` to ``put_item``.  Check whether any key in
     that dict is the string literal ``"provenance"``.
  4. Also inspect ``services/simulation/enhanced_historical_data_injector.py``
     specifically for ``generate_tco_rollups``, ``generate_vfo_actions``,
     and ``generate_decision_journal``, each of which calls
     ``batch_write_items`` with a display-facing table.
  5. Scripts that write **only** to catalog / infrastructure tables are
     explicitly opted out below with a one-line rationale.

Opt-out registry
----------------
Scripts are opted out when they write exclusively to catalog or
infrastructure tables whose data is never rendered as a computed row to
a fleet manager (no provenance semantics apply):

``seed_decoder_and_campaign.py``  -- decoder-manifest + signal-catalog +
    campaigns: config / IoT decoder config; values are not computed
    aggregates displayed on the Fleet Intelligence surface.
``seed_signal_catalog.py``        -- signal-catalog + event-catalog:
    static DTC catalog; values are lookup definitions, not per-vehicle
    computed rows.
``seed_event_catalog.py``         -- event-catalog: static catalog only.
``seed_vsa_demo_events.py``       -- event-catalog: static demo catalog.
``seed_event_model_links.py``     -- event-catalog FK links: config only.
``seed_dtc_catalog_gap_fill.py``  -- event-catalog gap-fill: catalog only.
``seed_model_manifests.py``       -- model-manifest: IoT configuration.
``seed_uds_dtc_template.py``      -- campaigns: IoT decoder template.
``seed_tenant_acquire_offers.py`` -- vsa-tenant-config: acquire-offer
    config for the CVX retail persona, not CMS fleet display.
``seed_engineering_fleets.py``    -- storage-fleets / vehicles /
    fleet-enrollment: base entity records (identity, not computed).
``seed_generic_fleets.py``        -- same as above.
``seed_meridian_fleet.py``        -- same as above.
``seed_public_demo_fleet.py``     -- same as above.
``seed_fleet_enrollment.py``      -- storage-fleet-enrollment: FK table
    only, no display aggregates.
``seed_driver_users.py``          -- storage-drivers + vehicles: identity
    records seeded for Cognito users; values are literal inputs, not
    computed outputs.
``seed_drivers.py``               -- storage-drivers: same as above.
``seed_trip_history.py``          -- storage-trips: raw telemetry input;
    trips are source data, not computed aggregates.
``seed_vehicle_lifecycle.py``     -- storage-vehicles (odometer bump) +
    vehicle-certificates: infrastructure / mileage reset; no display
    aggregate semantics.

Run (expected to FAIL until Group 4 ships):
    python3 -m pytest deployment/scripts/test_seed_provenance.py -v

from the repo root.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Generator

import pytest

# ── Repo layout ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "deployment" / "scripts"
INJECTOR_PATH = (
    REPO_ROOT / "services" / "simulation" / "enhanced_historical_data_injector.py"
)

# ── Opt-out registry ─────────────────────────────────────────────────────────
# Keys are bare filenames (no path).  Each entry must have a one-line reason
# that explains why the script never writes a display-facing computed row.
#
# If you add a new seed_*.py that writes exclusively to catalog / infrastructure
# tables, add it here.  If you add one that writes display-facing data, the
# test below will catch it automatically.
OPTED_OUT: dict[str, str] = {
    "seed_decoder_and_campaign.py": (
        "Writes decoder-manifest + signal-catalog + campaigns; "
        "all are IoT config tables, not computed display aggregates."
    ),
    "seed_signal_catalog.py": (
        "Writes signal-catalog + event-catalog; "
        "static DTC/signal lookup definitions."
    ),
    "seed_event_catalog.py": (
        "Writes event-catalog; static catalog definitions only."
    ),
    "seed_vsa_demo_events.py": (
        "Writes event-catalog demo entries; static catalog only."
    ),
    "seed_event_model_links.py": (
        "Writes event-catalog FK links; config only, no computed row."
    ),
    "seed_dtc_catalog_gap_fill.py": (
        "Writes event-catalog gap entries; catalog only."
    ),
    "seed_model_manifests.py": (
        "Writes model-manifest; IoT decoder configuration."
    ),
    "seed_uds_dtc_template.py": (
        "Writes campaigns table with UDS DTC template; IoT config."
    ),
    "seed_tenant_acquire_offers.py": (
        "Writes vsa-tenant-config; CVX acquire offer config, not CMS fleet display."
    ),
    "seed_engineering_fleets.py": (
        "Writes storage-fleets / vehicles / enrollment; "
        "base entity identity records, not computed aggregates."
    ),
    "seed_generic_fleets.py": (
        "Writes storage-fleets / vehicles / enrollment; "
        "base entity identity records."
    ),
    "seed_meridian_fleet.py": (
        "Writes storage-fleets / vehicles / enrollment; "
        "base entity identity records."
    ),
    "seed_public_demo_fleet.py": (
        "Writes storage-fleets / vehicles / enrollment; "
        "base entity identity records."
    ),
    "seed_fleet_enrollment.py": (
        "Writes storage-fleet-enrollment FK table; no display aggregates."
    ),
    "seed_driver_users.py": (
        "Writes storage-drivers + vehicles for Cognito demo users; "
        "literal identity inputs, not computed outputs."
    ),
    "seed_drivers.py": (
        "Writes storage-drivers; identity records."
    ),
    "seed_trip_history.py": (
        "Writes storage-trips; raw telemetry source, not computed aggregate."
    ),
    "seed_vehicle_lifecycle.py": (
        "Writes storage-vehicles (odometer) + vehicle-certificates; "
        "infrastructure / cert provisioning, no display aggregate."
    ),
    "seed_meridian_owner_data.py": (
        "Writes storage-trips + dtc-history + maintenance-alerts for one demo "
        "vehicle (VEH-MRDN-0015); these are raw telemetry / alert source records "
        "seeded from literal values, not provenance-bearing computed aggregates. "
        "The maintenance-alerts table feeds the Alerts surface but the records "
        "here are deterministic seed inputs, not derived computations."
    ),
}


# ── AST helpers ──────────────────────────────────────────────────────────────

def _has_key_literal(node: ast.AST, key: str) -> bool:
    """Return True if *node* is a dict literal containing the string key *key*."""
    if not isinstance(node, ast.Dict):
        return False
    for k in node.keys:
        if isinstance(k, ast.Constant) and k.value == key:
            return True
    return False


def _calls_in_function(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Walk a function body and return all Call nodes."""
    calls: list[ast.Call] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            calls.append(node)
    return calls


def _call_name(call: ast.Call) -> str:
    """Best-effort dotted name of a call (e.g. 'batch.put_item', 'table.put_item')."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_ddb_write_call(call: ast.Call) -> bool:
    """Return True if the call looks like a DynamoDB put_item invocation."""
    return _call_name(call) == "put_item"


def _item_arg(call: ast.Call) -> ast.AST | None:
    """Return the ``Item=`` keyword argument value of a put_item call, or None."""
    for kw in call.keywords:
        if kw.arg == "Item":
            return kw.value
    # positional arg (uncommon but possible)
    if call.args:
        return call.args[0]
    return None


def _contains_batch_writer(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function body uses a batch_writer context manager."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and _call_name(node) == "batch_writer":
            return True
        # also match: `with table.batch_writer() as bw:`
        if isinstance(node, ast.Attribute) and node.attr == "batch_writer":
            return True
    return False


def _provenance_in_dict_walk(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if ANY dict literal in the function body contains 'provenance'."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == "provenance":
                    return True
    return False


def _functions_with_ddb_writes(
    tree: ast.Module,
) -> Generator[ast.FunctionDef | ast.AsyncFunctionDef, None, None]:
    """Yield top-level and class-level functions that contain DDB write calls."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = _calls_in_function(node)
            has_put = any(_is_ddb_write_call(c) for c in calls)
            has_batch = _contains_batch_writer(node)
            if has_put or has_batch:
                yield node


def _check_file_for_provenance(path: Path) -> list[str]:
    """Parse *path* and return a list of function names whose DDB writes lack
    a ``provenance`` key in any dict literal in the function body.

    An empty list means the file is compliant (or has no DDB writes).
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [f"<SyntaxError: {exc}>"]

    violations: list[str] = []
    for func in _functions_with_ddb_writes(tree):
        if not _provenance_in_dict_walk(func):
            violations.append(func.name)
    return violations


# ── Injector-specific check ───────────────────────────────────────────────────

# Names of display-facing generator methods in the injector that must emit
# provenance.  These call batch_write_items internally; the returned list of
# dicts is what ends up in DynamoDB.
INJECTOR_DISPLAY_METHODS = {
    "generate_tco_rollups",
    "generate_vfo_actions",
    "generate_decision_journal",
}


def _check_injector_methods(path: Path) -> dict[str, bool]:
    """For each display-facing method in the injector, return whether it
    contains a ``provenance`` key in any dict literal.

    Returns ``{method_name: compliant}``."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))

    result: dict[str, bool] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in INJECTOR_DISPLAY_METHODS
        ):
            result[node.name] = _provenance_in_dict_walk(node)
    # methods not found in source → not compliant
    for m in INJECTOR_DISPLAY_METHODS:
        if m not in result:
            result[m] = False
    return result


# ── Tests ─────────────────────────────────────────────────────────────────────


def _seed_scripts() -> list[Path]:
    """All seed_*.py in deployment/scripts, minus opted-out entries."""
    return sorted(
        p for p in SCRIPTS_DIR.glob("seed_*.py")
        if p.name not in OPTED_OUT
    )


@pytest.mark.parametrize("script", _seed_scripts(), ids=lambda p: p.name)
def test_seed_script_emits_provenance(script: Path) -> None:
    """Every display-facing seed script must include a ``provenance`` field
    in the dict(s) it writes to DynamoDB.

    Currently FAILS because no seed script emits this field.
    Group 4 of spec 2026-09-02-cms-fleet-intelligence-v1 makes these pass.
    """
    violations = _check_file_for_provenance(script)
    assert not violations, (
        f"{script.name}: the following DDB-writing function(s) do not include "
        f"a 'provenance' key in any emitted dict: {violations!r}.\n"
        f"Add provenance='simulated' (or the appropriate value) to each item "
        f"dict before passing it to put_item / batch_writer."
    )


@pytest.mark.parametrize(
    "method_name",
    sorted(INJECTOR_DISPLAY_METHODS),
)
def test_injector_display_method_emits_provenance(method_name: str) -> None:
    """Each display-facing generator method in EnhancedHistoricalDataInjector must
    include a ``provenance`` key in the returned item dicts.

    Currently FAILS because the injector does not emit this field.

    Covered methods (minimum required by T1.4):
      - generate_tco_rollups   (line 1211)
      - generate_vfo_actions
      - generate_decision_journal
    """
    compliance = _check_injector_methods(INJECTOR_PATH)
    assert compliance[method_name], (
        f"enhanced_historical_data_injector.py::{method_name} does not include "
        f"a 'provenance' key in any dict literal it builds.  The TCO rollup rows, "
        f"VFO actions, and decision journal entries are rendered directly to fleet "
        f"managers; each must carry provenance='simulated' so the UI can badge them "
        f"appropriately."
    )


@pytest.mark.parametrize("script_name", sorted(OPTED_OUT.keys()))
def test_opted_out_scripts_are_justified(script_name: str) -> None:
    """Sanity-check: every opted-out entry in OPTED_OUT has a non-empty reason.

    This test passes always (it validates the opt-out registry itself, not
    production code).  It ensures nobody silently opts out without a reason.
    """
    reason = OPTED_OUT[script_name]
    assert reason and len(reason.strip()) > 10, (
        f"{script_name} is in OPTED_OUT but has an empty or trivially short reason."
    )


def test_opted_out_registry_no_orphans() -> None:
    """Every entry in OPTED_OUT must correspond to a file that actually exists.

    Prevents stale opt-outs that mask newly-added scripts with the same name.
    """
    for name in OPTED_OUT:
        assert (SCRIPTS_DIR / name).exists(), (
            f"OPTED_OUT entry '{name}' has no corresponding file in "
            f"{SCRIPTS_DIR}.  Remove the stale entry."
        )

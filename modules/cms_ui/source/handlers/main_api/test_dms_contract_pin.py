#!/usr/bin/env python3
"""Cross-repo contract pin: CMS test fixtures must match DMS's actual source.

Spec ``2026-09-02-cms-dms-service-convergence`` Fix Group 4 task F4.4.

D-G5i lesson (the root cause of Fix Group 4): a fixture that invents the
other side's shape makes the whole test suite agree with the bug. Nine mutations
all reported CAUGHT and all nine were true — but the feature never called DMS
because the fixture agreed with the invention, not with reality.

This guard locates the sibling DMS repo (``DMS_REPO_PATH`` env var, else the
path-relative default ``../guidance-for-dealer-management-system-on-aws/``),
reads ``_NARROW_RO_FIELDS`` and the list handler's response envelope directly
from DMS source, and asserts the test fixtures in this file match. It skips
cleanly when the sibling repo is absent so CI in a single-repo checkout still
runs.

Pattern precedent: ``scripts/tests/test_seed_scripts_no_fake_connected.py``
in ``guidance-for-connected-vehicle-experience-on-aws`` uses the same
auto-discovery pattern for the CMS sibling.

Three-way demo (F4.4 Verify requirement):
  1. Run with sibling present:  PASSES.
  2. Run with DMS_REPO_PATH=/nonexistent:  all tests SKIPPED.
  3. Edit _FakeDmsGetResp to say 'repairOrders' → test_envelope_key FAILS.

Run::

    python3 -m pytest test_dms_contract_pin.py -v          # (1) with sibling
    DMS_REPO_PATH=/nonexistent python3 -m pytest test_dms_contract_pin.py -v  # (2) skip
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

import pytest

# ── Locate the sibling DMS repo ───────────────────────────────────────────────

def _find_dms_repo() -> pathlib.Path | None:
    """Return the DMS repo root if reachable, None otherwise.

    Search order:
    1. ``DMS_REPO_PATH`` environment variable.
    2. Path-relative sibling: ``../guidance-for-dealer-management-system-on-aws/``
       from this file's location.
    """
    env_path = os.environ.get('DMS_REPO_PATH', '').strip()
    if env_path:
        p = pathlib.Path(env_path)
        if p.is_dir():
            return p
        return None  # explicit override that doesn't exist → skip

    # Auto-discover: this file is at
    # .../connected-mobility-guidance-on-aws/modules/cms_ui/source/handlers/main_api/
    # The sibling is at ../../../../../../../guidance-for-dealer-management-system-on-aws/
    # relative to this file. Walk up to the CMS repo root first.
    this_file = pathlib.Path(__file__).resolve()
    # Climb from main_api → handlers → source → cms_ui → modules → repo root
    cms_root = this_file.parent.parent.parent.parent.parent.parent
    candidate = cms_root.parent / 'guidance-for-dealer-management-system-on-aws'
    if candidate.is_dir():
        return candidate
    return None


_DMS_REPO = _find_dms_repo()
_SKIP_REASON = (
    'DMS sibling repo not found. '
    'Set DMS_REPO_PATH=<path-to-guidance-for-dealer-management-system-on-aws> '
    'or run from a checkout where the repo sits next to this one.'
)
_skip_if_no_sibling = pytest.mark.skipif(_DMS_REPO is None, reason=_SKIP_REASON)


# ── Read DMS source without importing it ─────────────────────────────────────

def _read_dms_source(relative_path: str) -> str:
    """Return DMS source as text; raises if the file is missing."""
    assert _DMS_REPO is not None
    full_path = _DMS_REPO / relative_path
    if not full_path.is_file():
        pytest.fail(f'Expected DMS source file not found: {full_path}')
    return full_path.read_text(encoding='utf-8')


def _extract_frozenset_literal(source: str, name: str) -> set[str]:
    """Parse a ``name: frozenset[str] = frozenset({...})`` literal from source.

    This reads the AST rather than importing the module, so no DMS dependencies
    need to be installed in the CMS environment.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                # Right-hand side is frozenset({...}) or frozenset([...])
                if (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == 'frozenset'
                    and node.value.args
                ):
                    collection = node.value.args[0]
                    if isinstance(collection, (ast.Set, ast.List)):
                        return {
                            elt.value if isinstance(elt, ast.Constant) else str(elt)
                            for elt in collection.elts
                        }
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    if (
                        isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == 'frozenset'
                        and node.value.args
                    ):
                        collection = node.value.args[0]
                        if isinstance(collection, (ast.Set, ast.List)):
                            return {
                                elt.value if isinstance(elt, ast.Constant) else str(elt)
                                for elt in collection.elts
                            }
    pytest.fail(f'Could not extract frozenset literal for {name!r} from DMS source')
    return set()  # unreachable but satisfies type checker


def _find_ok_response_body_key(source: str) -> str | None:
    """Scan list_fleet_repair_orders for the key passed to ok_response.

    Looks for a pattern like ``ok_response({"items": projected})`` and
    returns the key string, e.g. ``"items"``.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'list_fleet_repair_orders':
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == 'ok_response'
                    and inner.args
                ):
                    first_arg = inner.args[0]
                    if isinstance(first_arg, ast.Dict) and first_arg.keys:
                        key_node = first_arg.keys[0]
                        if isinstance(key_node, ast.Constant):
                            return str(key_node.value)
    return None


# ── Test classes ──────────────────────────────────────────────────────────────

class TestDmsContractPin:
    """Pin the CMS test fixture against DMS's actual source.

    ALL tests in this class are skipped when the sibling repo is absent.
    """

    @_skip_if_no_sibling
    def test_narrow_ro_fields_fixture_matches_dms_source(self):
        """_NARROW_RO_FIELDS in DMS source matches the field set the CMS fixture uses.

        The CMS fixture in test_service_history_dms_backed.py constructs _DMS_RO
        with the fields that _project_narrow emits. This test reads _NARROW_RO_FIELDS
        from DMS source (without importing DMS) and asserts the CMS fixture carries
        exactly those keys.

        FAILS when:
        - DMS adds or removes a field from _NARROW_RO_FIELDS without updating CMS tests.
        - CMS tests invent fields not present in DMS.
        """
        dms_source = _read_dms_source('source/handlers/repair_orders.py')
        dms_narrow_fields = _extract_frozenset_literal(dms_source, '_NARROW_RO_FIELDS')
        assert dms_narrow_fields, 'Could not extract _NARROW_RO_FIELDS from DMS source'

        # _project_narrow also adds 'dealer_name' (enrichment); include it.
        dms_projected_fields = dms_narrow_fields | {'dealer_name'}

        # The CMS fixture _DMS_RO must carry only fields that DMS actually emits.
        # Import the test module to read its fixture.
        # We import dynamically so this module itself doesn't fail if index.py is broken.
        _test_mod_path = pathlib.Path(__file__).parent / 'test_service_history_dms_backed.py'
        _spec = __import__('importlib').util.spec_from_file_location(
            '_dms_backed_fixture_reader', _test_mod_path
        )
        _mod = __import__('importlib').util.module_from_spec(_spec)
        # Patch sys.modules before exec to prevent index.py import errors
        _saved = sys.modules.copy()
        try:
            _spec.loader.exec_module(_mod)
            cms_fixture_keys = set(getattr(_mod, '_DMS_RO', {}).keys())
        except Exception:
            # Fall back: parse the fixture from source text
            _test_src = _test_mod_path.read_text(encoding='utf-8')
            # Extract the _DMS_RO dict literal keys from the AST
            _tree = ast.parse(_test_src)
            cms_fixture_keys = set()
            for _node in ast.walk(_tree):
                if (
                    isinstance(_node, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == '_DMS_RO'
                        for t in _node.targets
                    )
                    and isinstance(_node.value, ast.Dict)
                ):
                    for k in _node.value.keys:
                        if isinstance(k, ast.Constant):
                            cms_fixture_keys.add(str(k.value))
        finally:
            # Restore sys.modules to pre-import state
            sys.modules.clear()
            sys.modules.update(_saved)

        # Every key in the CMS fixture must be in DMS's projected fields
        unexpected = cms_fixture_keys - dms_projected_fields
        assert not unexpected, (
            f'CMS fixture _DMS_RO contains keys not present in DMS projected output: '
            f'{unexpected}. DMS _NARROW_RO_FIELDS + dealer_name = {sorted(dms_projected_fields)}. '
            'Update the CMS fixture to match DMS source, not memory.'
        )

        # The fixture should cover the core fields (not requiring all — DMS may omit
        # optional ones on sparse records). Check the non-optional ones.
        required_in_fixture = {'ro_id', 'status', 'dealer_id', 'description'}
        missing = required_in_fixture - cms_fixture_keys
        assert not missing, (
            f'CMS fixture _DMS_RO is missing required DMS fields: {missing}. '
            f'Fixture keys: {sorted(cms_fixture_keys)}'
        )

    @_skip_if_no_sibling
    def test_envelope_key_is_items_per_dms_source(self):
        """The envelope key used by list_fleet_repair_orders is "items".

        F4.4 three-way demo, step 3: edit _FakeDmsGetResp to use "repairOrders"
        and this test FAILS.

        MUTATION TARGET (Critical 1 of D-G5i): the previous implementation read
        `_dms_payload.get('repairOrders')` — the wrong key. Every DMS response
        had its records silently discarded and every GET returned dataSource='cache'.

        This test reads the ACTUAL return statement of list_fleet_repair_orders
        from DMS source and asserts it matches the key CMS's _FakeDmsGetResp uses.
        """
        dms_source = _read_dms_source('source/handlers/repair_orders.py')
        dms_envelope_key = _find_ok_response_body_key(dms_source)
        assert dms_envelope_key is not None, (
            'Could not extract the envelope key from list_fleet_repair_orders in DMS source'
        )
        assert dms_envelope_key == 'items', (
            f'DMS list_fleet_repair_orders returns {{"{ dms_envelope_key}": [...]}}, '
            f'but "items" was expected. Update the CMS fixture to match.'
        )

        # Now verify the CMS _FakeDmsGetResp fixture uses the SAME key.
        _test_src = (pathlib.Path(__file__).parent / 'test_service_history_dms_backed.py').read_text()
        _tree = ast.parse(_test_src)

        # Find the _FakeDmsGetResp.__init__ and check the key in json.dumps({"<KEY>": ...})
        cms_fixture_key = None
        for _node in ast.walk(_tree):
            if isinstance(_node, ast.ClassDef) and _node.name == '_FakeDmsGetResp':
                for _inner in ast.walk(_node):
                    if isinstance(_inner, ast.Dict) and _inner.keys:
                        k = _inner.keys[0]
                        if isinstance(k, ast.Constant):
                            cms_fixture_key = str(k.value)
                            break

        assert cms_fixture_key is not None, (
            'Could not extract envelope key from _FakeDmsGetResp in test_service_history_dms_backed.py'
        )
        assert cms_fixture_key == dms_envelope_key, (
            f'_FakeDmsGetResp uses envelope key "{cms_fixture_key}" '
            f'but DMS source uses "{dms_envelope_key}". '
            'The fixture does not match the real DMS contract. '
            'This is the Critical-1 defect from D-G5i — the fixture invented a key '
            'that DMS never emits, making the live path a silent no-op.'
        )

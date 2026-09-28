# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression: `_load_catalog` must resolve `products.json` in BOTH layouts.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.7.
Issue `issues/2026-09-12-products-catalog-flat-bundle-path-500/`.

The two layouts are genuinely different and only one of them was covered:

    source tree      services/connectors/subscriptions/products.json   (parent of handler dir)
    Lambda bundle    /var/task/products.json                           (same dir as handler.py)

Every pre-existing test in `test_handler.py` imports the handler from the source
tree, where `dirname(dirname(handler.py))` happens to be the directory holding
`products.json`. That made a real 500 on deployed staging invisible to the suite
and to both review gates.

These tests build each layout on a real filesystem and load `handler.py` from it,
so neither can pass for the wrong reason. The flat-bundle case asserts the
*absence* of the parent-dir file as a precondition — without that, the case
would silently degrade into a duplicate of the source-tree case.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

_HANDLER_SRC = Path(__file__).resolve().parents[1] / "handler.py"
_CATALOG_SRC = Path(__file__).resolve().parents[2] / "products.json"


def _load_handler_from(handler_path: Path, mod_name: str):
    """Import `handler.py` from an arbitrary path under a unique module name.

    A unique name per call is required: `sys.modules['handler']` is shared, and
    a bare `import handler` would bind to whichever layout loaded first — the
    exact shadowing defect recorded in this spec's RESUME.md.
    """
    spec = importlib.util.spec_from_file_location(mod_name, handler_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return module


@pytest.fixture(autouse=True)
def _cleanup_modules():
    created = set(sys.modules)
    yield
    for name in set(sys.modules) - created:
        if name.startswith("_t37_handler_"):
            sys.modules.pop(name, None)


def test_flat_bundle_layout_resolves_catalog(tmp_path):
    """Lambda layout: handler.py and products.json co-located, nothing above.

    Pre-fix this raises FileNotFoundError('/var/products.json'-equivalent),
    reproducing the deployed 500.
    """
    task = tmp_path / "var" / "task"
    task.mkdir(parents=True)
    shutil.copy2(_HANDLER_SRC, task / "handler.py")
    shutil.copy2(_CATALOG_SRC, task / "products.json")

    # Precondition — the parent-dir path MUST be absent, or this test would
    # pass via the source-tree branch and prove nothing about the bundle.
    parent_copy = tmp_path / "var" / "products.json"
    assert not parent_copy.exists(), "flat-bundle case must not have a parent-dir catalog"

    mod = _load_handler_from(task / "handler.py", "_t37_handler_flat")
    mod._load_catalog.cache_clear()

    catalog = mod._load_catalog()
    assert catalog.get("products"), "catalog loaded but carries no products"


def test_source_tree_layout_still_resolves_catalog(tmp_path):
    """Source layout: products.json one dir UP from handler.py. Must not regress."""
    pkg = tmp_path / "subscriptions" / "products"
    pkg.mkdir(parents=True)
    shutil.copy2(_HANDLER_SRC, pkg / "handler.py")
    shutil.copy2(_CATALOG_SRC, pkg.parent / "products.json")

    # Precondition — the same-dir path MUST be absent, so this case can only
    # be satisfied by the parent-dir branch.
    assert not (pkg / "products.json").exists(), "source-tree case must not have a co-located catalog"

    mod = _load_handler_from(pkg / "handler.py", "_t37_handler_srctree")
    mod._load_catalog.cache_clear()

    catalog = mod._load_catalog()
    assert catalog.get("products"), "catalog loaded but carries no products"


def test_missing_catalog_raises_not_silently_empty(tmp_path):
    """Negative control: with no catalog anywhere, the failure must be loud.

    Guards against a "fix" that swallows the error and returns `{}` — which
    would turn the 500 into a 200 serving an empty catalog, a worse outcome
    because the demo would show an empty Data Products screen with no signal.
    """
    task = tmp_path / "task"
    task.mkdir(parents=True)
    shutil.copy2(_HANDLER_SRC, task / "handler.py")

    mod = _load_handler_from(task / "handler.py", "_t37_handler_missing")
    mod._load_catalog.cache_clear()

    with pytest.raises(FileNotFoundError):
        mod._load_catalog()


def test_products_handler_returns_200_in_flat_bundle_layout(tmp_path):
    """End-to-end at handler level: the route itself must be 200 in the Lambda layout.

    `_load_catalog` is the unit, but the 500 the operator saw came from
    `products_handler`. Asserting the status code closes the gap between
    "the loader works" and "the route works".
    """
    task = tmp_path / "task"
    task.mkdir(parents=True)
    shutil.copy2(_HANDLER_SRC, task / "handler.py")
    shutil.copy2(_CATALOG_SRC, task / "products.json")

    mod = _load_handler_from(task / "handler.py", "_t37_handler_route")
    mod._load_catalog.cache_clear()

    event = {"requestContext": {"authorizer": {"claims": {
        "sub": "00000000-0000-0000-0000-000000000000",
        "cognito:groups": "subscriber",
    }}}}
    resp = mod.products_handler(event, None)

    assert resp["statusCode"] == 200, f"expected 200, got {resp['statusCode']}: {resp.get('body')}"
    body = json.loads(resp["body"])
    assert body.get("products"), "200 returned but products list is empty"

#!/usr/bin/env python3
"""Guard: `DMS_API_ENDPOINT` reaches the main_api Lambda's environment.

Spec `2026-09-02-cms-dms-service-convergence` T4.2.

WHY A CDK TEST FOR ONE ENV VAR.

`storage_stack.py:2118` records the precedent in this repo's own words: the DMS
alert publisher shipped with `VEHICLES_TABLE` set while the handler read
`VEHICLES_TABLE_NAME`, so every VIN lookup hit a nonexistent default table,
every alert was skipped as "VIN not resolved", and the Lambda reported success
with zero batch failures while DMS received nothing. Forty-two tests passed
through that, "because the handler tests stub the client and the CDK tests
asserted CDK's own names."

`GET /api/v1/dealers` has the identical exposure. The handler reads
`DMS_API_ENDPOINT`; if CDK sets any other key the route fails closed with a 503
on every call — correct behaviour for an unconfigured stage, and indistinguishable
from a misspelled key. So the name is derived FROM THE HANDLER SOURCE here rather
than restated, which is what makes this test able to catch a rename on either
side.

Run:
    python3 -m pytest deployment/stacks/test_dms_api_endpoint_env.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLER = (
    _REPO_ROOT / "modules" / "cms_ui" / "source" / "handlers" / "main_api" / "index.py"
)
_UI_STACK = _REPO_ROOT / "deployment" / "stacks" / "ui_stack.py"


def _handler_source() -> str:
    return _HANDLER.read_text(encoding="utf-8")


def _ui_stack_source() -> str:
    return _UI_STACK.read_text(encoding="utf-8")


def test_handler_reads_exactly_one_dms_endpoint_env_var() -> None:
    """Premise check: derive the key name instead of asserting a literal.

    If the route ever reads two different env vars, or none, the assertions below
    would be measuring the wrong thing — so establish the premise first.
    """
    keys = set(re.findall(r"os\.environ\.get\(\s*'(DMS_[A-Z_]+)'", _handler_source()))
    assert keys == {"DMS_API_ENDPOINT"}, (
        f"expected the dealers route to read exactly DMS_API_ENDPOINT, found {keys}"
    )


def test_ui_stack_sets_the_key_the_handler_reads() -> None:
    key = "DMS_API_ENDPOINT"
    assert f"'{key}':" in _ui_stack_source(), (
        f"ui_stack.py does not set {key} — GET /api/v1/dealers would return 503 on "
        "every call, which is indistinguishable from an intentionally unconfigured "
        "stage. See storage_stack.py:2118 for the same defect shipped once already."
    )


def test_env_var_is_sourced_from_context_or_environment_not_hardcoded() -> None:
    """No baked endpoint. A literal here would be a per-stage value in shared code.

    Same class as CVX's `vsa.ts` finding — baked per-stage literals in a file
    that must ship (~/.kiro/steering/public-mirror-publish.md).
    """
    src = _ui_stack_source()
    idx = src.index("'DMS_API_ENDPOINT':")
    block = src[idx: idx + 400]
    assert "try_get_context('dmsApiEndpoint')" in block, (
        "DMS_API_ENDPOINT should resolve from CDK context 'dmsApiEndpoint'"
    )
    assert "os.environ.get('DMS_API_ENDPOINT'" in block, (
        "DMS_API_ENDPOINT should fall back to the deploying shell's environment"
    )
    assert "https://" not in block, (
        "a literal URL is baked into ui_stack.py — this file ships, and a per-stage "
        "endpoint does not belong in it"
    )


def test_empty_value_does_not_fail_the_synth() -> None:
    """A stage with no DMS must still deploy.

    Deliberately NOT the fail-closed-at-synth posture CVX's Tier B fields use.
    There the missing value made an IAM or VPC decision wrong at synth time; here
    it only disables one route, which already fails closed at runtime with an
    explanatory 503. Making the whole UI stack un-deployable because a stage has
    no DMS would be a worse trade, and the `dmsEventBusName` gate in
    storage_stack.py sets the precedent for treating absent-DMS as a supported
    configuration.

    Asserted structurally: the expression ends in `.strip()` over an `or`
    fallback chain, so it yields `''` rather than raising or returning None.
    """
    src = _ui_stack_source()
    idx = src.index("'DMS_API_ENDPOINT':")
    block = src[idx: idx + 400]
    assert "or os.environ.get('DMS_API_ENDPOINT', '')" in block, (
        "the fallback must default to '' so an unconfigured stage synthesizes"
    )
    assert ").strip()" in block


@pytest.mark.parametrize(
    "frontend_dir",
    ["modules/cms_ui/source/frontend/src"],
)
def test_no_cms_frontend_source_calls_the_dms_api_directly(frontend_dir: str) -> None:
    """Spec D3: the read path is CMS backend -> DMS, never browser -> DMS.

    This assertion replaced a WRONG one, and the correction is worth recording
    because the spec text is what misled it. D3 says browser-direct was rejected
    partly because it "re-adds a DMS endpoint to CMS's runtimeConfig — the
    `dmsApiEndpoint` key that 2026-08-31-dms-standalone-ui T4.4 removed hours
    before this spec was written". A first draft of this test therefore asserted
    no runtime-config file mentions `dmsApiEndpoint`. It failed:
    `deployment/scripts/generate_runtime_config.py:79` still writes that key,
    gated on the `DMS_API_ENDPOINT` shell variable.

    What T4.4 actually removed was the CONSUMER — `config/api.ts:55-58` records
    that `getDmsApiEndpoint()` is gone "because CMS no longer calls DMS". The
    producer survived, so the key is dead residue rather than a live coupling.
    Filed as a follow-on; not fixed here, because that script belongs to the
    standalone-UI spec's surface.

    So the durable property is not "the key is absent" — it is "no frontend
    source calls DMS". That is what D3 protects, it is unaffected by dead config
    residue, and it fails loudly if someone reverses the decision by pointing a
    component at DMS directly.
    """
    root = _REPO_ROOT / frontend_dir
    assert root.is_dir(), f"frontend source tree not found at {root}"

    offenders: list[str] = []
    scanned = 0
    for path in root.rglob("*.ts*"):
        if "__tests__" in path.parts or path.name.endswith((".test.ts", ".test.tsx")):
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # Comments are documentation, and this repo's own guard history shows
            # that flagging a comment which explains a removal is a false
            # positive (see test_dealers_endpoint.py's note on the same trap).
            if stripped.startswith(("//", "*", "/*")):
                continue
            if "/api/dms/" in line or "getDmsApiEndpoint" in line:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{line_no}")

    # Premise guard: an absence check over an unscanned tree passes trivially.
    assert scanned > 100, (
        f"only {scanned} frontend sources scanned — the glob is matching almost "
        "nothing, so the assertion below is vacuous"
    )
    assert not offenders, (
        "CMS frontend source calls the DMS API directly, reversing spec D3 "
        f"(backend read-through, one origin, one auth story): {offenders}"
    )

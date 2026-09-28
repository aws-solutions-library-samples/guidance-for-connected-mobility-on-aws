# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guard: every CS-UI context key the stack reads is threaded by BOTH Makefile targets.

Prevention half of
``issues/2026-09-12-cs-portal-subscriptions-endpoint-never-threaded/``.

The defect this guards against is NOT a code defect and no existing test could
see it. ``runtimeConfigContract.test.ts`` asserts the TypeScript
``RuntimeConfig`` interface against ``connected_services_ui_stack.py``'s emitted
key names, and both sides agreed the whole time. What was missing sat one layer
further out: the stack called
``try_get_context("connectedServicesUiSubscriptionsApiEndpoint")`` and
``deployment/Makefile`` never passed ``-c`` for it, so a deploy emitted an empty
string and ``DataProductsView`` silently served its fixture.

So the contract asserted here is the one nobody owned:

    stack reads context key K  ==>  both CS-UI Makefile targets pass -c K=...

Failure mode without it: a new ``connectedServicesUi*`` context key is added to
the stack, the interface test stays green because the stack does emit it, and
the value is empty in every deployed environment until a human curls
``runtime-config.js``. That is exactly how the subscriptions endpoint went
5 days undetected across two review gates.

Deliberately source-text based rather than a synth assertion: the bug lives in
the Makefile, which CDK never parses, so no amount of ``cdk synth`` inspection
can reach it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STACK = _REPO_ROOT / "deployment" / "stacks" / "connected_services_ui_stack.py"
_MAKEFILE = _REPO_ROOT / "deployment" / "Makefile"

# The two targets that deploy/synth the CS UI stack. Both must thread every key:
# synthing with a value and deploying without it is how a config gap ships green.
_TARGETS = ("synth-connected-services", "deploy-connected-services")

# try_get_context("connectedServicesUiFoo") / .try_get_context('connectedServicesUiFoo')
_CTX_READ = re.compile(
    r"""try_get_context\(\s*["'](connectedServicesUi[A-Za-z0-9_]*)["']\s*\)"""
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected to exist: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"unexpectedly empty: {path}"
    return text


def _target_body(makefile_text: str, target: str) -> str:
    """Return the recipe body for ``target``.

    Bounded to the target so a key threaded by some *other* target cannot
    satisfy the assertion — the whole-file ``in`` check is the presence
    assertion this repo has repeatedly mistaken for a real one (see the
    ``cs-simulator-oem2-manifest-path`` FG4.2 lesson: a whole-file
    ``contains`` coincides with wiring only until the code moves).
    """
    start = makefile_text.find(f"\n{target}:")
    assert start != -1, (
        f"Makefile target {target!r} not found. If it was renamed, update "
        f"_TARGETS in this test — do NOT delete the assertion."
    )
    # The recipe ends at the next line that starts in column 0 and is not a
    # continuation, i.e. the next target/variable/comment block.
    rest = makefile_text[start + 1 :]
    end = len(rest)
    for m in re.finditer(r"\n(?=[A-Za-z0-9_.\-]+\s*:|\.PHONY|#)", rest):
        # Skip matches still inside a backslash-continued line.
        if rest[: m.start()].rstrip().endswith("\\"):
            continue
        end = m.start()
        break
    body = rest[:end]
    assert body.strip(), f"recipe body for {target!r} resolved empty"
    return body


def test_anti_vacuity_stack_declares_context_keys() -> None:
    """The extractor must actually find keys, or every assertion below is vacuous."""
    keys = set(_CTX_READ.findall(_read(_STACK)))
    assert len(keys) >= 5, (
        "Expected at least 5 connectedServicesUi* context reads in "
        f"{_STACK.name}; found {sorted(keys)}. A regex that matches nothing "
        "makes the threading test pass for the wrong reason."
    )


@pytest.mark.parametrize("target", _TARGETS)
def test_anti_vacuity_target_body_is_extractable(target: str) -> None:
    """Each target's recipe must be locatable and non-trivial."""
    body = _target_body(_read(_MAKEFILE), target)
    assert "cdk " in body, f"{target}'s recipe does not invoke cdk; extraction is wrong"


@pytest.mark.parametrize("target", _TARGETS)
def test_every_stack_context_key_is_threaded_by_target(target: str) -> None:
    """THE contract: no context key the stack reads may go unthreaded."""
    keys = sorted(set(_CTX_READ.findall(_read(_STACK))))
    body = _target_body(_read(_MAKEFILE), target)

    missing = [k for k in keys if f"-c {k}=" not in body]

    assert not missing, (
        f"Makefile target {target!r} does not pass -c for: {missing}.\n"
        "The stack reads these via try_get_context, so an unthreaded key "
        "resolves to '' and ships a silently-empty value into "
        "runtime-config.js — invisible to runtimeConfigContract.test.ts, "
        "which only compares the TS interface against the stack's emitted "
        "key names.\n"
        f"Fix: add '-c {missing[0] if missing else 'KEY'}=$$VAR \\' to "
        f"{target}, and give VAR a value in deployment/config/<stage>.env."
    )


@pytest.mark.parametrize("target", _TARGETS)
def test_threaded_values_use_make_escaped_shell_expansion(target: str) -> None:
    """Presence of ``-c KEY=`` is not enough — the VALUE must reach the shell.

    In a Makefile recipe, ``$VAR`` is a **make** expansion and ``$$VAR`` is what
    reaches the shell. A single ``$`` therefore expands against make's (empty)
    variable table and silently passes a wrong or empty value, while every
    presence-based assertion stays green.

    This is not hypothetical: on 2026-09-12 BOTH the subscriptions and the
    simulation endpoints shipped with single-``$`` forms in *two* places each —
    the shell assignment ``VAR=${VAR:-}`` (which overwrote the value sourced
    from ``<stage>.env`` with empty) and the ``-c KEY=$VAR`` flag. `cdk synth`
    exited 0 and emitted ``""`` both times. The first version of the guard above
    passed throughout, because it only checked that ``-c KEY=`` appeared.

    Asserting on the property named in the test title, not adjacent to it.
    """
    keys = sorted(set(_CTX_READ.findall(_read(_STACK))))
    body = _target_body(_read(_MAKEFILE), target)

    bad_flags: list[str] = []
    for k in keys:
        m = re.search(rf"-c {re.escape(k)}=(\S+)", body)
        if not m:
            continue  # absence is the other test's job
        value = m.group(1)
        # Literal values (e.g. -c foo=true) are fine; only variable refs matter.
        if value.startswith("$") and not value.startswith("$$"):
            bad_flags.append(f"-c {k}={value}")

    # Same defect shape on the shell-assignment lines that feed those flags.
    bad_assigns = [
        line.strip()
        for line in body.splitlines()
        if re.match(r"\s*[A-Z0-9_]+=\$\{[A-Z0-9_]+:?-", line)
        and not re.match(r"\s*[A-Z0-9_]+=\$\$\{", line)
    ]

    assert not bad_flags and not bad_assigns, (
        f"Target {target!r} uses single-$ (make) expansion where double-$$ "
        "(shell) is required — the value will be empty or garbage at deploy "
        "time while synth still exits 0.\n"
        f"  bad -c flags:      {bad_flags}\n"
        f"  bad assignments:   {bad_assigns}\n"
        "Fix: double every '$' that is meant for the shell."
    )



def test_subscriptions_endpoint_specifically_is_threaded() -> None:
    """Regression pin for the originating defect.

    Named explicitly and separately from the general loop: the general test
    would also pass if someone deleted the stack's context read, which would
    'fix' the test by removing the feature. This one fails in that case.
    """
    key = "connectedServicesUiSubscriptionsApiEndpoint"
    stack_text = _read(_STACK)
    assert f'try_get_context("{key}")' in stack_text, (
        f"{key} is no longer read by the stack. If the subscription plane was "
        "intentionally removed, delete this test in the same commit and say so; "
        "otherwise this is the 2026-09-12 defect regressing."
    )
    makefile_text = _read(_MAKEFILE)
    for target in _TARGETS:
        assert f"-c {key}=" in _target_body(makefile_text, target), (
            f"{key} is read by the stack but not threaded by {target}. This is "
            "the exact defect recorded in "
            "issues/2026-09-12-cs-portal-subscriptions-endpoint-never-threaded/."
        )

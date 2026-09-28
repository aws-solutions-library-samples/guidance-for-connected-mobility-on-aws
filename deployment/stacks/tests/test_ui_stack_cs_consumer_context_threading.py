# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guard: every ``cs*`` context key ``ui_stack.py`` reads is threaded by ``phase1``.

Spec ``2026-09-10-cms-connected-services-consumer``, filed by T2.5.

WHAT WENT WRONG
---------------
``ui_stack.py`` read ``csProducerApiEndpoint`` and ``csSubscriptionId`` via
``try_get_context``. Nothing wrote them — not ``deployment/Makefile``, not
``config/staging.env``, not ``cdk.context.json``. Both therefore synthesized to
``''`` on every deploy, ``ProxyConfig.from_env()`` raised ``ValueError``, and all
three Connected Services routes answered 502 ``config_missing``.

Every existing test was green, and correctly so.
``test_ui_stack_connected_services_consumer.py`` asserts the env-var NAMES and
the ``DEPLOY_SUBSCRIPTIONS`` gate, and both were right; it synthesizes with
explicit ``env_overrides``, which is precisely the state a real deploy did not
have. The gap sat one layer further out than any synth assertion can reach,
because the missing half lives in a Makefile and CDK never parses one.

This is the SAME DEFECT, the same week, as the sibling module's
``issues/2026-09-12-cs-portal-subscriptions-endpoint-never-threaded/``: a
declared-and-read context key with no producer. Two independent instances is why
this is a test and not a comment. Structure and several of the assertions below
are deliberately modelled on that issue's prevention half,
``test_connected_services_ui_context_threading.py`` — a second copy of a good
guard beats a shared helper that neither spec owns.

CONTRACT
--------
    ui_stack.py reads context key K matching ``cs[A-Z]*``
        ==> Makefile target ``phase1`` passes ``-c K=$$VAR``

Note the ``$$``: ``-c K=$VAR`` in a Makefile recipe is a **make** expansion,
which is almost always empty, so the flag arrives with no value while ``make``
still exits 0. The final test below asserts the escaping, not just the presence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STACK = _REPO_ROOT / "deployment" / "stacks" / "ui_stack.py"
_MAKEFILE = _REPO_ROOT / "deployment" / "Makefile"
_STAGING_ENV = _REPO_ROOT / "deployment" / "config" / "staging.env"

# `phase1` is the target that runs `cdk deploy ... cms-{stage}-ui`. `staging-deploy`
# reaches the same stack only by chaining `deploy-all` -> `phase1`, so asserting
# on `phase1` covers both without asserting on a target that merely delegates.
_TARGETS = ("phase1",)

# Scoped to `cs`+CapitalLetter so this contract covers the Connected Services
# CONSUMER keys and not the ~8 unrelated context keys `ui_stack.py` also reads
# (`uiCustomDomain`, `wafWebAclArn`, `cms.enable_*`, ...). Widening the regex
# would make the test fail on keys this spec does not own and cannot fix, which
# is how a guard gets deleted instead of satisfied.
_CTX_READ = re.compile(r"""try_get_context\(\s*["'](cs[A-Z][A-Za-z0-9_]*)["']\s*\)""")

# The env keys `phase1` reads out of `config/<stage>.env` to build those flags.
_REQUIRED_ENV_KEYS = ("CS_PRODUCER_API_ENDPOINT", "CS_SUBSCRIPTION_ID")


def _read(path: Path) -> str:
    assert path.is_file(), f"expected to exist: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"unexpectedly empty: {path}"
    return text


def _target_body(makefile_text: str, target: str) -> str:
    """Return the recipe body for ``target``.

    Bounded to the target, so a key threaded by some OTHER target cannot satisfy
    the assertion. A whole-file ``in`` check coincides with real wiring only
    until the code moves, and this repo has mistaken one for the other before.
    """
    start = makefile_text.find(f"\n{target}:")
    assert start != -1, (
        f"Makefile target {target!r} not found. If it was renamed, update "
        f"_TARGETS in this test — do NOT delete the assertion."
    )
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


def test_anti_vacuity_stack_declares_cs_context_keys() -> None:
    """The extractor must find the keys, or every assertion below is vacuous.

    An absence/threading assertion over an EMPTY key set passes trivially. This
    spec has already shipped one test whose name promised a property its body
    did not check; a regex that silently stops matching is the same failure with
    no name to notice.
    """
    keys = set(_CTX_READ.findall(_read(_STACK)))
    assert keys >= {"csProducerApiEndpoint", "csSubscriptionId"}, (
        f"Expected ui_stack.py to read csProducerApiEndpoint and "
        f"csSubscriptionId via try_get_context; found {sorted(keys)}."
    )


@pytest.mark.parametrize("target", _TARGETS)
def test_anti_vacuity_target_body_is_extractable(target: str) -> None:
    body = _target_body(_read(_MAKEFILE), target)
    assert "cdk deploy" in body, (
        f"{target}'s recipe does not invoke `cdk deploy`; the extraction above "
        "is finding the wrong block and the threading assertion is meaningless."
    )


@pytest.mark.parametrize("target", _TARGETS)
def test_every_cs_context_key_is_threaded_by_target(target: str) -> None:
    """THE contract: no ``cs*`` context key the stack reads may go unthreaded."""
    keys = sorted(set(_CTX_READ.findall(_read(_STACK))))
    body = _target_body(_read(_MAKEFILE), target)

    missing = [k for k in keys if f"-c {k}=" not in body]

    assert not missing, (
        f"Makefile target {target!r} does not pass -c for: {missing}.\n"
        "ui_stack.py reads these via try_get_context, so an unthreaded key "
        "resolves to '' and the main_api Lambda receives an empty "
        "CS_PRODUCER_API_ENDPOINT / CS_SUBSCRIPTION_ID. Every Connected "
        "Services route then answers 502 config_missing — which is correct "
        "fail-closed behaviour and therefore indistinguishable, from the UI, "
        "from the producer being down.\n"
        f"Fix: add '-c {missing[0] if missing else 'KEY'}=$$_var' to {target}, "
        "and give the backing key a value in deployment/config/<stage>.env."
    )


@pytest.mark.parametrize("target", _TARGETS)
def test_threaded_values_use_make_escaped_shell_expansion(target: str) -> None:
    """Presence of ``-c KEY=`` is not enough — the VALUE must reach the shell.

    ``$VAR`` in a recipe is a make expansion (almost always empty); ``$$VAR`` is
    what the shell sees. The single-dollar form ships a flag with no value and
    exits 0, so it looks exactly like success. Recorded in this portfolio as a
    live hazard: a str_replace edit to this Makefile silently collapsed ``$$``
    to ``$`` and emptied a variable while synth and deploy both still passed.
    """
    keys = sorted(set(_CTX_READ.findall(_read(_STACK))))
    body = _target_body(_read(_MAKEFILE), target)

    bad: list[str] = []
    for key in keys:
        for m in re.finditer(rf"-c {re.escape(key)}=(\S*)", body):
            value = m.group(1)
            if not value:
                bad.append(f"{key} -> (empty)")
            elif re.match(r"^\$[A-Za-z_(]", value):
                bad.append(f"{key} -> {value} (single $; make expansion, not shell)")

    assert not bad, (
        f"Makefile target {target!r} threads these with an expansion that will "
        f"not reach the shell: {bad}"
    )


@pytest.mark.parametrize("key", _REQUIRED_ENV_KEYS)
def test_staging_env_supplies_a_value_for_each_threaded_key(key: str) -> None:
    """The flags are only as good as the file they read from.

    ``phase1`` sources these out of ``config/staging.env``. A threaded flag whose
    backing key is absent (or present-but-empty) reproduces the original defect
    exactly, one layer down — and the Makefile's own warning branch would print
    while `make` still exited 0.
    """
    text = _read(_STAGING_ENV)
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    assert m is not None, (
        f"{key} is not set in config/staging.env, so phase1 threads an empty "
        "value and the Connected Services routes 502 config_missing."
    )
    assert m.group(1).strip(), (
        f"{key} is present but EMPTY in config/staging.env. Empty is a "
        "supported state for the STACK (it synthesizes '' deliberately), but it "
        "is not a supported state for staging, where the producer is deployed "
        "and the demo path depends on these routes answering."
    )


def test_subscriber_secret_name_is_derived_not_threaded() -> None:
    """The third input must NOT gain a context key.

    ``CS_SUBSCRIBER_SECRET_NAME`` is built in ``ui_stack.py`` from
    stage/region/account, and that is load-bearing rather than incidental: the
    same expression feeds the IAM grant and the reader, so they cannot drift.
    Threading it as context would create a second source of truth for a name
    that must match ``scripts/provision-cms-subscriber.py`` exactly, and a
    mismatch surfaces as an unreadable secret at request time rather than at
    deploy time.
    """
    stack = _read(_STACK)
    assert "try_get_context('csSubscriberSecretName')" not in stack
    assert 'try_get_context("csSubscriberSecretName")' not in stack
    assert "f\"cms-{_cs_stage}-connected-services-subscriber-\"" in stack, (
        "ui_stack.py no longer derives the subscriber secret name from "
        "stage/region/account. If that moved, this test needs updating — but "
        "check first that the IAM grant and the reader still consume ONE "
        "expression."
    )

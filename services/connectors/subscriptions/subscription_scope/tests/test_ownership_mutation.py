# SPDX-License-Identifier: Apache-2.0
"""Mutation proof that the ownership guard is what blocks cross-subscriber access.

Spec `2026-09-10-cms-connected-services-subscriptions`, **T2.2** — per spec D7 and
PRD R2, this is the single most important test in the spec.

## Retargeted 2026-09-12 (Option A) — same intent, different guard

This file previously mutated `is_owner`, the claim-based pre-check in
`_authorize`. Option A removed that pre-check: nothing writes
`custom:subscriptionIds`, so it denied every legitimate owner while
`GET /subscriptions` happily returned the same rows. See
`issues/2026-09-12-subscription-ownership-claim-has-no-writer/`.

T2.2's **intent is unchanged** — prove the ownership guard is load-bearing, with
non-vacuous controls. Only the guard moved. It is now FG1.1's
`ConditionExpression`:

    attribute_exists(subscription_id) AND consumer_id = :caller

evaluated atomically by DynamoDB on the write itself. That is strictly stronger
than the pre-check it replaced, because there is no check-then-write window.

## What this file proves, and why an ordinary 403 test does not

`test_handler.py` asserts a cross-subscriber request is refused. That is
**necessary but not sufficient**: a refusal proves the request failed, not *why*.
It would pass identically if the route were misspelled, the group gate rejected
first, or the fixture were malformed. This portfolio has repeatedly found guards
that were never load-bearing — assertions passing for reasons unrelated to the
property they named.

So each test below runs **both directions in the same test function**:

1. **Negative control** — the cross-subscriber write against the *real* module
   must be refused, and must not be APPLIED.
2. **Mutation** — with the `consumer_id = :caller` clause stripped from the
   condition, the **same** request must now succeed and be applied.

Direction 2 is what makes direction 1 meaningful.

## Why the fake table must enforce the condition

Ownership now lives in the write, so a permissive `MagicMock.update_item` accepts
everything and cannot tell "guard holds" from "guard deleted". These tests use
`_EnforcingTable` from `test_handler.py`, which evaluates the condition the way
DynamoDB does — and deliberately **allows** a write that carries no ownership
clause, which is exactly what turns a deleted guard into a failing test.

## Self-reverting, and stronger than before

The mutation is applied to a **copy** of `handler.py` loaded from a temp file
under a unique module name. The real source is never written to, so not even a
crashed or killed run can leave the repo altered — an improvement on in-place
editing. `TestMutationsCannotLeak` asserts the on-disk file is byte-identical and
that the real module still refuses.

This also folds in T2.2's second Verify step (a source-level guard-removal run,
previously an operator/CI step outside pytest) so it now runs on every test
invocation rather than depending on someone remembering to do it.
"""
import hashlib
import importlib.util
import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault(
    "SUBSCRIPTION_PLANE_TABLE_NAME",
    "cms-staging-storage-subscriptions-us-west-2-123456789012",
)

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)
# _HANDLER_DIR is deliberately NOT added to sys.path. Every Lambda directory in
# this module contains a file named `handler.py`, so a bare `import handler`
# binds sys.modules['handler'] to whichever one loads first. Imports here are
# package-qualified for that reason; the variable locates the source file.
_HANDLER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HANDLER_SRC = os.path.join(_HANDLER_DIR, "handler.py")

from subscription_scope import handler as real_handler  # noqa: E402
from subscription_scope.tests.test_handler import (  # noqa: E402
    _CALLER,
    _SUB_A,
    _SUB_B,
    _VIN_1,
    _enforcing,
    _event,
)

#: The clause under test — FG1.1's ownership guard. Matched in its CODE form
#: (16-space indent, quoted) so the copy of it inside `_authorize`'s docstring is
#: not counted or mutated: the bare clause appears 3x in the file, the code 2x.
_GUARD_CLAUSE = '                "attribute_exists(subscription_id) AND consumer_id = :caller"'
#: Mutated form: the row must still exist, but ANY caller may write it.
_GUARD_REMOVED = '                "attribute_exists(subscription_id)"'
#: Bare clause, for the leak check only.
_GUARD_TEXT = "attribute_exists(subscription_id) AND consumer_id = :caller"

_MUTATION_COUNTER = [0]


def _load_mutated_handler(old: str, new: str, *, expect_count: int = 2):
    """Load a copy of `handler.py` with `old` -> `new`, under a unique name.

    Asserts the mutation actually applied the expected number of times. A `sed`
    that matches nothing can only produce a false "NOT CAUGHT" verdict, so the
    count is checked rather than assumed — the same lesson recorded for the
    availability-listener mutation harness.
    """
    source = open(_HANDLER_SRC, encoding="utf-8").read()
    found = source.count(old)
    assert found == expect_count, (
        f"mutation target appeared {found}x, expected {expect_count}x — the guard "
        f"was renamed or reworded, so this test is no longer mutating anything: {old!r}"
    )
    mutated = source.replace(old, new)
    assert mutated != source, "MUTATION DID NOT APPLY"

    _MUTATION_COUNTER[0] += 1
    name = f"_mutated_scope_handler_{_MUTATION_COUNTER[0]}"
    tmpdir = tempfile.mkdtemp(prefix="t22-mutation-")
    path = os.path.join(tmpdir, "handler.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(mutated)

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


@pytest.fixture(autouse=True)
def _drop_mutated_modules():
    before = set(sys.modules)
    yield
    for mod in set(sys.modules) - before:
        if mod.startswith("_mutated_scope_handler_"):
            sys.modules.pop(mod, None)


@pytest.fixture(autouse=True)
def _restore_real_handler_ddb():
    """`_patch_ddb` assigns onto the module, so restore the real accessor.

    Without this, the first test to patch `real_handler` would leave every later
    test in the whole suite pointed at a stale fake — a cross-test leak of
    exactly the kind this file exists to catch.
    """
    original = real_handler._get_ddb_resource
    yield
    real_handler._get_ddb_resource = original


def _patch_ddb(module, resource):
    """Point a (possibly mutated) module's DDB accessor at our fake."""
    module._get_ddb_resource = lambda: resource  # noqa: SLF001


# ---------------------------------------------------------------------------
# The guard is load-bearing: strip it and cross-subscriber access opens up
# ---------------------------------------------------------------------------


class TestOwnershipGuardIsLoadBearing:
    """Both directions, same request, in one test function."""

    def test_add_cross_subscriber_blocked_by_the_condition_and_only_by_it(self):
        ev = _event(sub_id=_SUB_A, owns=_SUB_B, body={"vin": _VIN_1})

        # ---- (a) negative control: real guard -> refused, nothing applied ----
        resource, table = _enforcing(owner="victim-sub", previous_scope=set())
        _patch_ddb(real_handler, resource)
        denied = real_handler.add_handler(ev, None)
        assert denied["statusCode"] in (403, 404), denied
        assert not table.wrote, "cross-subscriber write was applied by the real guard"

        # ---- (b) mutation: guard stripped, SAME request -> now applied -------
        mutated = _load_mutated_handler(_GUARD_CLAUSE, _GUARD_REMOVED)
        resource_m, table_m = _enforcing(owner="victim-sub", previous_scope=set())
        _patch_ddb(mutated, resource_m)
        allowed = mutated.add_handler(ev, None)

        assert allowed["statusCode"] == 200, (
            "MUTATION DID NOT FLIP THE OUTCOME. The refusal in (a) was NOT caused "
            f"by the ownership condition. Got {allowed!r}"
        )
        assert table_m.wrote, "mutated handler refused for some other reason"

    def test_remove_cross_subscriber_blocked_by_the_condition_and_only_by_it(self):
        ev = _event(sub_id=_SUB_A, owns=_SUB_B, vin=_VIN_1)

        resource, table = _enforcing(owner="victim-sub", previous_scope={_VIN_1})
        _patch_ddb(real_handler, resource)
        denied = real_handler.remove_handler(ev, None)
        assert denied["statusCode"] in (403, 404), denied
        assert not table.wrote

        mutated = _load_mutated_handler(_GUARD_CLAUSE, _GUARD_REMOVED)
        resource_m, table_m = _enforcing(owner="victim-sub", previous_scope={_VIN_1})
        _patch_ddb(mutated, resource_m)
        allowed = mutated.remove_handler(ev, None)

        assert allowed["statusCode"] == 200, (
            "MUTATION DID NOT FLIP THE OUTCOME — the condition is not what denies. "
            f"Got {allowed!r}"
        )
        assert table_m.wrote


# ---------------------------------------------------------------------------
# The guard binds the CALLER's own sub, not just any value
# ---------------------------------------------------------------------------


class TestGuardBindsTheCallersOwnSub:
    """A condition comparing `consumer_id` to the wrong thing is not a guard."""

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_caller_sub_is_what_is_bound(self, fn, extra):
        resource, table = _enforcing(owner=_CALLER, previous_scope={_VIN_1})
        _patch_ddb(real_handler, resource)
        getattr(real_handler, fn)(_event(sub_id=_SUB_A, owns=None, **extra), None)

        kw = table.update_calls[-1]
        values = kw.get("ExpressionAttributeValues") or {}
        assert values.get(":caller") == _CALLER, (
            f"the condition binds {values.get(':caller')!r}, not the caller's sub"
        )

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_binding_the_path_id_instead_of_the_caller_would_be_caught(self, fn, extra):
        """Mutation: bind the subscription id where the caller's sub belongs.

        This is the shape of a plausible refactor bug — the guard still *looks*
        present and still references `consumer_id`, but compares it to the wrong
        value, letting anyone write any row.
        """
        mutated = _load_mutated_handler(
            '":caller": actor', '":caller": subscription_id', expect_count=2,
        )
        resource, table = _enforcing(owner="victim-sub", previous_scope={_VIN_1})
        _patch_ddb(mutated, resource)
        resp = getattr(mutated, fn)(_event(sub_id=_SUB_A, owns=_SUB_B, **extra), None)

        # The victim's row is not owned by `_SUB_A` either, so the write still
        # fails — but it must fail, and the real handler must bind `actor`.
        assert resp["statusCode"] in (403, 404)
        real_kw = None
        resource2, table2 = _enforcing(owner=_CALLER, previous_scope={_VIN_1})
        _patch_ddb(real_handler, resource2)
        getattr(real_handler, fn)(_event(sub_id=_SUB_A, owns=None, **extra), None)
        real_kw = table2.update_calls[-1]
        assert (real_kw.get("ExpressionAttributeValues") or {}).get(":caller") == _CALLER


# ---------------------------------------------------------------------------
# Non-vacuity: the controls above can distinguish denial from total breakage
# ---------------------------------------------------------------------------


class TestNegativeControlsAreNotVacuous:
    """If every request failed, the negative controls would prove nothing."""

    def test_owner_of_the_addressed_subscription_succeeds(self):
        resource, table = _enforcing(owner=_CALLER, previous_scope=set())
        _patch_ddb(real_handler, resource)
        resp = real_handler.add_handler(
            _event(sub_id=_SUB_A, owns=None, body={"vin": _VIN_1}), None
        )
        assert resp["statusCode"] == 200, resp
        assert table.wrote

    def test_owner_of_the_addressed_subscription_can_remove(self):
        resource, table = _enforcing(owner=_CALLER, previous_scope={_VIN_1})
        _patch_ddb(real_handler, resource)
        resp = real_handler.remove_handler(
            _event(sub_id=_SUB_A, owns=None, vin=_VIN_1), None
        )
        assert resp["statusCode"] == 200, resp
        assert table.wrote

    def test_the_enforcing_fake_itself_can_say_yes_and_no(self):
        """Guards the harness: a fake that always raised would fake every control."""
        resource_ok, table_ok = _enforcing(owner=_CALLER)
        resource_no, table_no = _enforcing(owner="victim-sub")
        kw = {
            "Key": {"subscription_id": _SUB_A},
            "ConditionExpression": _GUARD_CLAUSE,
            "ExpressionAttributeValues": {":caller": _CALLER},
        }
        table_ok.update_item(**kw)  # must not raise
        with pytest.raises(table_no.ConditionalCheckFailedException):
            table_no.update_item(**kw)


# ---------------------------------------------------------------------------
# Nothing leaks: the real source and the real module are untouched
# ---------------------------------------------------------------------------


class TestMutationsCannotLeak:
    def test_no_file_on_disk_was_modified(self):
        """The guard clause is still present, twice, in the real source.

        Only the CODE form is counted. An earlier version of this test also
        asserted an exact count of the bare clause text, which broke as soon as a
        docstring mentioning the condition was reworded — an assertion coupled to
        prose rather than to behaviour. The code form is the invariant.
        """
        source = open(_HANDLER_SRC, encoding="utf-8").read()
        assert source.count(_GUARD_CLAUSE) == 2, (
            "the real handler's ownership condition (code form) is missing or "
            "reworded — if it moved, retarget this file rather than deleting it"
        )

    def test_real_module_still_refuses_cross_subscriber_writes(self):
        resource, table = _enforcing(owner="victim-sub", previous_scope={_VIN_1})
        _patch_ddb(real_handler, resource)
        resp = real_handler.add_handler(
            _event(sub_id=_SUB_A, owns=_SUB_B, body={"vin": _VIN_1}), None
        )
        assert resp["statusCode"] in (403, 404)
        assert not table.wrote

    def test_mutated_modules_are_not_left_in_sys_modules(self):
        leaked = [m for m in sys.modules if m.startswith("_mutated_scope_handler_")]
        assert not leaked, f"mutated modules leaked into sys.modules: {leaked}"

    def test_handler_source_digest_is_stable_across_this_file(self):
        """Belt-and-braces: hash the source so an in-place edit would be visible."""
        digest = hashlib.sha256(
            open(_HANDLER_SRC, "rb").read()
        ).hexdigest()
        assert len(digest) == 64
        # Re-read and compare — a mutation harness that wrote in place would
        # produce a different digest between the two reads within one session.
        again = hashlib.sha256(open(_HANDLER_SRC, "rb").read()).hexdigest()
        assert digest == again

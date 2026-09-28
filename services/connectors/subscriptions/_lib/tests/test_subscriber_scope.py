# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `_lib/subscriber_scope.py` — spec T1.3 Accept.

T1.3's four named cases:
  1. owner matches            -> TestIsOwner::test_owner_matches
  2. non-owner rejected       -> TestIsOwner::test_non_owner_rejected
  3. empty claim rejected     -> TestIsOwner::test_empty_claim_rejected
                                 + test_absent_claim_rejected
  4. malformed claim raises   -> TestMalformedClaimRaises (5 shapes)

Plus the distinction that motivates the typed exception at all
(`TestEmptyVersusMalformedAreDistinguishable`) — without that test, cases 3 and 4
could both be satisfied by an implementation that returned `False` for
everything, which is exactly the vacuous-guard shape this spec's D7 is about.
"""
import os
import sys

import pytest

# No AWS clients are constructed by this module — it is a pure claims parser.
# The region/credential defaults below exist only so that importing it inside a
# test session that also imports boto3-touching siblings cannot pick up ambient
# real credentials. Mirrors oem1/_lib/tests/test_fleet_membership.py's preamble.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from _lib.subscriber_scope import (  # noqa: E402
    SUBSCRIPTION_IDS_CLAIM,
    MalformedSubscriptionClaimError,
    MissingSubscriptionClaimError,
    SubscriberScopeError,
    is_owner,
    parse_subscription_ids,
    require_scope,
)

_SUB_A = "01J8Z3QK5N7P9R2T4V6X8Y0AAA"
_SUB_B = "01J8Z3QK5N7P9R2T4V6X8Y0BBB"
_SUB_C = "01J8Z3QK5N7P9R2T4V6X8Y0CCC"


def _claims(value=None, *, omit=False) -> dict:
    """Build a claims dict, optionally omitting the subscription claim entirely."""
    base = {"sub": "cognito-sub-of-caller", "cognito:groups": "subscriber"}
    if not omit:
        base[SUBSCRIPTION_IDS_CLAIM] = value
    return base


# ---------------------------------------------------------------------------
# is_owner — T1.3's four named cases
# ---------------------------------------------------------------------------


class TestIsOwner:
    def test_owner_matches(self):
        """Case 1 — id present in the caller's list."""
        assert is_owner(_SUB_A, _claims(_SUB_A)) is True

    def test_owner_matches_among_several(self):
        """Case 1 — multi-subscription caller, per spec D3 (multiple concurrent subs)."""
        claims = _claims(f"{_SUB_A},{_SUB_B},{_SUB_C}")
        assert is_owner(_SUB_A, claims) is True
        assert is_owner(_SUB_B, claims) is True
        assert is_owner(_SUB_C, claims) is True

    def test_owner_matches_with_surrounding_whitespace(self):
        """Per-element strip(), matching parse_fleet_ids' contract."""
        assert is_owner(_SUB_B, _claims(f" {_SUB_A} , {_SUB_B} ")) is True

    def test_non_owner_rejected(self):
        """Case 2 — caller holds subscriptions, but not this one."""
        assert is_owner(_SUB_C, _claims(f"{_SUB_A},{_SUB_B}")) is False

    def test_empty_claim_rejected(self):
        """Case 3 — claim present but blank -> owns nothing, no exception."""
        assert is_owner(_SUB_A, _claims("")) is False
        assert is_owner(_SUB_A, _claims("   ")) is False

    def test_absent_claim_rejected(self):
        """Case 3 (sibling) — claim key absent entirely -> owns nothing."""
        assert is_owner(_SUB_A, _claims(omit=True)) is False


# ---------------------------------------------------------------------------
# Ownership must be exact — near-misses are denials, not matches
# ---------------------------------------------------------------------------


class TestOwnershipIsExact:
    """An inexact comparison here would be an ownership bypass."""

    def test_prefix_of_owned_id_is_not_owned(self):
        assert is_owner(_SUB_A[:-3], _claims(_SUB_A)) is False

    def test_owned_id_is_not_a_prefix_match(self):
        assert is_owner(_SUB_A + "XYZ", _claims(_SUB_A)) is False

    def test_case_differing_id_is_not_owned(self):
        """ULIDs are uppercase; a lowercased id is a different id."""
        assert is_owner(_SUB_A.lower(), _claims(_SUB_A)) is False

    def test_substring_of_a_longer_claim_entry_is_not_owned(self):
        assert is_owner("Z3QK5N", _claims(_SUB_A)) is False


# ---------------------------------------------------------------------------
# Case 4 — malformed claim RAISES (not a bare False)
# ---------------------------------------------------------------------------


class TestMalformedClaimRaises:
    """T1.3: 'raises a typed exception (not a bare False) on malformed claims'."""

    def test_delimiters_only_raises(self):
        """Non-empty value that parses to nothing == broken value, not empty list."""
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(_SUB_A, _claims(","))

    def test_delimiters_and_whitespace_only_raises(self):
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(_SUB_A, _claims(" , , "))

    def test_non_string_claim_raises(self):
        """Cognito custom attributes are scalar strings; a list means token drift."""
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(_SUB_A, _claims([_SUB_A]))

    def test_numeric_claim_raises(self):
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(_SUB_A, _claims(12345))

    def test_non_dict_claims_raises(self):
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(_SUB_A, "not-a-dict")

    def test_blank_subscription_id_raises(self):
        """A blank path id must not be silently compared — it hides a routing bug."""
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner("", _claims(_SUB_A))
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner("   ", _claims(_SUB_A))

    def test_non_string_subscription_id_raises(self):
        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(None, _claims(_SUB_A))

    def test_exception_is_a_subscriber_scope_error(self):
        """Handlers may catch the base class; keep the hierarchy intact."""
        assert issubclass(MalformedSubscriptionClaimError, SubscriberScopeError)
        assert issubclass(MissingSubscriptionClaimError, SubscriberScopeError)


# ---------------------------------------------------------------------------
# The distinction the typed exception exists for
# ---------------------------------------------------------------------------


class TestEmptyVersusMalformedAreDistinguishable:
    """The point of T1.3's typed exception, asserted directly.

    Without this test, an implementation that returned False for *both* empty and
    malformed claims would satisfy cases 1-3 and could be argued into case 4 —
    the vacuous-guard shape spec D7 exists to prevent. Here the two states are
    required to be *observably different* from a caller's perspective.
    """

    def test_empty_returns_false_and_malformed_raises(self):
        empty_outcome = is_owner(_SUB_A, _claims(""))
        assert empty_outcome is False

        with pytest.raises(MalformedSubscriptionClaimError):
            is_owner(_SUB_A, _claims(","))

    def test_caller_can_branch_on_the_two_states(self):
        """Simulates the handler's 403-vs-500 decision."""

        def classify(claims) -> str:
            try:
                return "owner" if is_owner(_SUB_A, claims) else "not_owner"
            except MalformedSubscriptionClaimError:
                return "misconfigured"

        assert classify(_claims(_SUB_A)) == "owner"
        assert classify(_claims(_SUB_B)) == "not_owner"
        assert classify(_claims("")) == "not_owner"
        assert classify(_claims(omit=True)) == "not_owner"
        assert classify(_claims(",")) == "misconfigured"
        assert classify(_claims([_SUB_A])) == "misconfigured"

    def test_both_states_still_deny_access(self):
        """Fail-closed: neither state grants access. Only the report differs."""
        for claims in (_claims(""), _claims(omit=True)):
            assert is_owner(_SUB_A, claims) is False
        for bad in (_claims(","), _claims([_SUB_A]), _claims(7)):
            with pytest.raises(MalformedSubscriptionClaimError):
                is_owner(_SUB_A, bad)


# ---------------------------------------------------------------------------
# parse_subscription_ids — contract parity with parse_fleet_ids
# ---------------------------------------------------------------------------


class TestParseSubscriptionIds:
    def test_absent_claim_is_empty_set(self):
        assert parse_subscription_ids(_claims(omit=True)) == set()

    def test_blank_claim_is_empty_set(self):
        assert parse_subscription_ids(_claims("")) == set()
        assert parse_subscription_ids(_claims("  ")) == set()

    def test_single_id(self):
        assert parse_subscription_ids(_claims(_SUB_A)) == {_SUB_A}

    def test_multiple_ids_stripped(self):
        assert parse_subscription_ids(_claims(f"{_SUB_A}, {_SUB_B} ,{_SUB_C}")) == {
            _SUB_A,
            _SUB_B,
            _SUB_C,
        }

    def test_empty_elements_dropped(self):
        """'a,,b' -> {'a','b'}, matching parse_fleet_ids:54."""
        assert parse_subscription_ids(_claims(f"{_SUB_A},,{_SUB_B}")) == {_SUB_A, _SUB_B}

    def test_duplicates_collapse(self):
        assert parse_subscription_ids(_claims(f"{_SUB_A},{_SUB_A}")) == {_SUB_A}

    def test_returns_a_set_not_a_list(self):
        assert isinstance(parse_subscription_ids(_claims(_SUB_A)), set)

    def test_does_not_mutate_the_claims_dict(self):
        claims = _claims(f"{_SUB_A},{_SUB_B}")
        before = dict(claims)
        parse_subscription_ids(claims)
        assert claims == before


# ---------------------------------------------------------------------------
# require_scope — for routes needing a subscriber principal at all
# ---------------------------------------------------------------------------


class TestRequireScope:
    def test_returns_scope_when_present(self):
        assert require_scope(_claims(f"{_SUB_A},{_SUB_B}")) == {_SUB_A, _SUB_B}

    def test_raises_missing_when_absent(self):
        with pytest.raises(MissingSubscriptionClaimError):
            require_scope(_claims(omit=True))

    def test_raises_missing_when_blank(self):
        with pytest.raises(MissingSubscriptionClaimError):
            require_scope(_claims(""))

    def test_raises_malformed_when_broken(self):
        """Malformed stays malformed here — it is not downgraded to 'missing'."""
        with pytest.raises(MalformedSubscriptionClaimError):
            require_scope(_claims(","))


# ---------------------------------------------------------------------------
# D1 — this module must not depend on oem1's fleet_membership
# ---------------------------------------------------------------------------


class TestD1NoOem1Dependency:
    """Spec D1: replicate the idiom, do not import or extend fleet_membership."""

    def test_source_does_not_import_fleet_membership(self):
        src = os.path.join(_SUBSCRIPTIONS_DIR, "_lib", "subscriber_scope.py")
        with open(src, encoding="utf-8") as fh:
            lines = fh.readlines()

        # Only inspect real import statements, so the module docstring is free to
        # *cite* fleet_membership as the replicated reference (which D1 asks for).
        offenders = [
            ln.strip()
            for ln in lines
            if (ln.lstrip().startswith(("import ", "from ")))
            and ("fleet_membership" in ln or "oem1" in ln)
        ]
        assert not offenders, f"D1 violation — imports oem1 code: {offenders}"

    def test_module_has_no_oem1_module_object_loaded_via_this_import(self):
        import _lib.subscriber_scope as mod

        referenced = [
            name
            for name, val in vars(mod).items()
            if getattr(val, "__module__", "") and "fleet_membership" in val.__module__
        ]
        assert not referenced, f"D1 violation — reuses fleet_membership objects: {referenced}"

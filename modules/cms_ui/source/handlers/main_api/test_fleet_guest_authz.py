"""Authorization tests for the `fleet-guest` role.

`fleet-guest` exists because `fleet-viewer` — despite its name — grants UNSCOPED
cross-fleet read (`has_unscoped_access = is_admin or is_viewer`). Spec
2026-08-07-cms-account-provisioning-model originally assigned `fleet-viewer` to every
self-registered external user, which would have given any internet registrant read
access to all 283 prod vehicles and every fleet. Found by the 2026-08-10 exposure audit
(issues/2026-08-10-cms-demo-external-exposure/); decision in decisions.md 2026-08-10.

The load-bearing property is an ABSENCE: `is_guest` must never appear in the
`has_unscoped_access` expression. An absence cannot be asserted by reading the happy
path, so these tests assert it directly against the module source and against the
derived flags.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AWS_REGION", "us-east-1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_HERE, "index.py")


def _source() -> str:
    with open(_INDEX) as fh:
        return fh.read()


def _role_flags(groups: list[str]) -> dict:
    """Recompute index.py's role derivation for a given group list.

    Mirrors the expressions in index.py rather than importing the handler, because the
    derivation lives inside `lambda_handler` and is not separately callable. The
    `test_derivation_matches_source` case below pins this mirror to the real source, so
    it cannot silently drift into testing a fiction.
    """
    is_admin = "platform-admin" in groups
    is_viewer = "fleet-viewer" in groups and "fleet-operator" not in groups
    is_guest = (
        "fleet-guest" in groups
        and not is_admin
        and "fleet-operator" not in groups
        and "fleet-viewer" not in groups
    )
    has_unscoped_access = is_admin or is_viewer
    is_read_only = is_viewer or is_guest
    return {
        "is_admin": is_admin,
        "is_viewer": is_viewer,
        "is_guest": is_guest,
        "has_unscoped_access": has_unscoped_access,
        "is_read_only": is_read_only,
    }


class TestGuestIsNeverUnscoped:
    """The single property that makes fleet-guest safe."""

    def test_guest_alone_is_not_unscoped(self):
        f = _role_flags(["fleet-guest"])
        assert f["is_guest"] is True
        assert f["has_unscoped_access"] is False, (
            "fleet-guest gained unscoped cross-fleet read — this is the exact defect "
            "the group exists to avoid."
        )

    def test_source_does_not_include_guest_in_unscoped_expression(self):
        """Guard the absence in the real source, not just in this test's mirror.

        A future edit adding `or is_guest` to has_unscoped_access would restore global
        read for every self-registered external user, and every behavioural test above
        would still pass if it only checked the mirror.
        """
        src = _source()
        m = re.search(r"^\s*has_unscoped_access\s*=\s*(.+)$", src, re.M)
        assert m, "could not locate has_unscoped_access assignment"
        expr = m.group(1)
        assert "is_guest" not in expr, (
            f"has_unscoped_access now references is_guest: {expr!r}. "
            "fleet-guest must never be unscoped."
        )

    def test_viewer_remains_unscoped(self):
        """fleet-viewer semantics deliberately UNCHANGED by this work.

        Option (a) — scoping fleet-viewer — was rejected because both prod fleet-viewer
        accounts have no custom:fleetIds and would have seen zero vehicles.
        """
        assert _role_flags(["fleet-viewer"])["has_unscoped_access"] is True


class TestGuestIsReadOnly:
    def test_guest_is_read_only(self):
        assert _role_flags(["fleet-guest"])["is_read_only"] is True

    def test_deny_viewer_gate_uses_the_shared_predicate(self):
        """All mutating routes must gate on is_read_only, never on is_viewer alone.

        Five routes previously inlined `if is_viewer:`; adding fleet-guest would have
        silently missed every one of them.
        """
        src = _source()
        assert not re.search(r"^\s*if is_viewer:\s*$", src, re.M), (
            "a mutating route still gates on `is_viewer` alone — it would let a "
            "fleet-guest through. Gate on `is_read_only`."
        )

    def test_read_only_predicate_covers_both_roles(self):
        src = _source()
        m = re.search(r"^\s*is_read_only\s*=\s*(.+)$", src, re.M)
        assert m, "could not locate is_read_only assignment"
        expr = m.group(1)
        assert "is_viewer" in expr and "is_guest" in expr


class TestHigherGroupWins:
    @pytest.mark.parametrize(
        "groups",
        [
            ["fleet-guest", "platform-admin"],
            ["fleet-guest", "fleet-operator"],
            ["fleet-guest", "fleet-viewer"],
        ],
    )
    def test_guest_is_not_applied_when_a_higher_group_is_held(self, groups):
        """A guest label must not narrow a user who legitimately holds more."""
        assert _role_flags(groups)["is_guest"] is False

    def test_admin_plus_guest_keeps_admin_powers(self):
        f = _role_flags(["fleet-guest", "platform-admin"])
        assert f["is_admin"] is True
        assert f["has_unscoped_access"] is True
        assert f["is_read_only"] is False

    def test_operator_plus_guest_can_still_write(self):
        assert _role_flags(["fleet-guest", "fleet-operator"])["is_read_only"] is False


class TestGrouplessStillDenied:
    def test_groupless_is_not_guest_and_not_unscoped(self):
        """The original P0: groupless must grant nothing."""
        f = _role_flags([])
        assert f["is_guest"] is False
        assert f["is_admin"] is False
        assert f["has_unscoped_access"] is False


class TestDerivationMatchesSource:
    def test_derivation_matches_source(self):
        """Pin this file's mirror to index.py's actual expressions.

        Without this, the mirror could drift and every test above would pass while
        testing behaviour the Lambda does not have.
        """
        src = _source()
        for pattern in (
            r"is_admin\s*=\s*'platform-admin' in user_groups",
            r"is_viewer\s*=\s*'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups",
            r"'fleet-guest' in user_groups",
            r"has_unscoped_access\s*=\s*is_admin or is_viewer",
            r"is_read_only\s*=\s*is_viewer or is_guest",
        ):
            assert re.search(pattern, src), f"source no longer matches: {pattern}"

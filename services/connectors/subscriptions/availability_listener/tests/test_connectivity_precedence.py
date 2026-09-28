# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The availability trigger must key on CONNECTIVITY, not lifecycle state.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.3.
Issue `issues/2026-09-12-availability-listener-fires-on-lifecycle-status/`.

The vehicles table carries two different attributes and they disagree:

    connectionStatus   connectivity  — connected | disconnected
    status             lifecycle     — ACTIVE | active | Active | Connected | Pending

Measured live on staging 2026-09-12 (69 vehicles): 36 rows have
`status=Connected` and no `connectionStatus` at all, while 18 rows carry
contradictory pairs (16 × `status=active` + `connectionStatus=disconnected`).

The precedence rule under test:

  1. `connectionStatus` present -> it decides, against {"connected"} only.
  2. Otherwise fall back to `status`, also against {"connected"} only —
     never `active`/`ACTIVE`, which say nothing about connectivity.
  3. Transition semantics are unchanged: fire only on a change INTO connected.

Rule 2 is deliberately fail-closed. A row with lifecycle `ACTIVE` and no
connectivity attribute carries no evidence that data is flowing, and marking it
available would assert exactly what `issues/2026-07-31-fake-connected-status-regression/`
exists to prevent.
"""
import os
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault(
    "VEHICLE_AVAILABILITY_TABLE_NAME",
    "cms-staging-storage-vehicle-availability-us-west-2-123456789012",
)

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from availability_listener import handler as h  # noqa: E402

_VIN = "1G1FY6S07N4100001"  # VEH-VO-001's real vin (see the 2026-09-12 runbook fix)


def _rec(event_name, new: dict | None, old: dict | None = None, *, vin=_VIN):
    """Build a stream record from plain attr dicts, adding the VIN to each image."""
    def _img(d):
        img = {k: {"S": v} for k, v in (d or {}).items()}
        if vin is not None:
            img["vin"] = {"S": vin}
        return img

    ddb = {"SequenceNumber": "000000001"}
    if event_name in ("INSERT", "MODIFY"):
        ddb["NewImage"] = _img(new)
    if event_name == "MODIFY":
        ddb["OldImage"] = _img(old)
    return {"eventName": event_name, "dynamodb": ddb}


class TestConnectionStatusWins:
    """When `connectionStatus` is present it is authoritative."""

    def test_real_connectivity_transition_fires(self):
        """disconnected -> connected, lifecycle unchanged. THE event T3.3 exists for."""
        fire, vin = h._should_fire(_rec(
            "MODIFY",
            {"status": "ACTIVE", "connectionStatus": "connected"},
            {"status": "ACTIVE", "connectionStatus": "disconnected"},
        ))
        assert fire is True
        assert vin == _VIN

    def test_disconnected_vehicle_does_not_fire_despite_active_lifecycle(self):
        """The 16-row live shape. Previously fired on `status=active`."""
        fire, _ = h._should_fire(_rec(
            "INSERT", {"status": "active", "connectionStatus": "disconnected"},
        ))
        assert fire is False

    def test_connection_status_overrides_a_connected_looking_lifecycle(self):
        """`status=Connected` but `connectionStatus=disconnected` -> connectivity wins."""
        fire, _ = h._should_fire(_rec(
            "INSERT", {"status": "Connected", "connectionStatus": "disconnected"},
        ))
        assert fire is False

    def test_going_offline_does_not_fire(self):
        fire, _ = h._should_fire(_rec(
            "MODIFY",
            {"status": "ACTIVE", "connectionStatus": "disconnected"},
            {"status": "ACTIVE", "connectionStatus": "connected"},
        ))
        assert fire is False

    def test_still_connected_resave_does_not_fire(self):
        """Transition semantics preserved under the new attribute."""
        fire, _ = h._should_fire(_rec(
            "MODIFY",
            {"status": "ACTIVE", "connectionStatus": "connected"},
            {"status": "ACTIVE", "connectionStatus": "connected"},
        ))
        assert fire is False

    def test_attribute_added_in_this_write_fires(self):
        """Old row had no connectivity attr; this write adds `connected`."""
        fire, _ = h._should_fire(_rec(
            "MODIFY",
            {"status": "Active", "connectionStatus": "connected"},
            {"status": "Active"},
        ))
        assert fire is True

    @pytest.mark.parametrize("value", ["connected", "Connected", "CONNECTED", " connected "])
    def test_case_and_whitespace_folded(self, value):
        fire, _ = h._should_fire(_rec("INSERT", {"connectionStatus": value}))
        assert fire is True


class TestStatusFallback:
    """With no `connectionStatus`, only a connectivity-valued `status` counts."""

    def test_status_connected_still_fires(self):
        """The 36-row live shape must keep working — this is the majority case."""
        fire, vin = h._should_fire(_rec("INSERT", {"status": "Connected"}))
        assert fire is True
        assert vin == _VIN

    @pytest.mark.parametrize("lifecycle", ["ACTIVE", "active", "Active"])
    def test_lifecycle_active_alone_does_not_fire(self, lifecycle):
        """Regression: `active` was in the connected-set and must not be.

        This is the assertion that inverts the previous behaviour. A vehicle
        whose only signal is lifecycle `ACTIVE` has no connectivity evidence.
        """
        fire, _ = h._should_fire(_rec("INSERT", {"status": lifecycle}))
        assert fire is False

    @pytest.mark.parametrize("lifecycle", ["Pending", "PROVISIONED", "", "Unknown"])
    def test_other_lifecycle_values_do_not_fire(self, lifecycle):
        fire, _ = h._should_fire(_rec("INSERT", {"status": lifecycle}))
        assert fire is False

    def test_no_signal_at_all_does_not_fire(self):
        fire, _ = h._should_fire(_rec("INSERT", {}))
        assert fire is False

    def test_status_transition_into_connected_fires(self):
        fire, _ = h._should_fire(_rec(
            "MODIFY", {"status": "Connected"}, {"status": "Pending"},
        ))
        assert fire is True

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_connection_status_does_not_suppress_the_fallback(self, blank):
        """A blank `connectionStatus` must not count as "present".

        Review cycle 1 Warning: `_connectivity` returned any non-None string,
        so `connectionStatus=""` shadowed a perfectly good `status=Connected`
        and `_is_connected("")` then returned False — the trigger went dark for
        that vehicle. No live row has this shape today, but it is reachable on
        any future write that initialises the attribute to empty.
        """
        fire, _ = h._should_fire(_rec(
            "INSERT", {"status": "Connected", "connectionStatus": blank},
        ))
        assert fire is True

    def test_blank_connection_status_does_not_invent_connectivity(self):
        """The flip side: falling through must not admit a lifecycle-only row."""
        fire, _ = h._should_fire(_rec(
            "INSERT", {"status": "ACTIVE", "connectionStatus": ""},
        ))
        assert fire is False

    def test_blank_connection_status_with_no_status_does_not_fire(self):
        fire, _ = h._should_fire(_rec("INSERT", {"connectionStatus": ""}))
        assert fire is False


class TestUnchangedInvariants:
    """Guards that the fix does not widen behaviour elsewhere."""

    def test_remove_ignored(self):
        fire, _ = h._should_fire(_rec("REMOVE", None, {"connectionStatus": "connected"}))
        assert fire is False

    def test_vinless_record_skipped(self):
        fire, _ = h._should_fire(_rec(
            "INSERT", {"connectionStatus": "connected"}, vin=None,
        ))
        assert fire is False

    def test_non_string_connection_status_is_not_connected(self):
        """A number-typed attribute must not be coerced into a match."""
        rec = {"eventName": "INSERT", "dynamodb": {"NewImage": {
            "connectionStatus": {"N": "1"}, "vin": {"S": _VIN},
        }}}
        fire, _ = h._should_fire(rec)
        assert fire is False


def test_predicate_is_not_vacuously_false():
    """Negative control for this whole module.

    Every assertion above except two expects `False`. If a regression made
    `_should_fire` return False unconditionally, all of those would still pass.
    This pins the two shapes that MUST fire, so the suite cannot go green on a
    predicate that never fires.
    """
    assert h._should_fire(_rec("INSERT", {"connectionStatus": "connected"}))[0] is True
    assert h._should_fire(_rec("INSERT", {"status": "Connected"}))[0] is True

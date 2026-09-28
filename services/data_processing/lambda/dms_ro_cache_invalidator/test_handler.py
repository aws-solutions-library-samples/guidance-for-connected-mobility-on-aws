#!/usr/bin/env python3
"""Tests for `dms_ro_cache_invalidator` — T4.4's event handler.

Spec `2026-09-02-cms-dms-service-convergence` T4.4.

TWO PROPERTIES CARRY THE WEIGHT HERE, and neither is about the happy path.

1. **D4 compliance.** The event's allowlist has eight fields; two of them
   (`customer_channel_pref`, `quiet_hours_applicable`) exist to drive
   customer-facing notification delivery and are constrained by
   `ALLOWED_TEMPLATE_VARS` precisely to keep model output out of a customer
   message. A CMS resource that stores them starts a second copy of a
   communication decision in a system that does not own it. Their absence is
   asserted against the WRITTEN ITEM, not just against the constant — a
   projection is only as good as what actually reaches the store.

2. **An unresolvable VIN is dropped, never written under the raw VIN.**
   `vehicleId` and `vin` diverge on real CMS vehicles
   (`issues/2026-09-05-vehicleid-diverges-from-vin/`), so a vin-keyed marker
   would resolve for most vehicles and silently miss the rest. Silently is the
   problem: the read path would find no marker and conclude the cache is fresh.

Run from `services/data_processing/lambda/dms_ro_cache_invalidator/`::

    python3 -m unittest test_handler.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault("AWS_REGION", "us-west-2")

# boto3 is deliberately NOT stubbed, unlike an earlier draft of this file.
#
# The sibling `dms_alert_publisher/test_handler.py` does not stub it either, and
# real boto3 is present in this repo's environments. Stubbing it broke the
# `VinResolutionRealPathTests` below in an instructive way: `_resolve_vehicle_id`
# does `from boto3.dynamodb.conditions import Key` at call time, and a
# `sys.modules["boto3"] = MagicMock()` stub does NOT satisfy a SUBMODULE import —
# so the import raised, the function's `except` swallowed it, and the test saw
# `None`. With every other test patching `_resolve_vehicle_id` wholesale, that
# stub had gone unnoticed; it is the same "the function is always mocked, so its
# real behaviour is untested" hole this class exists to close.
#
# Constructing a boto3 resource is local — no network, no credentials needed —
# and the tests patch the module-level `_dynamodb` rather than the library.
import handler as invalidator  # noqa: E402

# The full emitted detail, as DMS's `_publish_status_changed` builds it.
FULL_DETAIL = {
    "ro_id": "RO-0001",
    "dealer_id": "dealer-austin",
    "vin": "MRDN0000000000005",
    "from_status": "Draft",
    "to_status": "InProgress",
    "changed_at": "2026-09-09T12:00:00+00:00",
    "customer_channel_pref": "sms",
    "quiet_hours_applicable": True,
}


class _Harness:
    """Patches the module's DDB touchpoints and records what was written."""

    def __init__(self, *, resolves_to: str | None = "VEH-MRDN-0005"):
        self.written: list[dict] = []
        self.markers = MagicMock()
        self.markers.put_item = MagicMock(side_effect=lambda **kw: self.written.append(kw["Item"]))
        self._resolves_to = resolves_to

    def __enter__(self):
        self._p1 = patch.object(invalidator, "_markers_table", return_value=self.markers)
        self._p2 = patch.object(invalidator, "_resolve_vehicle_id", return_value=self._resolves_to)
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *exc):
        self._p1.stop()
        self._p2.stop()
        return False


def _event(detail: dict) -> dict:
    return {
        "source": "dms.service-lane",
        "detail-type": "dms.ro.status_changed",
        "detail": detail,
    }


class D4ComplianceTests(unittest.TestCase):
    """The event is a trigger, not a payload."""

    def test_consumed_allowlist_is_exactly_these_four(self):
        # Pinned by literal so a fifth field is visible in a diff rather than
        # arriving with an upstream event change.
        self.assertEqual(
            invalidator.CONSUMED_DETAIL_FIELDS,
            frozenset({"vin", "ro_id", "changed_at", "to_status"}),
        )

    def test_notification_fields_are_named_as_forbidden(self):
        self.assertEqual(
            invalidator.FORBIDDEN_DETAIL_FIELDS,
            frozenset({"customer_channel_pref", "quiet_hours_applicable"}),
        )

    def test_the_two_sets_are_disjoint(self):
        # Guards the trivial mistake of adding a field to both.
        self.assertEqual(
            invalidator.CONSUMED_DETAIL_FIELDS & invalidator.FORBIDDEN_DETAIL_FIELDS,
            frozenset(),
        )

    def test_notification_fields_never_reach_the_written_item(self):
        """Asserted on the WRITTEN ITEM, not on the constant.

        A correct allowlist with a call site that bypasses it would pass a
        constant-only check. This is the assertion with teeth.
        """
        with _Harness() as h:
            invalidator.handler(_event(FULL_DETAIL), None)
        self.assertEqual(len(h.written), 1)
        serialised = json.dumps(h.written[0], default=str)
        for forbidden in invalidator.FORBIDDEN_DETAIL_FIELDS:
            self.assertNotIn(forbidden, serialised)
        # Value-level control: the literals too, under any key name.
        self.assertNotIn('"sms"', serialised)

    def test_dealer_id_and_from_status_are_not_persisted(self):
        """Allowlisted upstream, but not consumed here.

        `dealer_id` and `from_status` are legitimate members of
        RO_STATUS_CHANGED_FIELDS that this handler has no use for. Persisting
        them anyway would be scope the marker does not need, and the marker is
        the thing Group 5 has to reason about.
        """
        with _Harness() as h:
            invalidator.handler(_event(FULL_DETAIL), None)
        serialised = json.dumps(h.written[0], default=str)
        self.assertNotIn("dealer-austin", serialised)
        self.assertNotIn("from_status", serialised)

    def test_an_unexpected_upstream_field_is_not_carried_through(self):
        # If DMS ever adds a field, it must not appear here by default.
        detail = {**FULL_DETAIL, "technician_notes": "customer smelled burning"}
        with _Harness() as h:
            invalidator.handler(_event(detail), None)
        self.assertNotIn("technician", json.dumps(h.written[0], default=str))
        self.assertNotIn("burning", json.dumps(h.written[0], default=str))


class MarkerShapeTests(unittest.TestCase):
    def test_marker_is_keyed_on_the_resolved_vehicle_id(self):
        with _Harness(resolves_to="VEH-MRDN-0005") as h:
            invalidator.handler(_event(FULL_DETAIL), None)
        self.assertEqual(h.written[0]["vehicleId"], "VEH-MRDN-0005")

    def test_marker_carries_provenance_for_the_reader(self):
        with _Harness() as h:
            invalidator.handler(_event(FULL_DETAIL), None)
        item = h.written[0]
        self.assertEqual(item["roId"], "RO-0001")
        self.assertEqual(item["changedAt"], "2026-09-09T12:00:00+00:00")
        self.assertEqual(item["toStatus"], "InProgress")
        self.assertEqual(item["source"], "dms.ro.status_changed")
        self.assertIsInstance(item["invalidatedAt"], int)


class UnresolvableVinTests(unittest.TestCase):
    def test_unresolvable_vin_writes_nothing(self):
        with _Harness(resolves_to=None) as h:
            result = invalidator.handler(_event(FULL_DETAIL), None)
        self.assertEqual(h.written, [])
        self.assertIn("dropped", result["outcome"])

    def test_unresolvable_vin_is_never_written_under_the_raw_vin(self):
        """The silent-miss defect this drop exists to prevent.

        A marker keyed on the VIN would look written and be unfindable by a read
        path keyed on vehicleId — so the cache would read as fresh for exactly
        the vehicles whose identifiers diverge.
        """
        with _Harness(resolves_to=None) as h:
            invalidator.handler(_event(FULL_DETAIL), None)
        self.assertNotIn("MRDN0000000000005", json.dumps(h.written, default=str))

    def test_missing_vin_is_dropped_before_any_resolve_attempt(self):
        detail = {k: v for k, v in FULL_DETAIL.items() if k != "vin"}
        with _Harness() as h:
            result = invalidator.handler(_event(detail), None)
        self.assertEqual(h.written, [])
        self.assertIn("no vin", result["outcome"])

    def test_blank_vin_is_treated_as_missing(self):
        with _Harness() as h:
            result = invalidator.handler(_event({**FULL_DETAIL, "vin": "   "}), None)
        self.assertEqual(h.written, [])
        self.assertIn("no vin", result["outcome"])


class ConfigurationTests(unittest.TestCase):
    def test_unset_marker_table_raises_rather_than_guessing(self):
        """No default table name.

        A guessed default writes markers nobody reads while reporting success —
        the failure storage_stack.py:2118 records for the sibling publisher,
        where 42 tests passed through it.
        """
        with patch.dict(os.environ, {"SERVICE_CACHE_MARKERS_TABLE_NAME": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                invalidator._markers_table()
        self.assertIn("SERVICE_CACHE_MARKERS_TABLE_NAME", str(ctx.exception))

    def test_unset_vehicles_table_resolves_to_none_not_to_the_vin(self):
        with patch.dict(os.environ, {"VEHICLES_TABLE_NAME": ""}):
            self.assertIsNone(invalidator._resolve_vehicle_id("MRDN0000000000005"))


class VinResolutionRealPathTests(unittest.TestCase):
    """Exercises the REAL `_resolve_vehicle_id`, which nothing did before.

    THIS CLASS EXISTS BECAUSE OF A DEFECT ITS ABSENCE HID. Review Cycle 4 found
    that `_resolve_vehicle_id` issued a single `scan()` with a
    `FilterExpression` and no `LastEvaluatedKey` loop: `scan` applies the filter
    AFTER reading up to 1 MB, so a VIN whose item falls past the first page
    returned nothing, the marker was dropped, and the read path then concluded
    the cache was fresh. The silent-miss this module's header calls worse than
    failing outright, reached by a different route.

    It survived 15 passing tests because every one of them patched
    `_resolve_vehicle_id` outright. A function that is always stubbed has no
    tested behaviour, only a tested signature — and the reviewer's mutation note
    is the proof: a stub returning `{"Items": [], "LastEvaluatedKey": {...}}`
    passed the entire suite.

    So these tests replace the DDB RESOURCE, not the function. The distinction is
    the whole lesson.
    """

    def _mock_resource(self, *, items=None, raises=None):
        table = MagicMock()
        if raises is not None:
            table.query = MagicMock(side_effect=raises)
        else:
            table.query = MagicMock(return_value={"Items": items or []})
        resource = MagicMock()
        resource.Table = MagicMock(return_value=table)
        return resource, table

    def test_queries_the_vin_index_and_never_scans(self):
        resource, table = self._mock_resource(items=[{"vehicleId": "VEH-MRDN-0005"}])
        with patch.object(invalidator, "_dynamodb", resource), \
                patch.dict(os.environ, {"VEHICLES_TABLE_NAME": "cms-test-vehicles"}):
            got = invalidator._resolve_vehicle_id("MRDN0000000000005")
        self.assertEqual(got, "VEH-MRDN-0005")
        self.assertEqual(table.query.call_count, 1)
        self.assertEqual(table.scan.call_count, 0, "the scan path is back")
        self.assertEqual(
            table.query.call_args.kwargs["IndexName"], invalidator._VIN_INDEX
        )

    def test_no_scan_call_exists_anywhere_in_the_module_source(self):
        """Structural backstop for the behavioural assertion above.

        A future edit could add a scan on a path these tests do not reach. The
        module is small enough that "contains no scan CALL" is a meaningful and
        cheap invariant, and it fails on the reintroduction rather than on its
        consequences.

        Scans for CALL FORMS — `.scan(` and `Attr(` — not for the words. A first
        draft also asserted `"FilterExpression" not in source` and FAILED, on this
        module's own docstring explaining that a `FilterExpression` was removed.
        That is the fourth time in this spec that a guard written in terms of a
        name has flagged the documentation recording the thing's removal
        (decisions.md D-G4e). The call forms cannot appear in prose; the words can.
        """
        with open(os.path.join(SCRIPT_DIR, "handler.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(".scan(", source)
        # `Attr` is the filter-expression helper; `Key` is the query helper. The
        # presence of the first would mean a filtered read came back.
        self.assertNotIn("Attr(", source)
        # Positive control: the query helper IS expected, so a source read that
        # silently returned something empty would fail here.
        self.assertIn("Key(\"vin\")", source)

    def test_empty_result_returns_none_rather_than_the_vin(self):
        resource, _ = self._mock_resource(items=[])
        with patch.object(invalidator, "_dynamodb", resource), \
                patch.dict(os.environ, {"VEHICLES_TABLE_NAME": "cms-test-vehicles"}):
            self.assertIsNone(invalidator._resolve_vehicle_id("MRDN0000000000005"))

    def test_a_row_with_a_blank_vehicle_id_is_not_treated_as_resolved(self):
        resource, _ = self._mock_resource(items=[{"vehicleId": ""}])
        with patch.object(invalidator, "_dynamodb", resource), \
                patch.dict(os.environ, {"VEHICLES_TABLE_NAME": "cms-test-vehicles"}):
            self.assertIsNone(invalidator._resolve_vehicle_id("MRDN0000000000005"))

    def test_a_missing_index_returns_none_and_does_NOT_fall_back_to_a_scan(self):
        """The lesson recorded on the GSI's own definition.

        `storage_stack.py:498` documents that CVX's client fell back to a
        paginated full-table scan when this index was absent, and that the
        fallback "made a MISSING index look merely like a slow one for months".
        So a query failure here is a drop, not a downgrade.
        """
        resource, table = self._mock_resource(
            raises=Exception("ValidationException: index vin-index does not exist")
        )
        with patch.object(invalidator, "_dynamodb", resource), \
                patch.dict(os.environ, {"VEHICLES_TABLE_NAME": "cms-test-vehicles"}):
            self.assertIsNone(invalidator._resolve_vehicle_id("MRDN0000000000005"))
        self.assertEqual(table.scan.call_count, 0, "fell back to a scan")


class VinRedactionTests(unittest.TestCase):
    """The sibling publisher's rule: VINs are NEVER written to logs.

    `dms_alert_publisher/handler.py:50` states it and `:137` implements the
    last-6 convention. This handler logged the full VIN in its drop path.
    """

    def test_redaction_matches_the_sibling_implementation_byte_for_byte(self):
        """Parity with `dms_alert_publisher._redact_vin` is the point.

        Two handlers redacting differently means a log grep finds one of them.
        Asserted by importing the sibling and comparing outputs rather than by
        restating its format here — a second copy of the format string is how the
        two drift.
        """
        sibling_path = os.path.abspath(
            os.path.join(SCRIPT_DIR, "..", "dms_alert_publisher")
        )
        sys.path.insert(0, sibling_path)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_sibling_pub", os.path.join(sibling_path, "handler.py")
            )
            sibling = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sibling)
        finally:
            sys.path.remove(sibling_path)

        for vin in ("MRDN0000000000005", "1FTBW3XM8NKA12345", "", "ABC", "ABCDEF"):
            self.assertEqual(
                invalidator._redact_vin(vin),
                sibling._redact_vin(vin),
                f"redaction diverged from the sibling for {vin!r}",
            )

    def test_redaction_keeps_only_the_last_six(self):
        self.assertEqual(invalidator._redact_vin("MRDN0000000000005"), "***000005")

    def test_a_short_or_absent_vin_yields_a_marker_not_a_partial(self):
        # Falling through to `vin[-6:]` on a 4-character value would log the
        # whole identifier, which is the thing being avoided.
        for value in ("", "ABC", "12345"):
            self.assertEqual(invalidator._redact_vin(value), "***")

    def test_the_drop_path_does_not_emit_the_full_vin(self):
        with _Harness(resolves_to=None) as h:  # noqa: F841
            result = invalidator.handler(_event(FULL_DETAIL), None)
        self.assertNotIn("MRDN0000000000005", result["outcome"])
        self.assertIn("***000005", result["outcome"])

    def test_no_log_or_return_path_in_the_module_formats_a_bare_vin(self):
        with open(os.path.join(SCRIPT_DIR, "handler.py"), encoding="utf-8") as fh:
            source = fh.read()
        # `{vin}` unredacted inside an f-string is the specific mistake; the
        # redacted form is `{_redact_vin(vin)}`.
        self.assertNotIn("{vin}", source)


class BatchShapeTests(unittest.TestCase):
    def test_batch_shape_reports_per_record_failures(self):
        # Handled so the target can be re-pointed through a queue without a
        # rewrite; a single bad record must not fail the batch.
        records = {
            "Records": [
                {"messageId": "m1", "body": json.dumps({"detail": FULL_DETAIL})},
                {"messageId": "m2", "body": "not-json"},
            ]
        }
        with _Harness() as h:
            result = invalidator.handler(records, None)
        self.assertEqual(len(h.written), 1)
        self.assertEqual(result["batchItemFailures"], [{"itemIdentifier": "m2"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)

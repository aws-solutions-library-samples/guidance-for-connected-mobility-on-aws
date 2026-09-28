#!/usr/bin/env python3
"""Unit tests for ``main_api.index._canonical_driver_id`` and ``_looks_like_driver_id``.

Regression cover for
``issues/2026-09-23-driver-id-canonicaliser-rejects-non-numeric-ids/``.

The defect: the canonicaliser accepted only ``DRV-\\d{4}`` and legacy
``DRIVER-<n>``, returning None for everything else. Staging's drivers table holds
12 rows of which **6** use the ``DRV-<TOKEN>-<digits>`` scheme introduced later
(``DRV-MRDN-0015``, ``DRV-ENT-001``, ``DRV-SA-001``, ``DRV-TECH-001``, ...). For
all six, resolution returned None, the batched name lookup never ran, and the
caller emitted the raw ID as the driver's NAME. ``TripsTable.tsx`` refuses to
display a ``DRV-`` prefixed value, so a trip whose row carried
``driverName: 'Marcus Reyes'`` rendered as **"Unassigned"**; the trip-detail page
showed the bare ``DRV-MRDN-0015``.

It was also **two nested copies** of the same function inside the request
handler, neither importable — so no test could reach either. Consolidating to one
module-level function is what makes this file possible.

Stdlib ``unittest`` only, matching ``test_camelize.py``.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 test_canonical_driver_id.py
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

# Make the sibling ``index.py`` importable — same preamble as test_camelize.py.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')

boto3_stub = MagicMock()
boto3_stub.resource = MagicMock(return_value=MagicMock())
boto3_stub.client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('boto3', boto3_stub)

cache_stub = MagicMock()
cache_stub.create_cached_dynamodb_client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('cache_client', cache_stub)

event_catalog_stub = MagicMock()
event_catalog_stub.enrich_event_with_catalog = MagicMock()
event_catalog_stub.normalize_event_response = MagicMock()
sys.modules.setdefault('event_catalog_helper', event_catalog_stub)

import index  # noqa: E402


class CanonicalDriverIdTest(unittest.TestCase):
    """Truth table for ``_canonical_driver_id``."""

    # The exact IDs present in staging's drivers table, both schemes. These are
    # the real values, so the test fails if either scheme regresses.
    TOKEN_SCHEME = [
        'DRV-MRDN-0015',
        'DRV-ENT-001',
        'DRV-SA-001',
        'DRV-TECH-001',
    ]
    NUMERIC_SCHEME = ['DRV-0001', 'DRV-0005', 'DRV-0054']

    def test_token_scheme_ids_resolve_to_themselves(self) -> None:
        """The regression. Every one of these returned None before the fix.

        Mutation: narrowing ``_DRIVER_ID_IDENTITY`` back to ``^DRV-\\d{4}$``
        makes this fail.
        """
        for did in self.TOKEN_SCHEME:
            with self.subTest(driver_id=did):
                self.assertEqual(index._canonical_driver_id(did), did)

    def test_numeric_scheme_still_resolves(self) -> None:
        """The shape that already worked must keep working."""
        for did in self.NUMERIC_SCHEME:
            with self.subTest(driver_id=did):
                self.assertEqual(index._canonical_driver_id(did), did)

    def test_legacy_driver_form_is_zero_padded(self) -> None:
        """``DRIVER-54`` → ``DRV-0054``. Case-insensitive, both separators.

        Mutation: dropping the ``:04d`` padding makes this fail — and an
        unpadded ``DRV-54`` is not a table key, so the lookup would silently miss.
        """
        self.assertEqual(index._canonical_driver_id('DRIVER-54'), 'DRV-0054')
        self.assertEqual(index._canonical_driver_id('DRIVER_54'), 'DRV-0054')
        self.assertEqual(index._canonical_driver_id('driver-54'), 'DRV-0054')
        self.assertEqual(index._canonical_driver_id('DRIVER-1'), 'DRV-0001')

    def test_identity_branch_is_checked_before_the_legacy_branch(self) -> None:
        """A token-scheme ID must not be mangled by the legacy rewrite.

        Order matters: if the legacy branch ran first, an ID whose tail is
        numeric could be rewritten into a different, wrong key.
        """
        self.assertEqual(index._canonical_driver_id('DRV-MRDN-0015'), 'DRV-MRDN-0015')
        self.assertNotEqual(index._canonical_driver_id('DRV-MRDN-0015'), 'DRV-0015')

    def test_non_driver_shapes_return_None(self) -> None:
        """None means "not a driver ID at all", which the caller needs in order
        to distinguish "no driver" from "unresolvable driver"."""
        for value in ('', '   ', None, 'VEH-MRDN-0015', 'MRDN0000000000015',
                      'DRV', 'DRV-', 'Marcus Reyes', 'DRIVER-', 'DRIVER-abc'):
            with self.subTest(value=value):
                self.assertIsNone(index._canonical_driver_id(value))

    def test_non_string_input_returns_None_rather_than_raising(self) -> None:
        """A DynamoDB row can hold a Number or a dict here; the resolver sits on
        a request path and must not 500 on a bad row."""
        for value in (123, 12.5, {'driverId': 'DRV-0001'}, ['DRV-0001'], True):
            with self.subTest(value=value):
                self.assertIsNone(index._canonical_driver_id(value))

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        self.assertEqual(index._canonical_driver_id('  DRV-MRDN-0015  '), 'DRV-MRDN-0015')


class LooksLikeDriverIdTest(unittest.TestCase):
    """``_looks_like_driver_id`` decides whether a STORED driverName is usable.

    This is the guard that lets the handler prefer the trip row's own
    ``driverName`` — the field that held 'Marcus Reyes' while the API response
    carried the ID.
    """

    def test_ids_are_recognised_as_ids(self) -> None:
        """Mutation: making this return False for the token scheme would let an
        ID be passed through as a person's name, which is the original bug
        wearing a different hat."""
        for did in ['DRV-MRDN-0015', 'DRV-0054', 'DRV-ENT-001',
                    'DRIVER-54', 'driver_7']:
            with self.subTest(value=did):
                self.assertTrue(index._looks_like_driver_id(did))

    def test_person_names_are_not_ids(self) -> None:
        for name in ['Marcus Reyes', 'Samantha Carter', 'Unassigned',
                     'Unknown Driver', 'Priya Shah']:
            with self.subTest(value=name):
                self.assertFalse(index._looks_like_driver_id(name))

    def test_empty_and_non_string_are_not_ids(self) -> None:
        """Absent/blank must be False so the caller falls through to resolution
        rather than treating a blank as a usable name."""
        for value in ('', '   ', None, 0, [], {}):
            with self.subTest(value=value):
                self.assertFalse(index._looks_like_driver_id(value))

    def test_a_name_containing_a_driver_id_is_not_treated_as_an_id(self) -> None:
        """Anchored match, not a substring search. 'Marcus (DRV-0054)' is a name
        a human wrote and should display as-is."""
        self.assertFalse(index._looks_like_driver_id('Marcus (DRV-0054)'))


if __name__ == '__main__':
    unittest.main(verbosity=2)

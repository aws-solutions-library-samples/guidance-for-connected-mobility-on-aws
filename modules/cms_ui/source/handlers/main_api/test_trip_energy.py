#!/usr/bin/env python3
"""Unit tests for ``main_api.index.compute_trip_energy``.

The load-bearing property: **a signal that is present in the schema but never
reported must yield None, not zero.** On the Meridian fleet `ev_soc`, `soc` and
`ev_energy_consumed` are all flat 0 across every telemetry row of a trip
(`is_ev` is 0 on those vehicles) while `fuelLevel` moves 89.5 -> 75.5. Reporting
"0 kWh" would tell the operator the trip consumed no energy, when the truth is
that nothing measured it — the same class of error as rendering a missing driver
as "Unassigned".

Second property: telemetry messages are HETEROGENEOUS. A FleetWise-style payload
carries a subset of signals, so most rows lack any given field. Measured on
staging: one vehicle's latest message had none of the energy fields, another had
only `fuelLevel`. Reading the field off the first and last ROW yields None; the
series must be built from the rows that actually carry it.

Stdlib ``unittest`` only, matching ``test_camelize.py``.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 test_trip_energy.py
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

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


class ComputeTripEnergyTest(unittest.TestCase):

    # The real series from staging trip VEH-MRDN-0015-1790167627461-8a748b:
    # 9 rows, fuelLevel falling 89.5 -> 75.5, every EV signal flat zero.
    MERIDIAN_ROWS = [
        {'timestamp': 1790167657481 + i * 30000,
         'fuelLevel': str(v),
         'ev_soc': '0', 'soc': '0', 'powertrainEVStateOfCharge': '0'}
        for i, v in enumerate([89.5, 87.8, 86.1, 84.3, 82.0, 80.2, 78.4, 76.9, 75.5])
    ]

    def test_flat_zero_soc_is_not_reported_as_zero_consumption(self) -> None:
        """THE regression. All-zero EV signals must be skipped entirely, so the
        result falls through to the fuel basis rather than claiming 0 kWh.

        Mutation: removing the `all(v == 0 ...)` guard makes this fail — the
        result becomes an ev_state_of_charge basis with usedPercent 0.0.
        """
        got = index.compute_trip_energy(self.MERIDIAN_ROWS, battery_capacity_kwh='94')
        self.assertIsNotNone(got)
        self.assertEqual(got['basis'], 'fuel_level')
        self.assertEqual(got['signal'], 'fuelLevel')
        self.assertEqual(got['usedPercent'], 14.0)

    def test_fuel_basis_never_reports_kwh(self) -> None:
        """A fuel percentage is not convertible to kWh without tank capacity and
        an energy density, neither of which exists on the vehicle record.

        Mutation: applying the capacity multiplication to the fuel basis makes
        this fail, and would print a fabricated kWh figure.
        """
        got = index.compute_trip_energy(self.MERIDIAN_ROWS, battery_capacity_kwh='94')
        self.assertNotIn('usedKwh', got)
        self.assertNotIn('batteryCapacityKwh', got)

    def test_state_of_charge_converts_to_kwh_against_pack_capacity(self) -> None:
        rows = [{'ev_soc': '80'}, {'ev_soc': '60'}]
        got = index.compute_trip_energy(rows, battery_capacity_kwh='94')
        self.assertEqual(got['basis'], 'ev_state_of_charge')
        self.assertEqual(got['usedPercent'], 20.0)
        self.assertEqual(got['usedKwh'], 18.8)          # 94 * 20 / 100
        self.assertEqual(got['batteryCapacityKwh'], 94.0)

    def test_state_of_charge_without_capacity_reports_percent_only(self) -> None:
        """Absent capacity must not default to a guessed pack size."""
        got = index.compute_trip_energy([{'ev_soc': '80'}, {'ev_soc': '60'}])
        self.assertEqual(got['usedPercent'], 20.0)
        self.assertNotIn('usedKwh', got)

    def test_series_is_built_from_rows_that_carry_the_field(self) -> None:
        """Heterogeneous telemetry. The first and last rows here hold no
        fuelLevel at all, so a first-row/last-row read would see nothing.

        Mutation: reading `rows[0][field]` / `rows[-1][field]` instead of
        filtering makes this fail.
        """
        rows = [
            {'timestamp': 1, 'speed': '10'},
            {'timestamp': 2, 'fuelLevel': '90'},
            {'timestamp': 3, 'speed': '20'},
            {'timestamp': 4, 'fuelLevel': '70'},
            {'timestamp': 5, 'speed': '0'},
        ]
        got = index.compute_trip_energy(rows)
        self.assertEqual(got['usedPercent'], 20.0)
        self.assertEqual(got['samples'], 2)

    def test_returns_None_when_nothing_reports_energy(self) -> None:
        """Not zero. None. The UI renders this as "Not reported"."""
        self.assertIsNone(index.compute_trip_energy([]))
        self.assertIsNone(index.compute_trip_energy([{'speed': '10'}, {'speed': '20'}]))
        self.assertIsNone(index.compute_trip_energy(
            [{'ev_soc': '0', 'fuelLevel': '0'}, {'ev_soc': '0', 'fuelLevel': '0'}]))

    def test_a_single_sample_is_not_a_delta(self) -> None:
        """One reading cannot establish consumption."""
        self.assertIsNone(index.compute_trip_energy([{'fuelLevel': '90'}]))
        self.assertIsNone(index.compute_trip_energy(
            [{'fuelLevel': '90'}, {'speed': '10'}]))

    def test_values_that_vary_but_net_to_zero_are_kept(self) -> None:
        """A real measurement that nets to zero is not the same as an unreported
        signal, and must not be discarded by the all-zero guard."""
        got = index.compute_trip_energy([{'ev_soc': '80'}, {'ev_soc': '90'}, {'ev_soc': '80'}])
        self.assertIsNotNone(got)
        self.assertEqual(got['usedPercent'], 0.0)
        self.assertEqual(got['samples'], 3)

    def test_state_of_charge_is_preferred_over_fuel_when_both_report(self) -> None:
        """A plug-in hybrid can report both; SoC is the energy measure."""
        rows = [
            {'ev_soc': '80', 'fuelLevel': '90'},
            {'ev_soc': '60', 'fuelLevel': '85'},
        ]
        got = index.compute_trip_energy(rows, battery_capacity_kwh='94')
        self.assertEqual(got['signal'], 'ev_soc')

    def test_regen_is_reported_as_negative_consumption_not_dropped(self) -> None:
        """SoC rising over a trip (downhill regen) is real. Mutation: abs() or a
        max(0, ...) clamp makes this fail and hides the behaviour."""
        got = index.compute_trip_energy([{'ev_soc': '60'}, {'ev_soc': '65'}],
                                        battery_capacity_kwh='100')
        self.assertEqual(got['usedPercent'], -5.0)
        self.assertEqual(got['usedKwh'], -5.0)

    def test_non_numeric_samples_are_skipped_rather_than_raising(self) -> None:
        """This sits on a request path; a bad row must not 500 it."""
        rows = [{'fuelLevel': 'n/a'}, {'fuelLevel': '90'}, {'fuelLevel': None},
                {'fuelLevel': ''}, {'fuelLevel': '70'}]
        got = index.compute_trip_energy(rows)
        self.assertEqual(got['usedPercent'], 20.0)
        self.assertEqual(got['samples'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)

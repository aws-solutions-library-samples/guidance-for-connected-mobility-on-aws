// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { render, screen } from '@testing-library/react';
import {
  computeCoverage,
  extractCatalogSignals,
  ManifestContent,
} from '../TransformManifestsViewer';

// ---- fixtures ----

const MOCK_CATALOG = {
  signal_groups: {
    location: {
      signals: {
        lat: {},
        lon: {},
        spd: {},
        hdg: {},
      },
    },
    diagnostics: {
      signals: {
        eng_temp: {},
        oil_life: {},
        oil_press: {},
      },
    },
    tires: {
      signals: {
        tire_fl: {},
        tire_fr: {},
      },
    },
  },
};

// Minimal OEM1-style manifest fixture (mirrors oem1-transform.json shape)
const OEM1_MANIFEST_FIXTURE: ManifestContent = {
  signal_mappings: [
    { cms_field: 'lat', source_signal: 'POSITION_LAT' },
    { cms_field: 'spd', source_signal: 'SPEED' },
    { cms_field: 'hdg', source_signal: 'HEADING' },
    { cms_field: 'eng_temp', source_signal: 'ENGINE_COOLANT_TEMP' },
    { cms_field: 'oil_life', source_signal: 'OIL_LIFE_REMAINING' },
    { cms_field: 'tire_fl', source_signal: 'TIRE_PRESSURE_FL' },
    { cms_field: 'tire_fr', source_signal: 'TIRE_PRESSURE_FR' },
  ],
  metadata: {
    deferred_signals: [
      { source_signal: 'STEERING_WHEEL_ANGLE', reason: 'no catalog match' },
      { source_signal: 'TORQUE_AT_TRANSMISSION', reason: 'no catalog match' },
      { source_signal: 'PARKING_BRAKE_STATUS', reason: 'no catalog match' },
    ],
  },
};

// ---- unit tests for computeCoverage (OEM-agnostic logic, no rendering) ----

// MOCK_CATALOG totals: location(4) + diagnostics(3) + tires(2) = 9 catalog signals
const CATALOG_TOTAL = 9;

describe('computeCoverage', () => {
  it('test_coverage_count_correct_for_oem1_manifest — covered equals signal_mappings length', () => {
    const result = computeCoverage(OEM1_MANIFEST_FIXTURE, MOCK_CATALOG);
    // covered = len(signal_mappings) = 7; total = 9 catalog signals in MOCK_CATALOG
    expect(result.covered).toBe(7);
    expect(result.total).toBe(CATALOG_TOTAL);
  });

  it('test_gap_list_includes_deferred_signals — deferred signals list is populated', () => {
    const result = computeCoverage(OEM1_MANIFEST_FIXTURE, MOCK_CATALOG);
    // deferred signals from metadata
    expect(result.deferred.length).toBeGreaterThanOrEqual(3);
    expect(result.deferred).toContain('STEERING_WHEEL_ANGLE');
    expect(result.deferred).toContain('TORQUE_AT_TRANSMISSION');
    expect(result.deferred).toContain('PARKING_BRAKE_STATUS');
  });

  it('test_gap_list_contains_unmapped_catalog_signals — gaps = catalog minus mapped cms_fields', () => {
    const result = computeCoverage(OEM1_MANIFEST_FIXTURE, MOCK_CATALOG);
    // MOCK_CATALOG has 10 signals; 7 mapped (lat,spd,hdg,eng_temp,oil_life,tire_fl,tire_fr)
    // gaps = {lon, oil_press, tire_fr} minus what's in mapped... actually tire_fr IS mapped
    // Unmapped: lon, oil_press  (lat, spd, hdg, eng_temp, oil_life, tire_fl, tire_fr are mapped)
    expect(result.gaps).toContain('lon');
    expect(result.gaps).toContain('oil_press');
    expect(result.gaps).not.toContain('lat');
  });

  it('test_coverage_works_for_arbitrary_manifest — OEM-agnostic: works with different signal_mappings', () => {
    const arbitraryManifest: ManifestContent = {
      signal_mappings: [
        { cms_field: 'lat', source_signal: 'GNSS_LAT' },
        { cms_field: 'lon', source_signal: 'GNSS_LON' },
      ],
      metadata: {
        deferred_signals: [{ source_signal: 'SYNTHETIC_SIGNAL_X', reason: 'no match' }],
      },
    };
    const result = computeCoverage(arbitraryManifest, MOCK_CATALOG);
    expect(result.covered).toBe(2);
    expect(result.total).toBe(CATALOG_TOTAL);
    expect(result.deferred).toEqual(['SYNTHETIC_SIGNAL_X']);
  });

  it('test_empty_manifest_has_zero_coverage', () => {
    const result = computeCoverage({}, MOCK_CATALOG);
    expect(result.covered).toBe(0);
    expect(result.total).toBe(CATALOG_TOTAL);
    expect(result.gaps).toHaveLength(CATALOG_TOTAL);
    expect(result.deferred).toHaveLength(0);
  });
});

describe('extractCatalogSignals', () => {
  it('extracts all signal names from all groups', () => {
    const signals = extractCatalogSignals(MOCK_CATALOG);
    expect(signals).toHaveLength(CATALOG_TOTAL);
    expect(signals).toContain('lat');
    expect(signals).toContain('tire_fr');
    expect(signals).toContain('oil_press');
  });
});

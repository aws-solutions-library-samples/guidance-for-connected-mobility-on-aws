// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Registry (index.ts) tests
 *
 * (1) rendererFor('lamp_self_check') returns LampSelfCheckRenderer
 * (2) rendererFor('unknown_id') returns RawDrawer
 * (3) Every key in ROUTINE_RENDERERS matches a spec D4 pilot routine ID
 */

import { describe, it, expect } from 'vitest';
import {
  rendererFor,
  ROUTINE_RENDERERS,
  RawDrawer,
  LampSelfCheckRenderer,
  O2HeaterCheckRenderer,
  EvapLeakTestRenderer,
  AbsPumpCycleRenderer,
  PackIsolationRenderer,
  CellBalanceCheckRenderer,
} from '../index';

// Spec D4 pilot routine IDs — the ground truth list
const PILOT_ROUTINE_IDS = [
  'lamp_self_check',
  'o2_heater_check',
  'evap_leak_test',
  'abs_pump_cycle',
  'pack_isolation_test',
  'cell_balance_check',
];

describe('rendererFor', () => {
  it('returns LampSelfCheckRenderer for lamp_self_check', () => {
    expect(rendererFor('lamp_self_check')).toBe(LampSelfCheckRenderer);
  });

  it('returns O2HeaterCheckRenderer for o2_heater_check', () => {
    expect(rendererFor('o2_heater_check')).toBe(O2HeaterCheckRenderer);
  });

  it('returns EvapLeakTestRenderer for evap_leak_test', () => {
    expect(rendererFor('evap_leak_test')).toBe(EvapLeakTestRenderer);
  });

  it('returns AbsPumpCycleRenderer for abs_pump_cycle', () => {
    expect(rendererFor('abs_pump_cycle')).toBe(AbsPumpCycleRenderer);
  });

  it('returns PackIsolationRenderer for pack_isolation_test', () => {
    expect(rendererFor('pack_isolation_test')).toBe(PackIsolationRenderer);
  });

  it('returns CellBalanceCheckRenderer for cell_balance_check', () => {
    expect(rendererFor('cell_balance_check')).toBe(CellBalanceCheckRenderer);
  });

  it('returns RawDrawer for an unknown routine ID', () => {
    expect(rendererFor('unknown_id')).toBe(RawDrawer);
  });

  it('returns RawDrawer for an empty string', () => {
    expect(rendererFor('')).toBe(RawDrawer);
  });
});

describe('ROUTINE_RENDERERS registry', () => {
  it('contains exactly the 6 spec D4 pilot routine IDs — no more, no less', () => {
    const registryKeys = Object.keys(ROUTINE_RENDERERS).sort();
    const pilotIds = [...PILOT_ROUTINE_IDS].sort();
    expect(registryKeys).toEqual(pilotIds);
  });

  it('every key in ROUTINE_RENDERERS is a recognised pilot routine ID', () => {
    const pilotSet = new Set(PILOT_ROUTINE_IDS);
    for (const key of Object.keys(ROUTINE_RENDERERS)) {
      expect(pilotSet.has(key)).toBe(true);
    }
  });
});

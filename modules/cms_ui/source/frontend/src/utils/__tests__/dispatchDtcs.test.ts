// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// FG3 (issue 2026-09-25-diagnostics-ia-uat-defects): a dispatch carries the
// vehicle's recorded ACTIVE codes as well as the scan's, because a code the
// cloud raised from telemetry is not read from an ECU and a scan need not return it.

import { describe, it, expect } from 'vitest';
import { mergeDispatchDtcs } from '../dispatchDtcs';

const P0217 = {
  code: 'P0217',
  description: 'Engine coolant critically overheated — stop driving, let engine cool',
};

describe('mergeDispatchDtcs', () => {
  it('carries a recorded-only code, marked as recorded, when the scan found nothing', () => {
    expect(mergeDispatchDtcs([], [P0217])).toEqual([
      { code: 'P0217', description: P0217.description, recorded: true },
    ]);
  });

  it('puts scan codes first and appends recorded codes the scan did not return', () => {
    const out = mergeDispatchDtcs(['P0300'], [P0217]);
    expect(out.map((d) => d.code)).toEqual(['P0300', 'P0217']);
    expect(out[0].recorded).toBeUndefined();
    expect(out[1].recorded).toBe(true);
  });

  it('lists a code in both once, as a scan code, with the recorded description', () => {
    const out = mergeDispatchDtcs(['P0217'], [P0217]);
    expect(out).toEqual([{ code: 'P0217', description: P0217.description }]);
  });

  it('drops duplicate and blank codes', () => {
    const out = mergeDispatchDtcs(['P0300', 'P0300', ' '], [P0217, { code: 'P0217' }, { code: '' }]);
    expect(out.map((d) => d.code)).toEqual(['P0300', 'P0217']);
  });

  it('trims codes so a padded duplicate is still one entry', () => {
    expect(mergeDispatchDtcs([' P0217'], [P0217]).map((d) => d.code)).toEqual(['P0217']);
  });

  it('a padded recorded code still lends its description to the scan entry', () => {
    expect(mergeDispatchDtcs(['P0217'], [{ ...P0217, code: ' P0217 ' }])).toEqual([
      { code: 'P0217', description: P0217.description },
    ]);
  });

  it('with no record loaded, returns the scan codes unchanged', () => {
    expect(mergeDispatchDtcs(['P0300'], undefined)).toEqual([{ code: 'P0300' }]);
  });

  it('keeps every record source on a record-only code, and none on a scan code', () => {
    const src = 'flink-maintenance-processor';
    expect(mergeDispatchDtcs(['P0300'], [
      { code: 'P0300', source: 'fwe-uds-dtc' },
      { ...P0217, source: src },
      { code: 'P0217', source: 'fwe-uds-dtc' },
      { code: 'P0217', source: src },
    ])).toEqual([
      { code: 'P0300' },
      { code: 'P0217', description: P0217.description, recorded: true, sources: [src, 'fwe-uds-dtc'] },
    ]);
  });
});

// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for `resolveDriverCell` — the Driver column's decision logic.
 *
 * Regression cover for
 * `issues/2026-09-23-driver-id-canonicaliser-rejects-non-numeric-ids/`.
 *
 * Reported symptom: a trip whose DynamoDB row carried
 * `driverName: 'Marcus Reyes'` rendered as **"Unassigned"** in this table, while
 * the trip-detail page showed the bare `DRV-MRDN-0015`. The backend cause was a
 * driver-ID canonicaliser that rejected the `DRV-<TOKEN>-<digits>` scheme and so
 * emitted the ID in the `driverName` field. This table's guard then correctly
 * refused to print an ID as a name — and incorrectly threw the driver away.
 *
 * The property under test: **a trip that has a driverId must never render as
 * Unassigned.** It may render the ID as the link text when the name is
 * unresolvable, but it must remain reachable.
 */
import { describe, it, expect } from 'vitest';
import { resolveDriverCell } from '../TripsTable';

describe('resolveDriverCell', () => {
  it('shows the name and links when both name and id are present', () => {
    expect(
      resolveDriverCell({ driverId: 'DRV-MRDN-0015', driverName: 'Marcus Reyes' }),
    ).toEqual({ label: 'Marcus Reyes', driverId: 'DRV-MRDN-0015' });
  });

  it('falls back to the ID as the label but still links, rather than reporting Unassigned', () => {
    // The reported bug. Mutation: returning null when the name is unusable
    // restores the old behaviour and makes this fail.
    expect(
      resolveDriverCell({ driverId: 'DRV-MRDN-0015', driverName: 'DRV-MRDN-0015' }),
    ).toEqual({ label: 'DRV-MRDN-0015', driverId: 'DRV-MRDN-0015' });
  });

  it('never returns null when a driverId is present, whatever the name holds', () => {
    // The invariant stated directly, across every driverName shape seen in the
    // data. Mutation: any early `return null` that ignores driverId fails here.
    for (const driverName of [
      undefined, '', '   ', 'DRV-0054', 'DRV-MRDN-0015', 'DRIVER-54',
      'driver_7', 'Marcus Reyes',
    ]) {
      const got = resolveDriverCell({ driverId: 'DRV-MRDN-0015', driverName });
      expect(got, `driverName=${JSON.stringify(driverName)}`).not.toBeNull();
      expect(got!.driverId).toBe('DRV-MRDN-0015');
    }
  });

  it('still refuses to display a raw ID as if it were a name', () => {
    // The original guard must survive: when the name IS an ID, the label is the
    // ID because that is honest — not because the ID was mistaken for a name.
    const got = resolveDriverCell({ driverId: 'DRV-0054', driverName: 'DRV-0054' });
    expect(got).toEqual({ label: 'DRV-0054', driverId: 'DRV-0054' });
  });

  it('renders a plain name with no link when there is no id to link to', () => {
    expect(resolveDriverCell({ driverName: 'Marcus Reyes' })).toEqual({
      label: 'Marcus Reyes',
    });
    expect(resolveDriverCell({ driverName: 'Marcus Reyes' })!.driverId).toBeUndefined();
  });

  it('returns null only when there is genuinely no driver', () => {
    // 'Unassigned' is reserved for this case. Mutation: making the function
    // return a label here would print 'undefined' or an empty link.
    expect(resolveDriverCell({})).toBeNull();
    expect(resolveDriverCell({ driverName: '' })).toBeNull();
    expect(resolveDriverCell({ driverName: '   ' })).toBeNull();
    expect(resolveDriverCell({ driverId: '', driverName: 'DRV-0054' })).toBeNull();
  });

  it('trims surrounding whitespace on the name', () => {
    expect(resolveDriverCell({ driverName: '  Marcus Reyes  ' })).toEqual({
      label: 'Marcus Reyes',
    });
  });
});

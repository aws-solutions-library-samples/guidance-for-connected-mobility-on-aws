// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the Safety Events table's presentation helpers on trip detail.
 *
 * The data was already on the page — the trip-detail endpoint returns
 * `safetyEvents`, which was counted in the summary and plotted on the map but
 * never listed, so an operator could see THAT a trip had safety events and
 * where, without seeing WHAT they were.
 *
 * Two traps these tests pin, both of which render plausible-looking wrong
 * output rather than failing visibly:
 *
 *  1. **Timestamp units.** Event rows carry milliseconds
 *     (`1790167627461`); the trips-LIST endpoint emits seconds for the same
 *     field. Assuming one unit renders 1970 or the year ~58000 for the other.
 *  2. **Severity scale.** Safety events carry the event catalog's
 *     REVERSE-RANKED numeric as a string — `"3"` means HIGH, not "3 out of 4",
 *     and `1` is LOW rather than worst. `src/utils/severity.ts` exists because
 *     rendering the two conventions interchangeably already confused operators.
 */
import { describe, it, expect } from 'vitest';
import {
  formatEventTime,
  formatEventType,
  sortSafetyEvents,
  summariseSafety,
} from '../safetyEventFormat';
import { severityLabel } from '../../../../utils/severity';

// The real event from staging trip VEH-MRDN-0015-1790167627461-8a748b.
const REAL_EVENT = {
  category: 'safety',
  severity: '3',
  vin: 'MRDN0000000000015',
  event_id: 'safety.service_steering',
  vehicleId: 'VEH-MRDN-0015',
  speed: 35.9,
  eventId: 'safety.service_steering-1790167627461-VEH-MRDN-0015',
  eventType: 'service_steering',
  detection: 'cloud',
  timestamp: 1790167627461,
  description: 'Electric power steering fault — increased steering effort required',
  lat: 33.748851,
  lng: -84.387475,
};

describe('formatEventTime', () => {
  it('reads a millisecond timestamp as the right year', () => {
    // Mutation: dropping the >9999999999 branch and multiplying by 1000 makes
    // this land in the year ~58000.
    expect(formatEventTime(REAL_EVENT.timestamp)).toContain('2026');
  });

  it('reads a second timestamp as the same instant', () => {
    // The trips-list endpoint emits seconds for this same field. Both must
    // resolve to the same calendar day.
    const asSeconds = Math.floor(REAL_EVENT.timestamp / 1000);
    expect(formatEventTime(asSeconds)).toBe(formatEventTime(REAL_EVENT.timestamp));
  });

  it('accepts a numeric string, which is how DynamoDB Numbers often arrive', () => {
    expect(formatEventTime(String(REAL_EVENT.timestamp))).toContain('2026');
  });

  it('returns an em-dash rather than "Invalid Date" for unusable input', () => {
    // Mutation: returning `new Date(x).toLocaleString()` unguarded prints
    // "Invalid Date" into the table.
    for (const bad of [null, undefined, '', 0, -1, 'not a date', NaN]) {
      expect(formatEventTime(bad)).toBe('—');
    }
  });
});

describe('formatEventType', () => {
  it('humanises the snake_case eventType', () => {
    expect(formatEventType('service_steering')).toBe('Service steering');
  });

  it('strips the dotted category from an event_id', () => {
    // The table is already titled Safety Events; repeating "safety." in every
    // row is noise.
    expect(formatEventType('safety.service_steering')).toBe('Service steering');
    expect(formatEventType('safety.antilock_brake_fault')).toBe('Antilock brake fault');
  });

  it('falls back to Unknown rather than rendering empty or undefined', () => {
    for (const bad of [null, undefined, '', 'safety.', 123, {}]) {
      expect(formatEventType(bad)).toBe('Unknown');
    }
  });
});

describe('severity rendering for safety events', () => {
  it('maps the reverse-ranked numeric string correctly', () => {
    // The trap: "3" is HIGH. A naive 1-4 ascending read would call it medium,
    // and would call 1 the worst.
    expect(severityLabel(REAL_EVENT.severity)).toBe('HIGH');
    expect(severityLabel('4')).toBe('CRITICAL');
    expect(severityLabel('1')).toBe('LOW');
    expect(severityLabel(3)).toBe('HIGH');
  });

  it('does not invent a severity for a missing one', () => {
    expect(severityLabel(undefined)).toBe('UNKNOWN');
    expect(severityLabel('')).toBe('UNKNOWN');
  });
});

describe('sortSafetyEvents', () => {
  it('orders newest first', () => {
    // Mutation: reversing the comparator, or comparing raw mixed-unit values,
    // makes this fail.
    const sorted = sortSafetyEvents([
      { timestamp: 1790103791156 },
      { timestamp: 1790167627461 },
      { timestamp: 1790103711156 },
    ]);
    expect(sorted.map((e) => e.timestamp)).toEqual([
      1790167627461, 1790103791156, 1790103711156,
    ]);
  });

  it('orders correctly across MIXED units', () => {
    // A seconds-valued row is numerically ~1000x smaller than a millis one, so
    // a raw numeric sort would always bury it last regardless of its real time.
    const older = 1790103791156;          // millis
    const newerAsSeconds = 1790167627;    // seconds — LATER in real time
    const sorted = sortSafetyEvents([{ timestamp: older }, { timestamp: newerAsSeconds }]);
    expect(sorted[0].timestamp).toBe(newerAsSeconds);
  });

  it('does not mutate its input', () => {
    const input = [{ timestamp: 1 }, { timestamp: 2 }];
    const before = input.map((e) => e.timestamp);
    sortSafetyEvents(input);
    expect(input.map((e) => e.timestamp)).toEqual(before);
  });

  it('handles an empty list and missing timestamps without throwing', () => {
    expect(sortSafetyEvents([])).toEqual([]);
    expect(sortSafetyEvents([{}, { timestamp: 5 }]).length).toBe(2);
  });
});



// ─── Per-trip safety summary ─────────────────────────────────────────────────

describe('summariseSafety', () => {
  it('buckets by CANONICAL severity, not the raw reverse-ranked value', () => {
    // The trap: "3" is HIGH and "1" is LOW. Bucketing on the raw number would
    // invert the ordering relative to the labels rendered next to it.
    const s = summariseSafety([
      { severity: '4' }, { severity: '3' }, { severity: '3' },
      { severity: '2' }, { severity: '1' },
    ]);
    expect(s).toMatchObject({
      total: 5, critical: 1, high: 2, medium: 1, low: 1, unknown: 0,
    });
  });

  it('counts an unresolvable severity as unknown rather than dropping it', () => {
    // An event whose severity cannot be resolved still happened; folding it into
    // low would understate the trip, dropping it would lose it entirely.
    const s = summariseSafety([{ severity: 'banana' }, {}, { severity: null }]);
    expect(s.total).toBe(3);
    expect(s.unknown).toBe(3);
    expect(s.low).toBe(0);
  });

  it('flags hasSevere only for CRITICAL or HIGH', () => {
    expect(summariseSafety([{ severity: '4' }]).hasSevere).toBe(true);
    expect(summariseSafety([{ severity: '3' }]).hasSevere).toBe(true);
    expect(summariseSafety([{ severity: '2' }]).hasSevere).toBe(false);
    expect(summariseSafety([{ severity: '1' }]).hasSevere).toBe(false);
    expect(summariseSafety([{ severity: 'banana' }]).hasSevere).toBe(false);
  });

  it('returns an all-zero summary for empty or missing input without throwing', () => {
    for (const input of [[], null, undefined]) {
      const s = summariseSafety(input as any);
      expect(s.total).toBe(0);
      expect(s.hasSevere).toBe(false);
    }
  });

  it('totals the real staging event', () => {
    const s = summariseSafety([REAL_EVENT]);
    expect(s).toMatchObject({ total: 1, high: 1, hasSevere: true });
  });
});

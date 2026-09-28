// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Presentation helpers for the trip-detail Safety Events table.
 *
 * A separate module rather than locals in `TripDetailView.tsx` so they can be
 * unit-tested: importing the component pulls in `@aws-sdk/client-location`, the
 * map, and the vehicle/user contexts, and that chain throws at module load
 * under vitest. Pure functions that guard against real data hazards are worth
 * being able to test directly.
 */

import { severityLabel } from '../../../utils/severity';

/**
 * Render a safety-event timestamp.
 *
 * Tolerates seconds AND milliseconds because this page receives both: the
 * trip-detail endpoint passes event `timestamp` through untouched (millis, e.g.
 * 1790167627461) while the trips-LIST endpoint converts to seconds on the way
 * out. Guessing one unit renders 1970 or the year ~58000 for the other, both of
 * which look like a plausible bug somewhere else entirely.
 *
 * The 9999999999 boundary is the same discriminator used in the backend
 * handlers (~2286 in seconds; 1970 in millis).
 */
export function formatEventTime(raw: unknown): string {
  if (raw === null || raw === undefined || raw === '') return '—';
  const n = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isFinite(n) || n <= 0) return '—';
  const ms = n > 9999999999 ? n : n * 1000;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

/**
 * Turn an event identifier into something readable.
 *
 * Handles both fields the rows carry: `eventType` (`service_steering`) and the
 * dotted `event_id` (`safety.service_steering`). The leading category is dropped
 * because the table is already titled Safety Events.
 */
export function formatEventType(raw: unknown): string {
  if (!raw || typeof raw !== 'string') return 'Unknown';
  const tail = raw.includes('.') ? raw.split('.').pop()! : raw;
  const words = tail.replace(/[_-]+/g, ' ').trim();
  if (!words) return 'Unknown';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Newest first, without mutating the source array.
 *
 * Normalises units before comparing. A seconds-valued row is numerically ~1000x
 * smaller than a millisecond one, so a raw numeric sort would bury it last
 * regardless of when it actually happened.
 */
export function sortSafetyEvents<T extends { timestamp?: unknown }>(
  events: readonly T[],
): T[] {
  const ms = (v: unknown) => {
    const n = typeof v === 'number' ? v : Number(v);
    if (!Number.isFinite(n) || n <= 0) return 0;
    return n > 9999999999 ? n : n * 1000;
  };
  return [...events].sort((a, b) => ms(b.timestamp) - ms(a.timestamp));
}


/**
 * Count a trip's safety events by canonical severity.
 *
 * Feeds the per-trip safety summary. Uses `severityLabel` rather than the raw
 * field because safety events carry the event catalog's REVERSE-RANKED numeric
 * as a string — `"3"` is HIGH, not "3 out of 4", and `1` is LOW rather than
 * worst. Bucketing on the raw value would produce a summary whose ordering is
 * inverted from its labels.
 *
 * `unknown` is a real bucket, not a silent drop: an event whose severity cannot
 * be resolved still happened, and folding it into LOW would understate the trip.
 */
export interface SafetySummary {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  unknown: number;
  /** True when anything CRITICAL or HIGH occurred — drives the summary's colour. */
  hasSevere: boolean;
}

export function summariseSafety(
  events: readonly { severity?: unknown }[] | null | undefined,
): SafetySummary {
  const summary: SafetySummary = {
    total: 0, critical: 0, high: 0, medium: 0, low: 0, unknown: 0, hasSevere: false,
  };
  if (!events || !Array.isArray(events)) return summary;
  for (const e of events) {
    summary.total += 1;
    switch (severityLabel(e?.severity)) {
      case 'CRITICAL': summary.critical += 1; break;
      case 'HIGH': summary.high += 1; break;
      case 'MEDIUM': summary.medium += 1; break;
      case 'LOW': summary.low += 1; break;
      default: summary.unknown += 1; break;
    }
  }
  summary.hasSevere = summary.critical > 0 || summary.high > 0;
  return summary;
}

// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Unit coverage for VehicleStatePanel — the UI surface for the actuator
// state loop closed by issue 2026-08-03-sim-actuator-ui-surface. Tests
// exercise both the pure helpers (`pickTelemetry`, `coerceBool`) and the
// rendered panel with representative fixtures.

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import VehicleStatePanel, {
  pickTelemetry,
  coerceBool,
  formatAge,
} from '../VehicleStatePanel';

describe('pickTelemetry', () => {
  it('returns undefined when telemetry is null or undefined', () => {
    expect(pickTelemetry(null, 'foo', 'bar')).toBeUndefined();
    expect(pickTelemetry(undefined, 'foo', 'bar')).toBeUndefined();
  });

  it('returns first present key from candidates in order', () => {
    expect(pickTelemetry({ b: 2, a: 1 }, 'a', 'b')).toBe(1);
    expect(pickTelemetry({ b: 2 }, 'a', 'b')).toBe(2);
    expect(pickTelemetry({}, 'a', 'b')).toBeUndefined();
  });

  it('treats 0 and false as present (does NOT skip)', () => {
    expect(pickTelemetry({ a: 0 }, 'a', 'b')).toBe(0);
    expect(pickTelemetry({ a: false }, 'a', 'b')).toBe(false);
  });

  it('skips null values but returns non-null on next candidate', () => {
    expect(pickTelemetry({ a: null, b: 5 }, 'a', 'b')).toBe(5);
  });

  it('supports camelCase-preferred, snake_case-fallback pattern for aliased signals', () => {
    // Simulator emits camelCase; Flink reverse-map may key either variant.
    // Whichever wins the DDB scan order becomes the latestTelemetry key.
    const camel = { allDoorsLocked: 1 };
    const snake = { all_doors_locked: 1 };
    expect(pickTelemetry(camel, 'allDoorsLocked', 'all_doors_locked')).toBe(1);
    expect(pickTelemetry(snake, 'allDoorsLocked', 'all_doors_locked')).toBe(1);
  });
});

describe('coerceBool', () => {
  it('coerces number types', () => {
    expect(coerceBool(1)).toBe(true);
    expect(coerceBool(0)).toBe(false);
    expect(coerceBool(2)).toBe(true);
  });

  it('coerces boolean types', () => {
    expect(coerceBool(true)).toBe(true);
    expect(coerceBool(false)).toBe(false);
  });

  it('coerces string types (Flink stringifies via JsonNode.asText)', () => {
    expect(coerceBool('1')).toBe(true);
    expect(coerceBool('0')).toBe(false);
    expect(coerceBool('true')).toBe(true);
    expect(coerceBool('false')).toBe(false);
    expect(coerceBool('True')).toBe(true);
    expect(coerceBool('FALSE')).toBe(false);
  });

  it('coerces numeric float-shaped strings (FWE-path values arrive as "1.0"/"0.0")', () => {
    // Value-shape divergence discovered via VEH-MICH-001 UAT 2026-08-04:
    // the FWE/Flink path stringifies floats as "1.0"/"0.0" while the
    // mqtt_direct sim path emits "1"/"0". Before this coercion tolerance,
    // every DOOR row on FWE-path vehicles rendered "—" while the trunk
    // (arriving as a real float 1.0/0.0) and ignition (arriving as
    // 'true'/'false' strings) coerced correctly — a partial-render tell.
    // Investigation to eliminate the upstream drift is tracked separately
    // (see issues/2026-08-04-actuator-value-shape-divergence/).
    expect(coerceBool('1.0')).toBe(true);
    expect(coerceBool('0.0')).toBe(false);
    expect(coerceBool('1.00')).toBe(true);
    expect(coerceBool('0.00')).toBe(false);
    // Whitespace tolerated (matches the existing trim-then-match contract).
    expect(coerceBool(' 1.0 ')).toBe(true);
    expect(coerceBool(' 0.0 ')).toBe(false);
    // Non-zero numeric strings coerce to true (matches raw number path).
    expect(coerceBool('2.5')).toBe(true);
    expect(coerceBool('-1')).toBe(true);
  });

  it('returns undefined for absent / unrecognized', () => {
    expect(coerceBool(undefined)).toBeUndefined();
    expect(coerceBool(null)).toBeUndefined();
    expect(coerceBool('maybe')).toBeUndefined();
    expect(coerceBool({})).toBeUndefined();
  });

  it('empty / non-numeric / NaN strings stay undefined (never falsely coerce to false/true)', () => {
    // Critical: the panel's whole contract is that it never falsely
    // displays a state it does not know. `Number('')` is 0 in JS, so
    // the empty-string guard is load-bearing — without it, an absent
    // telemetry field would silently render as "Unlocked". Same
    // reasoning for `'abc'` and `'NaN'` — both yield NaN via Number(),
    // both must stay unknown.
    expect(coerceBool('')).toBeUndefined();
    expect(coerceBool('   ')).toBeUndefined();
    expect(coerceBool('abc')).toBeUndefined();
    expect(coerceBool('NaN')).toBeUndefined();
    expect(coerceBool('1.0x')).toBeUndefined();
  });

  it('non-finite numbers stay undefined', () => {
    expect(coerceBool(NaN)).toBeUndefined();
    expect(coerceBool(Infinity)).toBeUndefined();
    expect(coerceBool(-Infinity)).toBeUndefined();
  });
});

describe('VehicleStatePanel', () => {
  it('renders the empty state message when no telemetry is present', () => {
    render(<VehicleStatePanel latestTelemetry={null} />);
    expect(
      screen.getByText(/No live vehicle state available/i),
    ).toBeInTheDocument();
  });

  it('renders "Locked" for all-doors + per-door state (camelCase keys)', () => {
    render(
      <VehicleStatePanel
        latestTelemetry={{
          allDoorsLocked: 1,
          doorLFLocked: 1,
          doorRFLocked: 1,
          doorLRLocked: 1,
          doorRRLocked: 1,
          bodyTrunkLocked: 1,
        }}
      />,
    );
    // Panel header
    expect(screen.getByText('Vehicle State')).toBeInTheDocument();
    // "Locked" appears once for master + once for each door + trunk = 6
    const locked = screen.getAllByText('Locked');
    expect(locked.length).toBeGreaterThanOrEqual(6);
  });

  it('reads snake_case fallback when camelCase key is absent', () => {
    // Simulates the case where DDB scan order picks the canonical
    // snake_case row's json_field for the idMeta reverse-map entry.
    render(
      <VehicleStatePanel
        latestTelemetry={{
          all_doors_locked: 0,
          door_frontleft_locked: 0,
        }}
      />,
    );
    // "Unlocked" for master + FL door at minimum
    const unlocked = screen.getAllByText('Unlocked');
    expect(unlocked.length).toBeGreaterThanOrEqual(2);
  });

  it('renders ignition + remote start state', () => {
    render(
      <VehicleStatePanel
        latestTelemetry={{
          ignitionOn: true,
          remoteStartActive: true,
        }}
      />,
    );
    expect(screen.getByText('On')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('falls back to pwrRemoteStart when remoteStartActive is absent', () => {
    // The simulator emits BOTH remoteStartActive and pwrRemoteStart;
    // if neither the camel nor snake alias wins idMeta for signal_id
    // 265, pwrRemoteStart is still emitted and we accept it.
    render(
      <VehicleStatePanel
        latestTelemetry={{
          pwrRemoteStart: 1,
        }}
      />,
    );
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('renders "Open" for charge door when open, "Closed" when closed', () => {
    const openView = render(
      <VehicleStatePanel latestTelemetry={{ chargeDoorOpen: 1 }} />,
    );
    expect(screen.getByText('Open')).toBeInTheDocument();
    openView.unmount();

    render(<VehicleStatePanel latestTelemetry={{ chargeDoorOpen: 0 }} />);
    expect(screen.getByText('Closed')).toBeInTheDocument();
  });

  it('renders "—" placeholder for fields the vehicle has not reported', () => {
    // A vehicle reporting only ignition should NOT show missing lock
    // states as "Locked" — the panel must distinguish "absent" from
    // "state=false". This is critical: falsely rendering an unlocked
    // vehicle as locked would mislead an operator.
    render(<VehicleStatePanel latestTelemetry={{ ignitionOn: true }} />);
    const placeholders = screen.getAllByText('—');
    // At least one placeholder per row that has no data
    expect(placeholders.length).toBeGreaterThan(0);
  });

  it('shows "refreshing" badge while telemetry poll is in flight', () => {
    // The `refreshing` prop is driven by RemoteCommandsPanel — surfaces
    // a subtle live-vs-stale signal so a silently-frozen panel is
    // distinguishable from a live one (issue
    // 2026-08-03-vehicle-state-panel-not-polled).
    render(
      <VehicleStatePanel
        latestTelemetry={{ allDoorsLocked: 1 }}
        refreshing
      />,
    );
    expect(screen.getByText('refreshing')).toBeInTheDocument();
  });

  it('renders "Updated Ns ago" freshness label from telemetry timestamp', () => {
    // Backend stamps `latestTelemetry.timestamp` from Redis meta
    // `lastSeenAt` (ms). The panel displays "Updated Ns ago" to make
    // freshness visible to the operator.
    const nowMs = Date.now();
    render(
      <VehicleStatePanel
        latestTelemetry={{
          allDoorsLocked: 1,
          timestamp: nowMs - 3_000, // 3 seconds ago
        }}
      />,
    );
    // Description text is rendered as-is inside the Header
    expect(
      screen.getByText(/Updated 3s ago/),
    ).toBeInTheDocument();
  });
});

describe('formatAge', () => {
  const now = 1_800_000_000_000;
  it('returns null for missing / invalid / future timestamps', () => {
    expect(formatAge(now, undefined)).toBeNull();
    expect(formatAge(now, null)).toBeNull();
    expect(formatAge(now, 0)).toBeNull();
    expect(formatAge(now, NaN)).toBeNull();
    expect(formatAge(now, now + 1000)).toBeNull();
  });

  it('renders "just now" within the first 2 seconds', () => {
    expect(formatAge(now, now - 500)).toBe('just now');
    expect(formatAge(now, now - 1_999)).toBe('just now');
  });

  it('renders seconds under 1 minute', () => {
    expect(formatAge(now, now - 5_000)).toBe('5s ago');
    expect(formatAge(now, now - 59_000)).toBe('59s ago');
  });

  it('renders minutes under 1 hour', () => {
    expect(formatAge(now, now - 60_000)).toBe('1m ago');
    expect(formatAge(now, now - 30 * 60_000)).toBe('30m ago');
  });

  it('renders hours under 1 day, null beyond', () => {
    expect(formatAge(now, now - 60 * 60_000)).toBe('1h ago');
    expect(formatAge(now, now - 23 * 60 * 60_000)).toBe('23h ago');
    expect(formatAge(now, now - 25 * 60 * 60_000)).toBeNull();
  });

  it('accepts legacy seconds-based timestamps (< 1e12)', () => {
    const nowSec = Math.floor(now / 1000);
    expect(formatAge(now, nowSec - 5)).toBe('5s ago');
  });
});

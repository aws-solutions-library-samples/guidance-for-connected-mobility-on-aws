// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Live vehicle-state panel — displays lock/ignition/trunk/remote-start
// state read from `latestTelemetry` (which flows from Flink →
// vehicle:{id}:signals in Redis → main_api /vehicles/{id} REST). This is
// the ONLY UI surface that shows the effect of Remote Commands actuator
// mutations; prior to this panel, the only visible feedback was the
// Command History row's status flip from Sent → Executed.
//
// See:
//   - issues/2026-08-03-remote-commands-simulator-actuation/summary.md
//     (simulator side — commit 9e20d97 makes actuator commands mutate
//     VehicleState and flow into telemetry)
//   - issues/2026-08-03-sim-actuator-field-mapping/report.md (catalog +
//     Flink side — camelCase json_field aliases so mutated state
//     survives to Redis)
//
// Field-name defensiveness: `latestTelemetry` keys come from Flink's
// SignalCatalogLoader reverse-map, whose "name" for a signal_id is the
// LAST json_field row scanned from DDB. DDB scan order is not
// guaranteed, so for aliased signals (all_doors_locked vs
// allDoorsLocked) either variant may be the key. `pickTelemetry` checks
// each alias in order and returns the first present value.

import React from 'react';
import {
  Container,
  Header,
  ColumnLayout,
  Box,
  StatusIndicator,
  SpaceBetween,
  Badge,
} from '@cloudscape-design/components';

interface Props {
  latestTelemetry: Record<string, unknown> | null | undefined;
  // Optional freshness signal — set to true while a refresh is in flight
  // so a subtle indicator can distinguish "live" from "silently stale".
  // The panel itself doesn't drive the poll; VehicleDetailView owns the
  // scoped fetch (issue 2026-08-03-vehicle-state-panel-not-polled).
  refreshing?: boolean;
}

/**
 * Best-effort human-readable "N seconds ago" from a millisecond epoch.
 * Returns null when the timestamp is missing / unparseable / in the
 * future.  Ignores values older than 1 day (unlikely in practice; a
 * whole-number-of-hours label there is more noise than signal).
 */
export function formatAge(now: number, tsMs: unknown): string | null {
  if (typeof tsMs !== 'number' || !Number.isFinite(tsMs) || tsMs <= 0) {
    return null;
  }
  // The backend stringifies `lastSeenAt` from Redis which is already ms;
  // guard against the (unlikely) legacy seconds case.
  const ms = tsMs > 1e12 ? tsMs : tsMs * 1000;
  const diffMs = now - ms;
  if (diffMs < 0) return null;
  const diffS = Math.floor(diffMs / 1000);
  if (diffS < 2) return 'just now';
  if (diffS < 60) return `${diffS}s ago`;
  const diffM = Math.floor(diffS / 60);
  if (diffM < 60) return `${diffM}m ago`;
  const diffH = Math.floor(diffM / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return null;
}

/**
 * Return the first defined + non-null value under any of the given keys.
 * Numbers/booleans coerce through — 0/false counts as defined.
 */
export function pickTelemetry(
  t: Record<string, unknown> | null | undefined,
  ...keys: string[]
): unknown {
  if (!t) return undefined;
  for (const k of keys) {
    const v = t[k];
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

/**
 * Coerce a "boolean-ish" telemetry value to a boolean. Returns
 * `undefined` for absent / unknown / unparseable — the panel's contract
 * is to render "—" rather than falsely display a state it does not know.
 *
 * Supported inputs:
 *   - `boolean` (passthrough)
 *   - `number` (finite: non-zero → true, zero → false; NaN/±Infinity → undefined)
 *   - `string`:
 *       - literal forms: `'1'`, `'true'`, `'yes'` → true; `'0'`, `'false'`, `'no'` → false
 *         (whitespace-trimmed, case-insensitive)
 *       - numeric strings: `'1.0'`, `'0.0'`, `'1.00'`, `' 1.0 '` etc. — parsed
 *         as a finite number, non-zero true / zero false. This case matters
 *         for FWE-path vehicles whose Flink pipeline stringifies floats as
 *         `'1.0'` where the mqtt_direct sim path emits `'1'`. See
 *         issue 2026-08-04-coerce-bool-float-strings for the upstream
 *         value-shape divergence this normalises around.
 *   - anything else (objects, empty strings, `'abc'`, `'NaN'`) → undefined
 */
export function coerceBool(v: unknown): boolean | undefined {
  if (v === undefined || v === null) return undefined;
  if (typeof v === 'boolean') return v;
  if (typeof v === 'number') return Number.isFinite(v) ? v !== 0 : undefined;
  if (typeof v === 'string') {
    const s = v.trim().toLowerCase();
    // Empty string must NOT coerce — `Number('')` is 0 which would
    // otherwise silently register as `false` and mask absence-of-data.
    if (s === '') return undefined;
    if (s === '1' || s === 'true' || s === 'yes') return true;
    if (s === '0' || s === 'false' || s === 'no') return false;
    // Numeric-string tolerance: covers `'1.0'`, `'0.0'`, `'1.00'`, etc.
    // `Number('abc')` and `Number('NaN')` yield NaN → falls through to
    // undefined below.
    const n = Number(s);
    if (Number.isFinite(n)) return n !== 0;
  }
  return undefined;
}

/**
 * Small labelled state row. Renders "—" for undefined state.
 */
const StateRow: React.FC<{
  label: string;
  value: boolean | undefined;
  trueText: string;
  falseText: string;
  // Which of true/false is the "good" state (drives status colour).
  positive: 'true' | 'false';
}> = ({ label, value, trueText, falseText, positive }) => {
  if (value === undefined) {
    return (
      <div>
        <Box variant="awsui-key-label">{label}</Box>
        <StatusIndicator type="pending">—</StatusIndicator>
      </div>
    );
  }
  const isPositive = value === (positive === 'true');
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <StatusIndicator type={isPositive ? 'success' : 'warning'}>
        {value ? trueText : falseText}
      </StatusIndicator>
    </div>
  );
};

export default function VehicleStatePanel({ latestTelemetry, refreshing }: Props) {
  const t = latestTelemetry || undefined;

  // Re-render every 5s so the "Updated Ns ago" label ticks visibly
  // between polls — otherwise a stale panel would appear to freeze at
  // "1s ago" until the next parent-driven refresh lands. This is a
  // display-only tick; the actual telemetry refresh is owned by
  // VehicleDetailView (scoped-fetch every 5s while the Remote Commands
  // tab is mounted).
  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);

  const ageLabel = t ? formatAge(now, (t as any).timestamp) : null;

  // Lock state — camelCase preferred (per patch_catalog_actuator_aliases.py),
  // snake_case fallback in case DDB scan order picks the canonical row's
  // json_field for the idMeta reverse-map entry.
  const allLocked = coerceBool(pickTelemetry(t, 'allDoorsLocked', 'all_doors_locked'));
  const doorLF = coerceBool(pickTelemetry(t, 'doorLFLocked', 'door_frontleft_locked'));
  const doorRF = coerceBool(pickTelemetry(t, 'doorRFLocked', 'door_frontright_locked'));
  const doorLR = coerceBool(pickTelemetry(t, 'doorLRLocked', 'door_rearleft_locked'));
  const doorRR = coerceBool(pickTelemetry(t, 'doorRRLocked', 'door_rearright_locked'));
  const trunk = coerceBool(pickTelemetry(t, 'bodyTrunkLocked', 'body_trunk_locked'));
  const chargeDoor = coerceBool(pickTelemetry(t, 'chargeDoorOpen', 'charge_door_open'));

  // Powertrain
  const ignition = coerceBool(pickTelemetry(t, 'ignitionOn', 'ignition_on'));
  const remoteStart = coerceBool(
    pickTelemetry(t, 'remoteStartActive', 'remote_start_active', 'pwrRemoteStart'),
  );

  const hasAnyState =
    allLocked !== undefined ||
    doorLF !== undefined ||
    doorRF !== undefined ||
    doorLR !== undefined ||
    doorRR !== undefined ||
    trunk !== undefined ||
    chargeDoor !== undefined ||
    ignition !== undefined ||
    remoteStart !== undefined;

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            hasAnyState
              ? `Live actuator + powertrain state — updates on next telemetry frame after a remote command${
                  ageLabel ? ` · Updated ${ageLabel}` : ''
                }`
              : 'Live actuator + powertrain state — updates on next telemetry frame after a remote command'
          }
          info={
            !hasAnyState ? (
              <Badge color="grey">no live state</Badge>
            ) : refreshing ? (
              <Badge color="blue">refreshing</Badge>
            ) : undefined
          }
        >
          Vehicle State
        </Header>
      }
    >
      {!hasAnyState ? (
        <Box variant="p" color="text-status-inactive">
          No live vehicle state available. Start a simulation for this vehicle
          to see actuator + powertrain state here.
        </Box>
      ) : (
        <SpaceBetween size="l">
          <ColumnLayout columns={3} variant="text-grid">
            <StateRow
              label="All doors"
              value={allLocked}
              trueText="Locked"
              falseText="Unlocked"
              positive="true"
            />
            <StateRow
              label="Trunk"
              value={trunk}
              trueText="Locked"
              falseText="Unlocked"
              positive="true"
            />
            <StateRow
              label="Charge door"
              value={chargeDoor}
              trueText="Open"
              falseText="Closed"
              positive="false"
            />
          </ColumnLayout>
          <ColumnLayout columns={4} variant="text-grid">
            <StateRow
              label="Front-left door"
              value={doorLF}
              trueText="Locked"
              falseText="Unlocked"
              positive="true"
            />
            <StateRow
              label="Front-right door"
              value={doorRF}
              trueText="Locked"
              falseText="Unlocked"
              positive="true"
            />
            <StateRow
              label="Rear-left door"
              value={doorLR}
              trueText="Locked"
              falseText="Unlocked"
              positive="true"
            />
            <StateRow
              label="Rear-right door"
              value={doorRR}
              trueText="Locked"
              falseText="Unlocked"
              positive="true"
            />
          </ColumnLayout>
          <ColumnLayout columns={2} variant="text-grid">
            <StateRow
              label="Ignition"
              value={ignition}
              trueText="On"
              falseText="Off"
              // No inherent good/bad for ignition — use "true" so on = green.
              positive="true"
            />
            <StateRow
              label="Remote start"
              value={remoteStart}
              trueText="Active"
              falseText="Inactive"
              positive="true"
            />
          </ColumnLayout>
        </SpaceBetween>
      )}
    </Container>
  );
}

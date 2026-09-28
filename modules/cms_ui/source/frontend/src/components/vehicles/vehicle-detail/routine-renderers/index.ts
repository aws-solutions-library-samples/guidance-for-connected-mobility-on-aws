// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Routine-renderer registry.
 *
 * `ROUTINE_RENDERERS` maps each pilot routine ID to its custom renderer.
 * `rendererFor(routineId)` resolves the renderer, falling back to `RawDrawer`
 * for any ID not in the registry (legacy rows, unsupported routines).
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ Group 3 T3.1
 */

import { LampSelfCheckRenderer } from './LampSelfCheckRenderer';
import { O2HeaterCheckRenderer } from './O2HeaterCheckRenderer';
import { EvapLeakTestRenderer } from './EvapLeakTestRenderer';
import { AbsPumpCycleRenderer } from './AbsPumpCycleRenderer';
import { PackIsolationRenderer } from './PackIsolationRenderer';
import { CellBalanceCheckRenderer } from './CellBalanceCheckRenderer';
import { RawDrawer } from './RawDrawer';
import type { RoutineRenderer } from './types';

export { RawDrawer };
export * from './types';
export { LampSelfCheckRenderer } from './LampSelfCheckRenderer';
export { O2HeaterCheckRenderer } from './O2HeaterCheckRenderer';
export { EvapLeakTestRenderer } from './EvapLeakTestRenderer';
export { AbsPumpCycleRenderer } from './AbsPumpCycleRenderer';
export { PackIsolationRenderer } from './PackIsolationRenderer';
export { CellBalanceCheckRenderer } from './CellBalanceCheckRenderer';
export { VerdictBanner } from './VerdictBanner';

/**
 * Registry of pilot routine IDs → custom renderers.
 * All 6 entries match the spec D4 pilot routine list.
 */
export const ROUTINE_RENDERERS: Record<string, RoutineRenderer> = {
  lamp_self_check: LampSelfCheckRenderer,
  o2_heater_check: O2HeaterCheckRenderer,
  evap_leak_test: EvapLeakTestRenderer,
  abs_pump_cycle: AbsPumpCycleRenderer,
  pack_isolation_test: PackIsolationRenderer,
  cell_balance_check: CellBalanceCheckRenderer,
};

/**
 * Resolve the renderer for a given routine ID.
 * Returns the registered custom renderer if one exists,
 * or `RawDrawer` as the fallback for unknown IDs.
 */
export function rendererFor(routineId: string): RoutineRenderer {
  return ROUTINE_RENDERERS[routineId] ?? RawDrawer;
}

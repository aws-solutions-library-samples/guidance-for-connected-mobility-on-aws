// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Fault codes for a service dispatch.
//
// A dispatch used to carry only the codes the latest full scan returned. Codes
// the cloud raises from telemetry thresholds (source `flink-maintenance-processor`)
// are not read from an ECU, so a scan need not return them, and a dispatch for a
// vehicle whose only fault was one of them carried no fault codes at all.
// Issue: issues/2026-09-25-diagnostics-ia-uat-defects/ (FG3).

import type { DispatchDtcEntry } from '../components/vehicles/vehicle-detail/DispatchModal';

/** One ACTIVE row from `GET /api/v1/vehicles/{id}/dtcs`, reduced to what dispatch needs. */
export interface RecordedDtc {
  code: string;
  description?: string;
  /** The row's `source`, e.g. `flink-maintenance-processor` for a cloud threshold detection. */
  source?: string;
}

/** DTC-history `source` for codes the cloud raises from telemetry thresholds. */
export const CLOUD_THRESHOLD_SOURCE = 'flink-maintenance-processor';

const clean = (code: unknown): string =>
  typeof code === 'string' ? code.trim() : '';

/**
 * Scan codes first, in scan order, then recorded ACTIVE codes the scan did not
 * return. Each code appears once. A recorded-only code is marked `recorded` and
 * keeps the `sources` of all its record rows, so the modal can say where it came
 * from. A code in both keeps the recorded description.
 *
 * `recorded` undefined (the list could not be loaded) contributes nothing, which
 * is the pre-FG3 behaviour, not a claim that the vehicle has no recorded faults.
 */
export function mergeDispatchDtcs(
  scanCodes: readonly string[],
  recorded: readonly RecordedDtc[] | undefined,
): DispatchDtcEntry[] {
  const descriptions = new Map<string, string>();
  const sources = new Map<string, string[]>();
  for (const r of recorded ?? []) {
    const code = clean(r?.code);
    if (code && r.description && !descriptions.has(code)) descriptions.set(code, r.description);
    if (code && r.source) {
      const list = sources.get(code) ?? [];
      if (!list.includes(r.source)) list.push(r.source);
      sources.set(code, list);
    }
  }

  const out: DispatchDtcEntry[] = [];
  const seen = new Set<string>();
  const add = (code: string, fromRecord: boolean) => {
    if (!code || seen.has(code)) return;
    seen.add(code);
    const description = descriptions.get(code);
    const codeSources = fromRecord ? sources.get(code) : undefined;
    out.push({
      code,
      ...(description ? { description } : {}),
      ...(fromRecord ? { recorded: true } : {}),
      ...(codeSources ? { sources: codeSources } : {}),
    });
  };

  for (const c of scanCodes) add(clean(c), false);
  for (const r of recorded ?? []) add(clean(r?.code), true);
  return out;
}

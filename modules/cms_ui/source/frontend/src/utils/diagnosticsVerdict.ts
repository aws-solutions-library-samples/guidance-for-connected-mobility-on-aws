// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// diagnosticsVerdict — pure utility for computing diagnostic verdict tier.
//
// Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform` Group 3, T3.2 (DX2, DX3).
// Issue: `issues/2026-09-02-event-catalog-duplicate-dtc-codes-conflicting-verdicts/`
//
// ── Design ────────────────────────────────────────────────────────────────────
//
// computeVerdict:
//   - Takes a list of DTC readings and the full event catalog.
//   - Looks up each DTC code in the catalog by `dtc_code` field.
//   - Returns the highest severity_hint across all matching entries.
//   - If a code has NO catalog match, or the catalog entry has an empty/absent
//     severity_hint, the code is "unrecognised" — it contributes no severity.
//   - If ALL codes are unrecognised, the verdict tier is 'Unrecognised'.
//   - If the DTC list is empty, the verdict tier is 'Healthy'.
//   - The result object for 'Unrecognised' must NOT carry severity,
//     severity_hint, severityHint, or level fields (DX3).
//
// assertCatalogDtcCodesUnique:
//   - Returns an array of {dtc_code} objects for every dtc_code that appears
//     more than once in the catalog. Empty array if the catalog is unique.
//   - Order-independence of the verdict is only guaranteed when every dtc_code
//     is a unique catalog key. This guard enforces that precondition.

// ── Severity ordering ─────────────────────────────────────────────────────────
//
// P0 is most severe (numeric 4), P3 is least severe (numeric 1).
// A higher SEVERITY_RANK value wins.

const SEVERITY_RANK: Record<string, number> = {
  P0: 4,
  P1: 3,
  P2: 2,
  P3: 1,
};

/**
 * Rank of a `severity_hint` for comparison purposes; 0 for anything unusable.
 *
 * Exported so consumers (e.g. the verdict banner) compare severities against the
 * SAME table the verdict was computed from, rather than declaring a second one.
 * A divergent copy would let the banner disagree with the verdict about which
 * finding is most severe — on a P0 that is a safety-relevant disagreement.
 */
export function severityRank(hint: string | undefined): number {
  if (!hint) return 0;
  return SEVERITY_RANK[hint] ?? 0;
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface CatalogEntry {
  event_id?: string;
  category?: string;
  severity?: number;
  severity_hint?: string;
  description?: string;
  dtc_code?: string;
  [key: string]: unknown;
}

export interface DtcReading {
  code: string;
  status?: string;
  ecu?: string;
}

// ── Result types ──────────────────────────────────────────────────────────────
//
// DX3: when the verdict tier is 'Unrecognised', the returned object must NOT
// contain severity, severity_hint, severityHint, or level fields.
// TypeScript discriminated unions ensure this structurally.

interface HealthyVerdict {
  tier: 'Healthy';
}

interface SeverityVerdict {
  tier: 'P0' | 'P1' | 'P2' | 'P3';
  unrecognisedCodes?: string[];
}

interface UnrecognisedVerdict {
  tier: 'Unrecognised';
  unrecognisedCodes: string[];
}

export type VerdictResult = HealthyVerdict | SeverityVerdict | UnrecognisedVerdict;

// ── assertCatalogDtcCodesUnique ───────────────────────────────────────────────

/**
 * Check that every `dtc_code` in the catalog is unique.
 *
 * Returns an array of `{ dtc_code }` objects for every code that appears more
 * than once. Returns an empty array when the catalog is violation-free.
 *
 * A non-unique catalog means a DTC code maps to multiple entries with potentially
 * conflicting severity_hint values — which makes the verdict non-deterministic
 * (it would depend on array scan order, which DynamoDB does not guarantee).
 */
export function assertCatalogDtcCodesUnique(
  catalog: CatalogEntry[],
): Array<{ dtc_code: string }> {
  const seen = new Map<string, number>();

  for (const entry of catalog) {
    if (!entry.dtc_code) continue;
    seen.set(entry.dtc_code, (seen.get(entry.dtc_code) ?? 0) + 1);
  }

  const violations: Array<{ dtc_code: string }> = [];
  for (const [code, count] of seen) {
    if (count > 1) {
      violations.push({ dtc_code: code });
    }
  }
  return violations;
}

// ── computeVerdict ────────────────────────────────────────────────────────────

/**
 * Compute the diagnostic verdict for a set of DTC readings against a catalog.
 *
 * Algorithm:
 *  1. Build a lookup map from dtc_code → highest usable severity_hint in the
 *     catalog. "Usable" means the severity_hint is a non-empty string in
 *     {P0, P1, P2, P3}. Duplicate dtc_code entries are resolved by taking
 *     the highest severity among all entries for that code (deterministic).
 *
 *  2. For each DTC reading, look up its code:
 *     - Hit with usable severity_hint → track as a known finding.
 *     - Miss or entry with unusable severity_hint → track as unrecognised.
 *
 *  3. Determine the verdict:
 *     - No DTCs → Healthy.
 *     - At least one known finding → highest severity_hint is the tier.
 *       Unrecognised codes are noted in `unrecognisedCodes` if any.
 *     - All codes unrecognised → { tier: 'Unrecognised', unrecognisedCodes }.
 *       NO severity-related fields (DX3).
 *
 * Order-independence is guaranteed because:
 *   - The catalog is converted to a Map keyed on dtc_code before any DTC is
 *     processed; array scan order has no effect on lookup results.
 *   - The highest severity is taken as the maximum over all matching entries.
 */
export function computeVerdict(
  dtcs: DtcReading[],
  catalog: CatalogEntry[],
): VerdictResult {
  // Empty DTC list → Healthy (no issues to diagnose).
  if (dtcs.length === 0) {
    return { tier: 'Healthy' };
  }

  // Build a Map from dtc_code → best usable severity rank.
  // If a code appears in the catalog multiple times, take the highest rank.
  const catalogMap = new Map<string, string>(); // code → severity_hint (e.g. 'P0')

  for (const entry of catalog) {
    const code = entry.dtc_code;
    if (!code) continue;

    const hint = entry.severity_hint;
    if (!hint || !(hint in SEVERITY_RANK)) continue; // empty or unrecognised hint

    const existingHint = catalogMap.get(code);
    if (
      existingHint === undefined ||
      SEVERITY_RANK[hint] > SEVERITY_RANK[existingHint]
    ) {
      catalogMap.set(code, hint);
    }
  }

  // Classify each DTC reading.
  let bestRank = -1;
  let bestHint: string | undefined;
  const unrecognisedCodes: string[] = [];

  for (const dtc of dtcs) {
    const hint = catalogMap.get(dtc.code);
    if (hint === undefined) {
      // No catalog match, or the catalog entry had no usable severity_hint.
      unrecognisedCodes.push(dtc.code);
    } else {
      const rank = SEVERITY_RANK[hint];
      if (rank > bestRank) {
        bestRank = rank;
        bestHint = hint;
      }
    }
  }

  // All codes unrecognised → Unrecognised verdict (DX3: no severity fields).
  if (bestHint === undefined) {
    return {
      tier: 'Unrecognised',
      unrecognisedCodes,
    };
  }

  // At least one known finding.
  const result: SeverityVerdict = {
    tier: bestHint as 'P0' | 'P1' | 'P2' | 'P3',
  };
  if (unrecognisedCodes.length > 0) {
    result.unrecognisedCodes = unrecognisedCodes;
  }
  return result;
}

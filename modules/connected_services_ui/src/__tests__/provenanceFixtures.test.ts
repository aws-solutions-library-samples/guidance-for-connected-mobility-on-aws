// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * provenanceFixtures.test.ts — T2.2 red-phase guard (fixture shape)
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T2.2
 *
 * ## What this test asserts
 *
 * Every `src/**​/*.fixture.ts` module is scanned. For each exported object or
 * array, every leaf value that is NOT an allowlisted structural key must be a
 * `{value, provenance}` pair whose `provenance` is in `VALID_PROVENANCE_MARKERS`.
 *
 * Allowlisted structural keys exempt from wrapping (spec D4 — these describe a
 * conclusion rather than being displayed values):
 *   - `id`
 *   - `computed_at`
 *   - `confidence`
 *   - `evidence`
 *   - `agent_version`
 *   - `inputs_hash`
 *
 * ## Anti-vacuity
 *
 * The "scanCoverage" suite FAILS in the red phase because no `*.fixture.ts`
 * files exist yet. The shape-assertion suite passes on an empty file set
 * (no violators), so the anti-vacuity companion is what gives the required
 * red-phase failure.
 *
 * After Group 4 creates fixture modules, both suites do real work.
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

// ── Path constants ────────────────────────────────────────────────────────────

const SRC_ROOT = path.resolve(__dirname, "..");

// ── Allowlisted keys ──────────────────────────────────────────────────────────

/**
 * Keys whose values are exempt from provenance-wrapping.
 * Spec D4: these describe a Tier2Artifact conclusion or are structural ids,
 * not displayed data values.
 */
const EXEMPT_KEYS = new Set([
  "id",
  "computed_at",
  "confidence",
  "evidence",
  "agent_version",
  "inputs_hash",
]);

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Collect all *.fixture.ts files under dir, recursively. */
function collectFixtureFiles(dir: string): string[] {
  const results: string[] = [];

  if (!fs.existsSync(dir)) return results;

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectFixtureFiles(fullPath));
    } else if (entry.isFile() && entry.name.endsWith(".fixture.ts")) {
      results.push(fullPath);
    }
  }

  return results;
}

/**
 * Check if a value is a ProvenanceValue shape: `{value: any, provenance: string}`.
 * The provenance string is validated separately; here we just verify the shape.
 */
function isProvenanceValueShape(v: unknown): v is { value: unknown; provenance: string } {
  if (v == null || typeof v !== "object" || Array.isArray(v)) return false;
  const obj = v as Record<string, unknown>;
  return "value" in obj && "provenance" in obj;
}

/** Valid markers — must stay in sync with types.ts VALID_PROVENANCE_MARKERS. */
const VALID_MARKERS = new Set(["live", "simulated", "absent"]);

interface Violation {
  filePath: string;
  keyPath: string;
  reason: string;
}

/**
 * Recursively walk an exported value and collect provenance violations.
 *
 * @param value - The value to inspect.
 * @param keyPath - Human-readable path to this value (e.g. "entries[0].vin").
 * @param violations - Accumulator.
 */
function walkValue(
  value: unknown,
  keyPath: string,
  violations: Omit<Violation, "filePath">[],
): void {
  if (value == null || typeof value !== "object") {
    // Primitive leaf — it should have been wrapped in a ProvenanceValue.
    // We only flag this if the parent was NOT an exempt key, which is
    // enforced by the caller passing keyPath that excludes exempt segments.
    // Nothing to check at a bare primitive — the parent object check handles it.
    return;
  }

  if (Array.isArray(value)) {
    value.forEach((item, idx) => {
      walkValue(item, `${keyPath}[${idx}]`, violations);
    });
    return;
  }

  // It's a plain object. Two cases:
  //   A) It is a ProvenanceValue shape — validate its provenance marker.
  //   B) It is a domain object — recurse into each non-exempt field,
  //      expecting each leaf field to be a ProvenanceValue.

  if (isProvenanceValueShape(value)) {
    // Case A: validate the marker.
    if (!VALID_MARKERS.has(value.provenance)) {
      violations.push({
        keyPath,
        reason: `provenance marker "${value.provenance}" is not in VALID_PROVENANCE_MARKERS (live, simulated, absent)`,
      });
    }
    // Do NOT recurse into value.value — its type is arbitrary.
    return;
  }

  // Case B: domain object — inspect each key.
  const obj = value as Record<string, unknown>;
  for (const key of Object.keys(obj)) {
    const fieldPath = `${keyPath}.${key}`;
    const fieldValue = obj[key];

    if (EXEMPT_KEYS.has(key)) {
      // This key is exempt (id, computed_at, etc.) — skip.
      continue;
    }

    if (fieldValue == null) {
      // null / undefined field — not a leaf in the fixture data sense.
      continue;
    }

    if (typeof fieldValue === "object") {
      if (isProvenanceValueShape(fieldValue) || Array.isArray(fieldValue)) {
        // ProvenanceValue or array — recurse normally.
        walkValue(fieldValue, fieldPath, violations);
      } else {
        // Nested domain object — recurse.
        walkValue(fieldValue, fieldPath, violations);
      }
    } else {
      // Bare primitive (string, number, boolean) where we expect a ProvenanceValue.
      violations.push({
        keyPath: fieldPath,
        reason: `bare ${typeof fieldValue} value "${String(fieldValue).slice(0, 60)}" is not wrapped in a ProvenanceValue {value, provenance}`,
      });
    }
  }
}

/**
 * Dynamically import a fixture file and collect violations across all exports.
 */
async function checkFixtureFile(filePath: string): Promise<Violation[]> {
  // Dynamic import — vitest resolves TypeScript modules natively.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mod = await import(/* @vite-ignore */ filePath) as Record<string, unknown>;
  const violations: Violation[] = [];

  for (const [exportName, exportValue] of Object.entries(mod)) {
    if (exportName === "default" || exportName.startsWith("__")) continue;

    const innerViolations: Omit<Violation, "filePath">[] = [];
    walkValue(exportValue, exportName, innerViolations);

    violations.push(
      ...innerViolations.map((v) => ({ ...v, filePath })),
    );
  }

  return violations;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("T2.2 provenanceFixtures — every *.fixture.ts leaf is a ProvenanceValue", () => {
  it("all exported fixture values have valid provenance markers on every leaf", async () => {
    const fixtureFiles = collectFixtureFiles(SRC_ROOT);
    const allViolations: Violation[] = [];

    for (const file of fixtureFiles) {
      const fileViolations = await checkFixtureFile(file);
      allViolations.push(...fileViolations);
    }

    const report = allViolations
      .map(
        (v) =>
          `${path.relative(SRC_ROOT, v.filePath)} → ${v.keyPath}: ${v.reason}`,
      )
      .join("\n");

    expect(
      allViolations,
      `Provenance fixture violations found:\n${report}`,
    ).toHaveLength(0);
  });
});

/**
 * Anti-vacuity — FAILS in the red phase (no *.fixture.ts files exist yet).
 */
describe("T2.2 provenanceFixtures anti-vacuity — scan must cover a non-empty fixture set", () => {
  it("at least one *.fixture.ts file exists under src/ (fails red-phase)", () => {
    const fixtureFiles = collectFixtureFiles(SRC_ROOT);

    expect(
      fixtureFiles.length,
      "Expected ≥1 *.fixture.ts file under src/. " +
        "This assertion fails until Group 4 creates fixture modules. " +
        "That is intentional: a guard that passes on an empty file set is vacuous.",
    ).toBeGreaterThan(0);
  });
});

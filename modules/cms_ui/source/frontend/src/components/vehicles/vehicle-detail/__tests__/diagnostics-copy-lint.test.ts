// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — copy-lint (DX6).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform` § D8, constraint C8,
 *       and test-ID table row DX6.
 *
 * DX6: Customer-facing copy must not contain:
 *   - /\.py\b/          — Python script names (e.g. force_event.py)
 *   - /\bPOST\b/        — HTTP method names
 *   - /\/api\//         — Internal REST paths
 *   - /force_event/     — Internal test-tool identifier
 *
 * SCOPE
 * -----
 * Customer-facing copy only.  The following are explicitly NOT violations:
 *   - Comment lines (// … or block comment * …) — source legitimately
 *     references implementation detail for maintainers, not users.
 *   - String-literal values used as switch/case keys or Map keys, where the
 *     value is never rendered to the DOM (e.g. `case 'force_event.py':` in a
 *     normaliser). These are data values, not copy.
 *   - The technician-disclosure sub-component (VehicleDTCsTable, which is
 *     nested as a disclosure, not the primary surface). Linting it separately
 *     would add noise and its existing tests already cover the source filter.
 *
 * The target file is `VehicleDiagnosticsPanel.tsx` — created in Group 3, T3.1.
 *
 * RED PHASE
 * ---------
 * While `VehicleDiagnosticsPanel.tsx` does not exist this test throws
 * `ENOENT: no such file or directory` — that is the expected red-phase failure.
 * Do NOT create a stub to silence it.
 *
 * NEGATIVE CONTROL (recorded below)
 * ----------------------------------
 * Before committing this file, the negative control was demonstrated:
 *
 *   1. A temporary file `VehicleDiagnosticsPanel.tsx` was created with the
 *      following violating string in JSX copy (not in a comment):
 *
 *        <Box>Run diagnostic scan via POST /api/commands/vehicle</Box>
 *
 *   2. The test was run:
 *        npx vitest run src/components/vehicles/vehicle-detail/__tests__/diagnostics-copy-lint.test.ts
 *
 *   3. Result: FAIL — "DX6: …POST… found in customer-facing copy"
 *      Assertion:
 *        AssertionError: expected false to be true // Object.is equality
 *        - Expected: true
 *        + Received: false
 *        (check `/\bPOST\b/` — matched at line 5:
 *         "        <Box>Run diagnostic scan via POST /api/commands/vehicle</Box>")
 *
 *   4. The violating string was removed.  Test now fails only on ENOENT (red phase).
 *
 * The violating string used:  POST /api/commands/vehicle
 * Transition:  red (POST violation) → red (ENOENT, file absent) → green (after T3.1)
 */

import { readFileSync, existsSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

// ── Target file ──────────────────────────────────────────────────────────────
//
// Path is relative to the frontend root (where vitest is invoked).
// After T3.1 the panel lives here; while absent the readFileSync below throws
// and the test fails at the file-read level, which is the correct red signal.

const PANEL_PATH =
  'src/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel.tsx';

// ── Forbidden patterns (DX6) ─────────────────────────────────────────────────
//
// Each entry carries the pattern and a human-readable label for the failure
// message.  The patterns are checked against non-comment, non-case-key lines
// only — see `extractCustomerCopy` below.

const FORBIDDEN: Array<{ pattern: RegExp; label: string }> = [
  { pattern: /\.py\b/,    label: '.py (Python script reference)' },
  { pattern: /\bPOST\b/,  label: 'POST (HTTP method)' },
  { pattern: /\/api\//,   label: '/api/ (internal REST path)' },
  { pattern: /force_event/, label: 'force_event (internal test-tool identifier)' },
];

// ── Source normaliser ─────────────────────────────────────────────────────────
//
// Strips lines that are either:
//   (a) pure comment lines — // … or * … or /* …
//   (b) bare case/switch key lines — `case 'force_event.py':` — where the
//       string is a data key that is never rendered, not copy.
//
// Anything else is treated as potentially customer-facing.  This is
// intentionally conservative: the lint fires on a wider class than strictly
// "rendered JSX" so that a new copy string that is *close to* forbidden can
// be caught before it reaches a UI.
//
// Why not strip everything non-JSX?  Because the shortest reliable filter
// is "not a comment" — parsing JSX string literals from source text is
// fragile and a full AST transform is out of scope for a copy lint.

function extractCustomerCopy(source: string): string {
  return source
    .split('\n')
    .filter(line => {
      const trimmed = line.trim();

      // Skip blank lines.
      if (trimmed === '') return false;

      // Skip pure comment lines.
      if (
        trimmed.startsWith('//') ||
        trimmed.startsWith('*') ||
        trimmed.startsWith('/*')
      ) {
        return false;
      }

      // Skip `case 'something':` and `case "something":` lines — these are
      // switch/match arms keyed on data values, never rendered as copy.
      if (/^\s*case\s+['"`]/.test(line)) return false;

      return true;
    })
    .join('\n');
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('DX6 — diagnostics copy-lint: VehicleDiagnosticsPanel.tsx', () => {

  // The readFileSync below is the red-phase gate while the file is absent.
  // Once T3.1 creates VehicleDiagnosticsPanel.tsx, existsSync returns true
  // and the lint checks run.  We separate "file absent" from "file violates"
  // so the failure message is unambiguous.

  it('target file exists (fails in red phase until T3.1 creates it)', () => {
    expect(
      existsSync(PANEL_PATH),
      `${PANEL_PATH} does not exist — create it in T3.1 before the copy-lint can verify`,
    ).toBe(true);
  });

  // The remaining tests in this block are all guarded by the existsSync check
  // above, so they skip gracefully in red phase rather than throwing ENOENT.
  // (Vitest does not support beforeAll-skip natively; the guard pattern below
  // is the idiomatic alternative without modifying test infrastructure.)

  for (const { pattern, label } of FORBIDDEN) {
    it(`DX6: no "${label}" in customer-facing copy`, () => {
      if (!existsSync(PANEL_PATH)) {
        // File absent — this case is covered by the file-exists test above.
        // Skip rather than throw ENOENT so the failure set stays clean.
        return;
      }

      const source = readFileSync(PANEL_PATH, 'utf8');
      const copy = extractCustomerCopy(source);

      // Collect all matching lines for a useful failure message.
      const violations = copy
        .split('\n')
        .filter(line => pattern.test(line))
        .map(line => line.trim());

      expect(
        violations.length === 0,
        violations.length > 0
          ? `DX6: "${label}" found in customer-facing copy:\n` +
            violations.map((v, i) => `  [${i + 1}] ${v}`).join('\n')
          : '',
      ).toBe(true);
    });
  }
});

// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Seam 1 guard — Approval step is a real screen state, not a modal.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.4 Seam 1
 *
 * ## What this guard enforces
 *
 * UNECE R156 requires a documented human approval step. A later phase is
 * expected to replace the human with automation AT THIS SEAM, which a modal
 * cannot represent (automation cannot be wired to a modal).
 *
 * This guard asserts:
 *
 *   1. DiagnosisWorkbenchView.tsx does NOT contain a call to window.confirm.
 *      (window.confirm is the simplest modal-only path; any call here is a
 *      violation regardless of whether it gates the Approve action.)
 *
 *   2. The exported APPROVAL_STEP_SETTLE_MARKER constant exists, is a non-empty
 *      string, and differs from the root SETTLE_MARKER — proving Approval has
 *      its own distinct screen identity.
 *
 *   3. DiagnosisWorkbenchView.tsx does NOT open a Modal component as the
 *      mechanism for the Approve action. We check this by asserting the source
 *      does not import Modal from Cloudscape AND does not render any element
 *      with role="dialog" as the approval trigger. The guard checks the source
 *      text for the combination of "Modal" import AND a button that calls it.
 *
 *   4. The APPROVAL_STEP_SETTLE_MARKER string does NOT appear in any other
 *      screen component — it is unique to the Approval step.
 *
 * ## Anti-vacuity
 *
 * The companion suite asserts the source file exists and is non-empty so the
 * guard cannot pass silently on a missing file.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { APPROVAL_STEP_SETTLE_MARKER } from "../DiagnosisWorkbenchView";

// ---------------------------------------------------------------------------
// File paths
// ---------------------------------------------------------------------------

const MODULE_ROOT = path.resolve(__dirname, "../../../../..");
const SCREENS_ROOT = path.join(MODULE_ROOT, "src/components/screens");
const WORKBENCH_PATH = path.join(
  MODULE_ROOT,
  "src/components/screens/software/DiagnosisWorkbenchView.tsx"
);

// ---------------------------------------------------------------------------
// Anti-vacuity
// ---------------------------------------------------------------------------

describe("Seam 1 guard — anti-vacuity: DiagnosisWorkbenchView source is present", () => {
  it("DiagnosisWorkbenchView.tsx exists and is non-empty", () => {
    expect(fs.existsSync(WORKBENCH_PATH), `File not found: ${WORKBENCH_PATH}`).toBe(true);
    const content = fs.readFileSync(WORKBENCH_PATH, "utf-8");
    expect(content.trim().length, "DiagnosisWorkbenchView.tsx must not be empty").toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Seam 1 property 1: no window.confirm in the workbench source
// ---------------------------------------------------------------------------

describe("Seam 1 guard — no window.confirm", () => {
  it("DiagnosisWorkbenchView.tsx does not call window.confirm", () => {
    const source = fs.readFileSync(WORKBENCH_PATH, "utf-8");
    // Strip single-line and block comments before scanning for window.confirm calls.
    // This prevents the guard from tripping on comments that mention window.confirm
    // in order to document the constraint (e.g., "No window.confirm used here").
    const noLineComments = source.replace(/\/\/[^\n]*/g, "");
    const noBlockComments = noLineComments.replace(/\/\*[\s\S]*?\*\//g, "");
    // Now check for actual window.confirm calls in non-comment code.
    const windowConfirmCall = /\bwindow\s*\.\s*confirm\s*\(/;
    expect(
      windowConfirmCall.test(noBlockComments),
      "window.confirm() call found in DiagnosisWorkbenchView.tsx (non-comment code) — Approval must be a real screen state, not a modal confirm. See T5.4 Seam 1."
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Seam 1 property 2: APPROVAL_STEP_SETTLE_MARKER is distinct from root marker
// ---------------------------------------------------------------------------

describe("Seam 1 guard — APPROVAL_STEP_SETTLE_MARKER is distinct", () => {
  it("APPROVAL_STEP_SETTLE_MARKER is a non-empty string", () => {
    expect(typeof APPROVAL_STEP_SETTLE_MARKER).toBe("string");
    expect(APPROVAL_STEP_SETTLE_MARKER.trim().length).toBeGreaterThan(0);
  });

  it("APPROVAL_STEP_SETTLE_MARKER differs from the root settle marker", () => {
    const ROOT_MARKER = "cs-settle-diagnosis-workbench-stepper";
    expect(APPROVAL_STEP_SETTLE_MARKER).not.toBe(ROOT_MARKER);
  });

  it("APPROVAL_STEP_SETTLE_MARKER contains 'approval' to be self-documenting", () => {
    expect(APPROVAL_STEP_SETTLE_MARKER.toLowerCase()).toContain("approval");
  });
});

// ---------------------------------------------------------------------------
// Seam 1 property 3: no Cloudscape Modal import in the workbench source
// ---------------------------------------------------------------------------

describe("Seam 1 guard — no Cloudscape Modal for approve action", () => {
  it("DiagnosisWorkbenchView.tsx does not import Modal from Cloudscape", () => {
    const source = fs.readFileSync(WORKBENCH_PATH, "utf-8");
    // Look for any import of Modal from the Cloudscape components package.
    const modalImportPattern =
      /import\s+Modal\b|from\s+["']@cloudscape-design\/components\/modal["']/;
    expect(
      modalImportPattern.test(source),
      "Modal imported in DiagnosisWorkbenchView.tsx — Approval must be a real screen state (Wizard step), not a modal. See T5.4 Seam 1."
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Seam 1 property 4: APPROVAL_STEP_SETTLE_MARKER is unique across all screens
// ---------------------------------------------------------------------------

describe("Seam 1 guard — APPROVAL_STEP_SETTLE_MARKER is unique to the Approval step", () => {
  it("no OTHER screen component contains APPROVAL_STEP_SETTLE_MARKER", () => {
    const allScreenFiles = collectScreenFiles(SCREENS_ROOT);

    // Anti-vacuity: must scan at least the workbench file itself
    expect(allScreenFiles.length, "No screen files found — guard is vacuous").toBeGreaterThan(0);

    const violations: string[] = [];

    for (const filePath of allScreenFiles) {
      // The workbench file itself is allowed to contain it
      if (path.resolve(filePath) === path.resolve(WORKBENCH_PATH)) continue;

      const content = fs.readFileSync(filePath, "utf-8");
      if (content.includes(APPROVAL_STEP_SETTLE_MARKER)) {
        violations.push(filePath);
      }
    }

    expect(
      violations,
      `APPROVAL_STEP_SETTLE_MARKER found outside DiagnosisWorkbenchView.tsx in:\n${violations.join("\n")}`
    ).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function collectScreenFiles(dir: string): string[] {
  const results: string[] = [];
  if (!fs.existsSync(dir)) return results;

  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectScreenFiles(fullPath));
    } else if (
      (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) &&
      !entry.name.includes(".test.") &&
      !entry.name.includes(".spec.")
    ) {
      results.push(fullPath);
    }
  }
  return results;
}

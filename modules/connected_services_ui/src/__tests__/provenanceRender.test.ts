// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * provenanceRender.test.ts — T2.2 red-phase guard (render path)
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T2.2
 *
 * ## What this test asserts
 *
 * Every file under `src/components/screens/**​/*.tsx` is scanned for JSX
 * interpolations of the form `{someVar}` or `{someExpr}`. Any interpolation
 * that looks like it is rendering a fixture-derived field (i.e., a variable
 * or property access that does NOT come wrapped through `ProvenanceField` or
 * another component that accepts a `ProvenanceValue`) is a violation.
 *
 * The check is structural: we look for JSX expression containers whose text
 * contains a `.value` dereference outside a `ProvenanceField` component call.
 * This mirrors the PRD's intent ("lint fails the build if a component renders
 * a numeric value with no provenance prop") without attempting general type
 * inference (which spec D4 records as not statically decidable).
 *
 * Concretely, the guard scans screen files for:
 *   - JSX expression containers `{...}` that contain `.value` or `.value}` —
 *     the typical pattern when a developer unwraps a ProvenanceValue directly
 *     instead of passing it to ProvenanceField.
 *   - Direct JSX interpolation of `.vin`, `.iccid`, `.status`, `.imsi`,
 *     `.profile`, `.market`, `.policyReference`, `.isDenied`, `.bearer`,
 *     `.signal` — field names declared in types.ts as ProvenanceValue<T> fields.
 *     If these appear directly in `{...}` without going through ProvenanceField,
 *     the guard fires.
 *
 * False-positive suppression:
 *   - Any line that is inside a `<ProvenanceField ... />` JSX element is exempt,
 *     including multi-line usages where the opening tag and the prop appear on
 *     different lines. The guard tracks element nesting depth so a prop line like
 *     `field={payload.market}` is not flagged when the enclosing opening tag is
 *     `<ProvenanceField` on the previous line.
 *   - Lines that contain `ProvenanceValue` in the immediate context are exempt
 *     (type annotation, not a render call).
 *   - Lines that contain `assertProvenance` are exempt (the enforcement call
 *     itself, not a render).
 *   - Comments are stripped before matching.
 *
 * ## Anti-vacuity
 *
 * The "scanCoverage" suite FAILS in the red phase because no screen files
 * exist yet. The render-path suite passes on an empty file set.
 *
 * Reports: file path, line number, and the matched expression.
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

// ── Path constants ────────────────────────────────────────────────────────────

const SCREENS_ROOT = path.resolve(
  __dirname,
  "..",
  "components",
  "screens",
);

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Collect all *.tsx files under dir, recursively (excluding __tests__/). */
function collectScreenFiles(dir: string): string[] {
  const results: string[] = [];
  if (!fs.existsSync(dir)) return results;

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__") continue;
      results.push(...collectScreenFiles(fullPath));
    } else if (entry.isFile() && entry.name.endsWith(".tsx")) {
      results.push(fullPath);
    }
  }

  return results;
}

/**
 * ProvenanceValue field names declared in types.ts.
 * A JSX interpolation directly accessing these fields on a fixture-derived
 * object is a provenance violation — they must go through ProvenanceField.
 */
const PROVENANCE_FIELD_NAMES = [
  "vin",
  "iccid",
  "imsi",
  "profile",
  "market",
  "policyReference",
  "isDenied",
  "bearer",
  "signal",
  "status",
  "entries",
];

/**
 * Patterns that indicate a bare unwrap of a ProvenanceValue in JSX.
 * Each pattern is tried against each non-comment line of a screen file.
 *
 * ## What the guard keys on
 *
 * A ProvenanceValue must not be DISPLAYED without provenance. The guard fires
 * when `.value` is the FINAL token inside a JSX expression container — i.e.,
 * `.value}` closes the expression directly — because that means the raw value
 * is being rendered as visible DOM content with no provenance wrapping.
 *
 * The guard does NOT fire when `.value` is:
 *   - used in a conditional test: `{payload.x.value !== null && ...}` — the
 *     comparison operator (`!==`, `!=`, `===`, etc.) follows `.value`, so
 *     `.value}` is never reached directly.
 *   - passed as a function argument: `type={scoreType(payload.x.value ?? 0)}`
 *     — `.value` is followed by ` ??`, `,`, `)`, etc., not `}` directly.
 *   - extracted to a local variable before JSX: `const v = payload.x.value`
 *     — this line does not contain a JSX expression container `{...}`.
 *
 * This is coherent: the same file does `const trendValue = payload.trend.value`
 * (a pre-JSX extraction that the guard permits) and then uses `trendValue` in
 * a conditional exactly as lines 121/124 in QualitySignalsView do. Forbidding
 * `.value` inside a conditional while permitting the pre-extraction of the same
 * value would be incoherent — both patterns access the raw value for a non-
 * display purpose (guard test / prop computation). The rule is: display requires
 * wrapping. Non-display use (test, prop, local variable) does not.
 */
const FORBIDDEN_RENDER_PATTERNS: { pattern: RegExp; reason: string }[] = [
  // Direct .value} at end of a JSX expression container — the raw value is
  // being rendered as visible DOM content without provenance wrapping.
  // This matches {someExpr.value} but NOT {fn(x.value ?? 0)} or {x.value !== null && ...}
  // because in those cases .value is not immediately followed by `}`.
  {
    pattern: /\{[^}]*\.value\}/,
    reason: "bare .value} dereference in JSX — pass the ProvenanceValue to ProvenanceField instead",
  },
];

// Add patterns for each known ProvenanceValue field name
for (const field of PROVENANCE_FIELD_NAMES) {
  FORBIDDEN_RENDER_PATTERNS.push({
    // Matches: {something.vin} or {something.vin.value} — direct interpolation
    // without going through ProvenanceField
    pattern: new RegExp(`\\{[^}]*\\.${field}[}\\s]`),
    reason: `direct interpolation of .${field} in JSX — this field is a ProvenanceValue<T>; pass to ProvenanceField`,
  });
}

/**
 * JSX element opening tags whose props are the sanctioned path for ProvenanceValue fields.
 * Any line that falls inside one of these elements is exempt from the render-path check,
 * regardless of which line carries the opening tag.
 *
 * Matches components that accept a ProvenanceValue prop — i.e., the prop passed to them
 * is a structured ProvenanceValue, not a bare interpolated string.
 */
const PROVENANCE_SAFE_COMPONENT_PATTERN = /<ProvenanceField\b/;

/**
 * Mark lines that are inside a provenance-safe JSX element as exempt.
 *
 * We track a nesting counter: when we see an opening tag matching
 * PROVENANCE_SAFE_COMPONENT_PATTERN, we set inSafeElement=true and increment
 * depth. Every subsequent `<` on any line increments depth, every `/>` or `</`
 * decrements it. When depth returns to zero we clear the flag.
 *
 * In practice <ProvenanceField> is always self-closing (`/>`), so the counter
 * rarely goes beyond 1, but we track depth to be correct for the general case.
 *
 * Returns a boolean array parallel to the source lines: true means "inside a
 * provenance-safe element" and the line must not be checked.
 */
function computeSafeElementLines(source: string): boolean[] {
  const lines = source.split("\n");
  const safe: boolean[] = new Array(lines.length).fill(false);
  let depth = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Do not arm on comment lines — a comment mentioning `<ProvenanceField` is
    // not an actual JSX element open and must not start tracking depth.
    const isComment = /^\s*\/\//.test(line) || /^\s*\*/.test(line);

    if (depth === 0 && !isComment && PROVENANCE_SAFE_COMPONENT_PATTERN.test(line)) {
      // Opening tag found — mark this line and start tracking.
      depth = 1;
      safe[i] = true;

      // Check if the element closes on the same line (e.g. single-line `<ProvenanceField ... />`).
      // Count any additional `<` opens and `/>` / `</` closes on this same line.
      // We already counted one open above, so look for closes on this line.
      const closesOnSameLine = (line.match(/\/>/g) || []).length;
      if (closesOnSameLine > 0) {
        depth -= closesOnSameLine;
      }
      continue;
    }

    if (depth > 0) {
      safe[i] = true;
      // Count tag opens that might nest (e.g. children inside the element).
      const opens = (line.match(/<[A-Za-z]/g) || []).length;
      // Count self-closes and explicit closes.
      const selfCloses = (line.match(/\/>/g) || []).length;
      const endTags = (line.match(/<\/[A-Za-z]/g) || []).length;
      depth += opens - selfCloses - endTags;
      if (depth < 0) depth = 0;
    }
  }

  return safe;
}

/**
 * Files exempt from the render-path check as a WHOLE, not line-by-line.
 *
 * Only for components this repo maintains **byte-identical** to a CMS canonical,
 * where the parity guard (`scripts/verify_cs_log_viewer_parity.py`) forbids
 * editing the CS copy to satisfy a CS-only lint. Editing it to silence this test
 * would fail that guard; silencing that guard would reintroduce the drift it
 * exists to catch. A file-level exemption is the only move that keeps both true.
 *
 * `SimLogViewer.tsx` (added 2026-09-22 by
 * `issues/2026-09-22-cs-simulate-missing-sim-console-and-agent-controls/`) trips
 * four heuristic false positives, none of which is a ProvenanceValue:
 *   - `s.status` / `s.id` — fields of a simulation summary from GET /list, plain
 *     strings off an API response.
 *   - `simId.value` ×3 — the `value` of a Cloudscape `Select` option object
 *     (`{ value, label }`), not a ProvenanceValue unwrap.
 * All four also sit in plain async functions and template literals, not in JSX
 * at all — the guard's JSX detection over-reaches on this file.
 *
 * NARROWNESS IS ENFORCED BELOW: an entry must name a file that (a) exists and
 * (b) is genuinely mirrored, i.e. the CMS sibling exists at the recorded path.
 * So this cannot quietly become a place to park a real violation — delete the
 * mirror and the exemption fails rather than silently exempting nothing, which
 * is the same failure mode `cssScoped.test.ts` guards against for
 * COMPONENT_OWNED_EXEMPT.
 */
const MIRRORED_FILE_EXEMPT: ReadonlyMap<string, string> = new Map([
  [
    "SimLogViewer.tsx",
    "modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/SimLogViewer.tsx",
  ],
]);

/** Lines that are exempt from the render-path check (false-positive suppression). */
const EXEMPT_LINE_PATTERNS: RegExp[] = [
  // Type annotations
  /ProvenanceValue/,
  // The enforcement function
  /assertProvenance/,
  // Comments
  /^\s*\/\//,
  /^\s*\*/
];

function isLineExempt(line: string): boolean {
  return EXEMPT_LINE_PATTERNS.some((p) => p.test(line));
}

interface RenderViolation {
  filePath: string;
  lineNumber: number;
  line: string;
  reason: string;
}

function checkScreenFile(filePath: string): RenderViolation[] {
  const source = fs.readFileSync(filePath, "utf8");
  const lines = source.split("\n");
  // Compute which lines sit inside a provenance-safe JSX element — these are
  // always exempt regardless of their content (the enclosing element is the
  // sanctioned path).
  const safeElementLines = computeSafeElementLines(source);
  const violations: RenderViolation[] = [];

  lines.forEach((line, idx) => {
    // Exempt: inside a <ProvenanceField .../> element (multi-line or single-line)
    if (safeElementLines[idx]) return;
    // Exempt: type annotation, enforcement call, comment
    if (isLineExempt(line)) return;

    for (const { pattern, reason } of FORBIDDEN_RENDER_PATTERNS) {
      if (pattern.test(line)) {
        violations.push({
          filePath,
          lineNumber: idx + 1,
          line: line.trim(),
          reason,
        });
        break; // one violation per line
      }
    }
  });

  return violations;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe(
  "T2.2 provenanceRender — no screen file renders a fixture-derived field outside ProvenanceField",
  () => {
    it(
      "no screen file directly interpolates a ProvenanceValue field in JSX",
      () => {
        const screenFiles = collectScreenFiles(SCREENS_ROOT);
        const allViolations: RenderViolation[] = [];

        for (const file of screenFiles) {
          if (MIRRORED_FILE_EXEMPT.has(path.basename(file))) continue;
          allViolations.push(...checkScreenFile(file));
        }

        const report = allViolations
          .map(
            (v) =>
              `${path.relative(SCREENS_ROOT, v.filePath)}:${v.lineNumber} — ${v.reason}\n  > ${v.line}`,
          )
          .join("\n");

        expect(
          allViolations,
          `Provenance render-path violations found:\n${report}`,
        ).toHaveLength(0);
      },
    );

    it("every MIRRORED_FILE_EXEMPT entry is a real, still-mirrored file", () => {
      // Anti-vacuity, same shape as cssScoped.test.ts's stale-entry check. A
      // stale entry reads as coverage while exempting nothing; worse, if the file
      // stops being a mirror the exemption's whole justification evaporates and a
      // real violation could hide behind it.
      const repoRoot = path.resolve(SCREENS_ROOT, "../../../../..");
      const discovered = new Set(
        collectScreenFiles(SCREENS_ROOT).map((f) => path.basename(f)),
      );

      for (const [basename, cmsRelPath] of MIRRORED_FILE_EXEMPT) {
        expect(
          discovered,
          `Stale MIRRORED_FILE_EXEMPT entry "${basename}" — no such screen file. ` +
            `Remove the entry.`,
        ).toContain(basename);

        // The exemption is justified ONLY by the byte-identical-mirror
        // constraint. If the CMS canonical is gone, the file is no longer a
        // mirror and must face the normal check.
        const cmsPath = path.join(repoRoot, cmsRelPath);
        expect(
          fs.existsSync(cmsPath),
          `MIRRORED_FILE_EXEMPT entry "${basename}" claims a CMS canonical at ` +
            `${cmsRelPath}, which does not exist (resolved: ${cmsPath}). Either ` +
            `fix the path or drop the exemption — without a canonical there is ` +
            `no parity constraint forcing this file to keep the flagged lines.`,
        ).toBe(true);
      }
    });
  },
);

/**
 * Anti-vacuity — FAILS in the red phase (no screen files exist yet).
 */
describe(
  "T2.2 provenanceRender anti-vacuity — scan must cover a non-empty screens tree",
  () => {
    it(
      "at least one *.tsx screen file exists under src/components/screens/ (fails red-phase)",
      () => {
        const screenFiles = collectScreenFiles(SCREENS_ROOT);

        expect(
          screenFiles.length,
          "Expected ≥1 *.tsx file under src/components/screens/. " +
            "This assertion fails until Group 4 creates screen components. " +
            "That is intentional: a guard that passes on an empty file set is vacuous.",
        ).toBeGreaterThan(0);
      },
    );
  },
);

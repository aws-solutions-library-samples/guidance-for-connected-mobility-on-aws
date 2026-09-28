// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * cssScoped.test.ts — CSS structural and scoping guard for every stylesheet
 * under `src/`.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md D9, T3.2
 * Modelled on: DMS frontend/src/components/commons/__tests__/cssSyntax.test.ts
 *
 * ## Why this exists
 *
 * DMS's PageHeader.css arrived with a stray orphan `}` — a syntax error that
 * browsers silently tolerate, `tsc` cannot see, and hundreds of tests did not
 * notice. It was introduced by a line-range deletion whose own guard asserted
 * that the removed text CONTAINED the expected markers, but not that it
 * contained the trailing brace.
 *
 * Additionally, CMS's Header.css contains an unscoped
 * `[class*="awsui_body-cell"]` cascade that restyles every table cell in the
 * app. The T3.2 constraint explicitly names this as a rule to NOT port.
 *
 * ## What it checks
 *
 * 1. Every CSS file: braces balance; no orphan closing brace.
 * 2. Every CSS file except named shell-scope exemptions: every selector is
 *    scoped to .cs-header* or .page-header*.
 *
 * ## Shell-scope exemption
 *
 * AppShell.css (T3.3) is the ONE file exempt from the scoping rule by design.
 * Its job is the global layout frame: sticky #h, AppLayout nav transparency,
 * body overflow containment. Those rules target document-level elements and
 * Cloudscape's own generated class names and CANNOT be scoped to .cs-header.
 *
 * Any other exempt file requires a deliberate decision recorded in decisions.md,
 * not a quiet append to the exempt set. The guard re-verifies against a real
 * violation on any change to the exempt set.
 *
 * ## Deliberate unscoped selectors in PageHeader.css
 *
 * PageHeader.css intentionally uses two combinators that match Cloudscape's
 * generated class names as siblings/children of .cs-header:
 *
 *   .cs-header + [class*="awsui_layout"]          — overlap enablement
 *   .cs-header + [class*="awsui_layout"] > [class*="awsui_background"]
 *
 * These selectors ARE scoped — they are all prefixed by ".cs-header +" or
 * ".cs-header + [...] >", so the guard accepts them as correctly scoped.
 * The cssSyntax.test.ts approach (used here) checks that EVERY compound
 * part of a selector mentions a permitted root class. Because .cs-header
 * appears in every such compound, the guard passes correctly.
 *
 * However, if a developer were to add a bare `[class*="awsui_layout"]`
 * selector that did NOT start from .cs-header, the guard would reject it.
 * This is the deliberate observation T3.2 asks us to record.
 *
 * Spec: T3.2 Constraints note — "every selector in this module's CSS is
 * scoped to a .cs-header* / shell selector, with named single-file
 * exemptions carrying a rationale comment."
 */

import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC_ROOT = path.resolve(import.meta.dirname, "..");

/** Recursively collect every .css file under `dir`. */
function findCssFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules" && entry.name !== "dist") {
        findCssFiles(full, acc);
      }
    } else if (entry.name.endsWith(".css")) {
      acc.push(full);
    }
  }
  return acc;
}

/** Strip block comments so braces inside prose comments are not counted. */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

const cssFiles = findCssFiles(SRC_ROOT);

// ── Anti-vacuity ──────────────────────────────────────────────────────────────

describe("CSS guard anti-vacuity", () => {
  it("finds at least one stylesheet under src/ (guard is non-vacuous)", () => {
    // Without this, every assertion below would trivially pass if the discovery
    // walk broke or the CSS files moved to a different location.
    expect(
      cssFiles.length,
      "No .css files found under src/. Either the discovery walk is broken or " +
        "PageHeader.css was not created. Every following test would trivially pass " +
        "on an empty file set — that is the vacuity this check prevents.",
    ).toBeGreaterThan(0);
  });

  it("PageHeader.css is among the discovered stylesheets", () => {
    const names = cssFiles.map((f) => path.basename(f));
    expect(
      names,
      "PageHeader.css not found — T3.2 must create it at " +
        "src/components/commons/PageHeader.css",
    ).toContain("PageHeader.css");
  });
});

// ── Brace balance ─────────────────────────────────────────────────────────────

describe("CSS brace balance — no orphan closing brace", () => {
  it.each(cssFiles.map((f) => [path.relative(SRC_ROOT, f), f] as [string, string]))(
    "%s — braces balance and no orphan }",
    (_rel, file) => {
      const css = stripComments(fs.readFileSync(file, "utf8"));

      let depth = 0;
      let line = 1;
      for (const ch of css) {
        if (ch === "\n") line += 1;
        else if (ch === "{") depth += 1;
        else if (ch === "}") {
          depth -= 1;
          // Negative depth means a `}` closed a rule that was never opened.
          expect(
            depth,
            `orphan '}' at line ${line} in ${path.relative(SRC_ROOT, file)}`,
          ).toBeGreaterThanOrEqual(0);
        }
      }

      expect(
        depth,
        `Unclosed rule at end of ${path.relative(SRC_ROOT, file)} — more { than }`,
      ).toBe(0);
    },
  );
});

// ── Selector scoping ──────────────────────────────────────────────────────────
//
// Every selector in every CSS file (except named exemptions) must mention a
// permitted root class: .cs-header* or .page-header*.
//
// The test parses selectors by splitting on `}` and taking the text before
// each `{`. @-rules (@media, @supports) are treated as containers and skipped.
// Each comma-separated compound of a selector is checked independently.

/**
 * Shell-scope exemptions.
 *
 * AppShell.css is the ONE file deliberately exempt from the scoping rule.
 * Its rules target document-level elements and Cloudscape generated classes
 * that CANNOT be scoped to .cs-header.
 *
 * Any additional exemption requires a deliberate decision in decisions.md.
 */
const SHELL_SCOPE_EXEMPT = new Set(["AppShell.css"]);

/**
 * Component-owned stylesheet exemptions.
 *
 * A DIFFERENT category from SHELL_SCOPE_EXEMPT, deliberately kept separate.
 * Shell exemptions are for document-level rules that cannot be scoped at all.
 * These are for a stylesheet that is imported BY a single component and declares
 * exactly one class that only that component renders.
 *
 * `FWELogViewer.css` is here because it fixes a real defect and cannot satisfy
 * the .cs-header scoping rule. `FWELogViewer.tsx` is maintained byte-identical
 * across CS and the CMS fleet manager (enforced by
 * `scripts/verify_cs_log_viewer_parity.py`), and the terminal styling for its log
 * pane used to live only in the CMS app's global `styles/theme.css`. The mirrored
 * copy therefore rendered its log lines as unstyled body prose in CS, and the
 * parity guard stayed green throughout because it compared the two .tsx files
 * and nothing else. Moving the rule into a stylesheet the component imports is
 * what makes the styling travel with it; scoping the selector to `.cs-header`
 * would make the file CS-specific and reintroduce the divergence.
 *
 * The exemption is NARROW AND MECHANICALLY ENFORCED, not a blanket pass: a file
 * listed here must declare exactly ONE selector, and that selector must be a
 * single bare class. Grow it a second rule, a descendant combinator, or an
 * element selector and the exemption stops applying — the file is then subject
 * to the normal scoping rule and fails. So this set cannot quietly become a
 * place to hide global cascades of the `[class*="awsui_body-cell"]` kind that
 * T3.2 exists to keep out.
 *
 * Recorded in `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/decisions.md`.
 *
 * `SimLogViewer.css` joins it 2026-09-22 for the identical reason, one step
 * ahead of the defect rather than behind it. `SimLogViewer.tsx` is the CMS
 * trip-simulator console, mirrored into CS by
 * `issues/2026-09-22-cs-simulate-missing-sim-console-and-agent-controls/`. It
 * renders its pane with the same `.theme-log-viewer` class and, in CMS, resolves
 * it from that app's global `styles/theme.css` — which CS has no copy of. So a
 * byte-identical mirror with no co-located stylesheet would have reproduced the
 * FWELogViewer defect exactly: an unstyled "console" that no parity guard could
 * see. Both files declare the same single rule; duplicate identical CSS is
 * harmless and co-location is what makes each component self-sufficient.
 */
const COMPONENT_OWNED_EXEMPT = new Set(["FWELogViewer.css", "SimLogViewer.css"]);

/** A single bare class selector — `.foo-bar`, nothing compound. */
const SINGLE_CLASS_SELECTOR = /^\.[a-z][a-z0-9-]*$/;

/**
 * Extract every comma-separated selector compound from a stylesheet.
 * Shared by the scoping test and the component-owned narrowness check so the
 * two cannot disagree about what counts as a selector.
 */
function selectorCompounds(css: string): string[] {
  const out: string[] = [];
  for (const block of css.split("}")) {
    const idx = block.indexOf("{");
    if (idx === -1) continue;
    const rawSelector = block.slice(0, idx).trim().replace(/\s+/g, " ");
    if (!rawSelector) continue;
    // @-rules are containers, not element selectors — skip them.
    if (rawSelector.startsWith("@")) continue;
    for (const compound of rawSelector.split(",")) {
      const part = compound.trim();
      if (part) out.push(part);
    }
  }
  return out;
}

describe("CSS component-owned exemptions stay narrow", () => {
  it("every COMPONENT_OWNED_EXEMPT file is discovered (no stale entry)", () => {
    // A stale entry would silently exempt nothing while reading as coverage.
    const names = new Set(cssFiles.map((f) => path.basename(f)));
    for (const exempt of COMPONENT_OWNED_EXEMPT) {
      expect(
        names,
        `Stale COMPONENT_OWNED_EXEMPT entry "${exempt}" — no such stylesheet ` +
          `under src/. Remove it or fix the path.`,
      ).toContain(exempt);
    }
  });

  it.each([...COMPONENT_OWNED_EXEMPT])(
    "%s declares exactly one single-class selector",
    (basename) => {
      const file = cssFiles.find((f) => path.basename(f) === basename)!;
      const compounds = selectorCompounds(stripComments(fs.readFileSync(file, "utf8")));

      expect(
        compounds.length,
        `${basename} is exempt from selector scoping on the basis that it owns ` +
          `exactly one class. It declares ${compounds.length} selector(s): ` +
          `${compounds.join(", ")}. Either split the extra rules into a scoped ` +
          `stylesheet or drop the exemption.`,
      ).toBe(1);

      expect(
        SINGLE_CLASS_SELECTOR.test(compounds[0]),
        `${basename}'s selector "${compounds[0]}" is not a single bare class. ` +
          `Compound, descendant, and element selectors can reach markup the ` +
          `component does not own, which is what the scoping rule prevents.`,
      ).toBe(true);
    },
  );
});

describe("CSS selector scoping — every selector scoped to .cs-header* or .page-header*", () => {
  it.each(cssFiles.map((f) => [path.relative(SRC_ROOT, f), f] as [string, string]))(
    "%s — all selectors scoped (shell exemptions named above)",
    (_rel, file) => {
      if (SHELL_SCOPE_EXEMPT.has(path.basename(file))) {
        // Named exemption — scoping rules do not apply to shell stylesheets.
        return;
      }
      if (COMPONENT_OWNED_EXEMPT.has(path.basename(file))) {
        // Named exemption — a component-owned single-class stylesheet. Its
        // narrowness is enforced by the suite above, so this is not a free pass.
        return;
      }

      const css = stripComments(fs.readFileSync(file, "utf8"));

      // Shared with the narrowness check above so the two cannot disagree about
      // what counts as a selector.
      const offenders: string[] = [];
      for (const part of selectorCompounds(css)) {
        // A selector is "scoped" if it contains .cs-header or .page-header
        // anywhere in the compound (including as a combinator ancestor).
        const isScoped =
          part.includes(".cs-header") || part.includes(".page-header");

        if (!isScoped) {
          offenders.push(`"${part}" (in ${path.relative(SRC_ROOT, file)})`);
        }
      }

      expect(
        offenders,
        `Unscoped selector(s) found — a global rule regresses every screen at once.\n` +
          `To add a shell-level rule, add the file to SHELL_SCOPE_EXEMPT with a rationale.\n` +
          `Offenders:\n  ${offenders.join("\n  ")}`,
      ).toEqual([]);
    },
  );
});

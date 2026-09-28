// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * commonsIsolated.test.ts — T2.5 red-phase guard
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T2.5
 *
 * ## What this test asserts
 *
 * No file under `src/components/commons/` may:
 *   - import a `*.fixture.ts` module (commons must be data-free)
 *   - import any API client
 *   - reference `fetch` or `XMLHttpRequest`
 *   - import from `../screens/`
 *
 * Rationale: shared UI-state components must be testable with no data at all.
 * This matters especially here because every screen is fixture-backed (spec § Module
 * layout). A commons component that pulls fixture data breaks isolation and makes
 * it impossible to test commons without also shipping screen-specific fixtures.
 *
 * ## Anti-vacuity
 *
 * The "scanCoverage" suite asserts ≥1 non-test source file exists under commons/.
 * This suite FAILS in the red phase because commons/ does not yet exist.
 * The isolation suite itself passes on an empty tree (no violators), so the
 * anti-vacuity companion is what gives the required red-phase failure.
 *
 * After T3.1 creates commons/ source files, both suites do real work.
 *
 * Modeled on:
 *   ~/guidance-for-dealer-management-system-on-aws/frontend/src/components/commons
 *   /__tests__/commonsNoApiImport.test.ts
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

// ── Path constants ────────────────────────────────────────────────────────────

const COMMONS_ROOT = path.resolve(
  // src/__tests__/ → src/ → src/components/commons/
  __dirname,
  "..",
  "components",
  "commons",
);

const TESTS_DIR = path.resolve(COMMONS_ROOT, "__tests__");

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Recursively collect all `.ts` and `.tsx` files under `dir`, excluding
 * the `__tests__/` subtree (test files are exempt from the isolation constraint).
 */
function collectSourceFiles(dir: string): string[] {
  const results: string[] = [];

  if (!fs.existsSync(dir)) {
    return results;
  }

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      if (fullPath === TESTS_DIR) continue;
      results.push(...collectSourceFiles(fullPath));
    } else if (
      entry.isFile() &&
      (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))
    ) {
      results.push(fullPath);
    }
  }

  return results;
}

/**
 * Patterns whose presence in a commons source file constitutes a violation.
 *
 * Four categories:
 *   1. Fixture imports — any import whose resolved path contains `.fixture`
 *   2. API client imports — any import from a module named `*-client*` or `@/api/`
 *   3. fetch / XHR usage — raw network calls
 *   4. Screen imports — importing from `../screens/` couples commons to concrete screens
 */
const FORBIDDEN_PATTERNS: { pattern: RegExp; reason: string }[] = [
  // 1. Fixture imports
  {
    pattern: /from\s+['"][^'"]*\.fixture[^'"]*['"]/,
    reason: "fixture import",
  },
  {
    pattern: /import\s*\(\s*['"][^'"]*\.fixture[^'"]*['"]/,
    reason: "dynamic fixture import",
  },
  // 2. API client imports
  {
    pattern: /from\s+['"][^'"]*-client[^'"]*['"]/,
    reason: "API client import (*-client*)",
  },
  {
    pattern: /from\s+['"]@\/api\//,
    reason: "API alias import (@/api/)",
  },
  {
    pattern: /import\s*\(\s*['"]@\/api\//,
    reason: "dynamic API alias import",
  },
  // 3. Raw network calls
  {
    pattern: /\bfetch\s*\(/,
    reason: "raw fetch() call",
  },
  {
    pattern: /\bnew\s+XMLHttpRequest\b/,
    reason: "XMLHttpRequest usage",
  },
  // 4. Screen imports
  {
    pattern: /from\s+['"]\.*\.+\/screens\//,
    reason: "import from ../screens/",
  },
  {
    pattern: /import\s*\(\s*['"]\.*\.+\/screens\//,
    reason: "dynamic import from ../screens/",
  },
];

function checkFile(filePath: string): string[] {
  const source = fs.readFileSync(filePath, "utf8");
  const lines = source.split("\n");
  const violations: string[] = [];

  lines.forEach((line, idx) => {
    for (const { pattern, reason } of FORBIDDEN_PATTERNS) {
      if (pattern.test(line)) {
        const relPath = path.relative(COMMONS_ROOT, filePath);
        violations.push(
          `${relPath}:${idx + 1} — ${reason} — matched: ${line.trim()}`,
        );
        break;
      }
    }
  });

  return violations;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe(
  "T2.5 commons isolation — no fixture import, no API client, no fetch/XHR, no ../screens/",
  () => {
    it(
      "no file under commons/ violates the isolation constraints",
      () => {
        const sourceFiles = collectSourceFiles(COMMONS_ROOT);
        const allViolations: string[] = [];

        for (const file of sourceFiles) {
          allViolations.push(...checkFile(file));
        }

        expect(
          allViolations,
          `Commons isolation violated. Forbidden patterns found:\n${allViolations.join("\n")}`,
        ).toHaveLength(0);
      },
    );
  },
);

/**
 * Anti-vacuity suite — asserts the scan ran over a non-empty source tree.
 *
 * FAILS in the red phase because commons/ does not yet exist.
 * After T3.1 creates the commons modules this assertion holds and the
 * isolation suite above does real scanning work.
 */
describe(
  "T2.5 anti-vacuity — commons/ must contain at least one non-test source file",
  () => {
    it(
      "at least one .ts/.tsx source file exists under commons/ (fails red-phase)",
      () => {
        const sourceFiles = collectSourceFiles(COMMONS_ROOT);

        expect(
          sourceFiles.length,
          "Expected ≥1 .ts/.tsx source file under src/components/commons/ " +
            "(excluding __tests__/). " +
            "This assertion fails until T3.1 creates the commons modules. " +
            "That is intentional: a guard that passes on an empty tree is vacuous.",
        ).toBeGreaterThan(0);
      },
    );
  },
);

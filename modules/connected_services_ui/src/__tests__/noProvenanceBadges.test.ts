// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * noProvenanceBadges.test.ts — provenance is not displayed per value.
 *
 * ## Why this guard exists
 *
 * On 2026-09-05 the user asked for the "simulated" markers to be removed from the UI:
 * with a marker beside every value the portal was unreadable. I removed the `<Badge>`
 * from `ProvenanceField` and reported it done.
 *
 * It was not done. The user came back with "still lots of simulated badges on the command
 * center." Nineteen screens were rendering their own hardcoded
 * `<Badge color="blue">simulated</Badge>` — 23 instances — entirely independently of
 * `ProvenanceField`. Fixing the shared component could not remove them, and nothing in
 * the suite noticed, because every guard was aimed at the shared component's contract
 * rather than at what screens actually put on screen.
 *
 * That is the lesson worth pinning: a shared component is not a chokepoint unless
 * something enforces that it is the only path. `provenanceRender` enforces that raw
 * values go through `ProvenanceField`; nothing enforced that provenance *display* goes
 * through it too. This suite closes that gap.
 *
 * ## What it asserts
 *
 * No screen or commons component renders provenance as visible text. Provenance survives
 * in `data-testid` suffixes (`-simulated` / `-live` / `-absent`), which is how guards and
 * tests read it, and in one compact top-bar indicator plus the sign-in notice — neither of
 * which is per-value.
 *
 * ## Deliberately NOT asserted
 *
 * The word "simulated" is not banned outright. It legitimately appears in:
 *   - `provenance: "simulated"` on fixture values (the contract itself)
 *   - `data-testid` suffixes
 *   - comments and docstrings explaining the contract
 *   - the sign-in notice and the top-bar indicator
 * Banning the string would fail on all of those. What is banned is a rendered Badge or
 * visible label whose text is a provenance marker.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { globSync } from "node:fs";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** All screen + commons component sources, excluding tests. */
function componentFiles(): string[] {
  const pattern = resolve(SRC, "components", "**", "*.tsx");
  return globSync(pattern).filter((f) => !f.includes("__tests__"));
}

/** Strip line and block comments so prose about the contract is never matched. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const MARKERS = ["simulated", "live", "absent"];

describe("provenance is never displayed per value", () => {
  const files = componentFiles();

  it("anti-vacuity: found component sources to scan", () => {
    expect(
      files.length,
      "expected to scan a non-empty set of component files"
    ).toBeGreaterThan(20);
  });

  it("no component renders a Badge whose text is a provenance marker", () => {
    const violations: string[] = [];
    for (const file of files) {
      const src = stripComments(readFileSync(file, "utf8"));
      src.split("\n").forEach((line, i) => {
        for (const marker of MARKERS) {
          // <Badge …>simulated</Badge>  (any props, any marker)
          const re = new RegExp(`<Badge[^>]*>\\s*${marker}\\s*<\\/Badge>`, "i");
          if (re.test(line)) {
            violations.push(
              `${file.replace(`${SRC}/`, "")}:${i + 1} — <Badge>${marker}</Badge>`
            );
          }
        }
      });
    }
    expect(
      violations,
      "Provenance must not be rendered as a visible badge. 23 of these were hand-rolled " +
        "across 19 screens and survived a fix to ProvenanceField, because they never went " +
        "through it. The marker lives in the data-testid suffix instead.\n" +
        violations.join("\n")
    ).toHaveLength(0);
  });

  it("ProvenanceField itself imports no Badge", () => {
    const src = readFileSync(
      resolve(SRC, "components", "commons", "ProvenanceField.tsx"),
      "utf8"
    );
    expect(
      /from "@cloudscape-design\/components\/badge"/.test(src),
      "ProvenanceField must not import Badge — provenance is not displayed"
    ).toBe(false);
  });

  it("the shell renders no full-width simulated-data banner", () => {
    const shell = readFileSync(
      resolve(SRC, "components", "layout", "AppShell.tsx"),
      "utf8"
    );
    expect(
      /<SimulatedDataBanner\s*\/?>/.test(stripComments(shell)),
      "The per-screen SimulatedDataBanner was removed; the disclosure lives on the " +
        "sign-in screen plus a compact top-bar indicator"
    ).toBe(false);
  });
});

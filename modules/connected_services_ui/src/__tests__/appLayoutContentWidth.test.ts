// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AppLayout content-width guard.
 *
 * Asserts AppShell passes NO `maxContentWidth` to Cloudscape AppLayout, matching CMS's
 * config, so the content column keeps Cloudscape's default cap and the two `1fr` gap
 * columns absorb the remaining width.
 *
 * ## This file REPLACES appLayoutFullWidth.test.ts, which asserted the opposite
 *
 * That guard pinned `maxContentWidth={Number.MAX_VALUE}` — the documented way to make the
 * main panel span nav-edge to scrollbar — because the Command Center had shipped with a
 * constrained centred column while CMS and DMS looked full-width, and the prop is the
 * explicit way to state that intent across Cloudscape versions.
 *
 * It was reversed on 2026-09-14 on the user's decision ("match CMS ... one code base
 * shared as much as possible"), because full-width content and CMS's page-banner overlap
 * are mutually exclusive, and the overlap is the look we want.
 *
 * AppLayout lays the page out as a grid:
 *
 *   grid-template-columns: min-content
 *                          minmax(var(--awsui-content-gap-left), 1fr)
 *                          minmax(var(--awsui-min-content-width),
 *                                 var(--awsui-default-max-content-width))
 *                          minmax(var(--awsui-content-gap-right), 1fr)
 *                          min-content
 *
 * With the prop unset the content column caps at Cloudscape's default and the `1fr` gap
 * tracks take 150-300px each on a wide monitor. `.cs-header`'s full-bleed background
 * spans them, so the sibling it pulls up into its padding band has visible banner either
 * side of it — that flank is what reads as the overlap. With `Number.MAX_VALUE` the
 * content column takes everything, the gap tracks collapse to their 24px minimum, and
 * the overlap is a ~14px sliver (Cloudscape's `Grid` spends 10px of it on
 * `margin-inline: calc(20px / -2)`) that reads as a slightly shorter banner.
 *
 * So the two guards encode the same care about one prop, pointed opposite ways. Keeping
 * the reversal explicit here — rather than deleting the old file with no trace — is the
 * point: the next person to reach for `Number.MAX_VALUE` to "fix" a narrow column needs
 * to know it silently removes the banner overlap on every screen.
 *
 * ## Deliberate consequence, not a defect
 *
 * Page width now varies by `contentType`, exactly as it does in CMS: `default` and
 * `dashboard` screens get a capped centred column, the `table` screens listed in
 * `resolveContentType()` stay full width (Cloudscape's default max width for those
 * content types is `100%`).
 *
 * Asserted at source level deliberately: the prop's effect is a CSS custom property
 * Cloudscape computes at render, and jsdom does not do layout, so a render-based
 * assertion could not observe the actual width. Source is the honest level for this claim.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const APP_SHELL = resolve(__dirname, "../components/layout/AppShell.tsx");
const source = readFileSync(APP_SHELL, "utf8");

/** Source with block and line comments stripped, so prose never satisfies an assertion. */
const CODE = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("AppShell — corpus sanity", () => {
  it("read a non-trivial AppShell source", () => {
    // A zero-length or wrong-path read would make every assertion below vacuous.
    expect(source.length).toBeGreaterThan(2000);
  });

  it("positive control — the file really does render AppLayout", () => {
    expect(CODE).toContain("<AppLayout");
  });

  it("positive control — comment stripping leaves the props it should", () => {
    // If the stripper were too greedy, the absence assertions below would pass vacuously.
    expect(CODE).toMatch(/navigationWidth=\{\s*280\s*\}/);
    expect(CODE).toMatch(/contentType=\{/);
  });
});

describe("AppShell — AppLayout content width is Cloudscape's default (CMS parity)", () => {
  it("passes no maxContentWidth prop at all", () => {
    expect(
      CODE,
      "maxContentWidth must stay unset. Setting it — to Number.MAX_VALUE or a pixel " +
        "literal — collapses AppLayout's 1fr gap columns to their 24px minimum, which " +
        "removes the flank .cs-header's overlap band needs to be visible in. The banner " +
        "then reads as slightly shorter instead of overlapping, on every screen."
    ).not.toMatch(/maxContentWidth/);
  });

  it("prose may discuss the prop; the props block must not set it", () => {
    // Guards the realistic regression: someone re-adds the prop while the explanatory
    // comment above it still says it is unset.
    const openTag = CODE.indexOf("<AppLayout");
    expect(openTag).toBeGreaterThan(-1);
    const propsBlockEnd = CODE.indexOf("content={", openTag);
    expect(propsBlockEnd).toBeGreaterThan(openTag);
    expect(CODE.slice(openTag, propsBlockEnd)).not.toMatch(/maxContentWidth/);
  });

  it("the rationale is recorded next to the props block", () => {
    // The uncommented source must not set it; the commented source must explain why.
    // Without this, a future formatter that drops comments leaves an unexplained absence.
    expect(source).toMatch(/maxContentWidth`? is DELIBERATELY UNSET/);
  });
});

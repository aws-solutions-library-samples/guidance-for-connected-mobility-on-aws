// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * pageHeaderGaps.test.ts — the banner's gap cancellation must not be removed again.
 *
 * ## Why this guard exists
 *
 * `.cs-header` cancels AppLayout's content gaps so the page banner sits flush against
 * the top bar and the left nav. Without it there is a white strip on both edges.
 *
 * I removed that cancellation on 2026-09-05 while simplifying `PageHeader.css`, on the
 * grounds that its literals (`calc(-1 * 24px)` horizontal, `calc(-1 * 12px)` top) were
 * inferred from a DMS DOM dump on a different Cloudscape version and had never been
 * measured here. The reasoning about provenance was sound; the conclusion was wrong.
 * The user's live Computed rules showed both values were correct:
 *
 *   --awsui-main-gap-esndbs:         var(--space-scaled-s-8ozaad, 12px)
 *   --awsui-content-gap-left-esndbs: var(--space-layout-content-horizontal-buc0zz, 24px)
 *
 * So the removal was a regression that a user had to notice and report. This suite is the
 * cheap mechanical check that would have caught it, and stops the next person — including
 * me — from deleting the rules while tidying.
 *
 * ## What it pins, and what it deliberately does not
 *
 * REVISED 2026-09-14. The horizontal half of this guard was reversed on the user's
 * decision to match CMS's layout. The banner now bleeds to the viewport
 * (`width: 100vw` + the `left: 50%` / `margin-left: -50vw` shift) instead of cancelling
 * the content-gap tracks, because `--awsui-content-gap-left/right` hold each track's
 * MINIMUM, not its used width — `minmax(var(--awsui-content-gap-left), 1fr)` grows to
 * 150-300px once `maxContentWidth` is unset, so cancelling 24px of it leaves exactly the
 * white strip this suite exists to prevent. The vertical cancellation is kept: that gap
 * is a grid ROW whose height genuinely is the property's value.
 *
 * So the suite still pins the *form* of the fix, not pixel values:
 *
 *   - the vertical margin references an applied `--awsui-main-gap-*` custom property, so
 *     it tracks the gap actually in effect rather than restating a number;
 *   - its `var()` carries a `0px` fallback, so "no gap applied" cancels nothing. This is
 *     what makes it correct below the 689px breakpoint, and what makes a Cloudscape hash
 *     change fail toward a visible white seam instead of a mispositioned banner;
 *   - the horizontal edges are asserted as full bleed, with the text inset expressed as
 *     `max(<floor>, calc(50vw - 50%))` rather than CMS's hardcoded 140px/320px.
 *
 * It does NOT assert `24px` or `12px` anywhere. Asserting the resolved value would
 * recreate the original problem: a number in the repo that nobody re-measures, quietly
 * wrong after an upgrade.
 *
 * The overlap band (`padding-bottom: 50px` / `margin-bottom: -25px`) is REQUIRED, at
 * CMS's values. It is load-bearing on `maxContentWidth` staying unset in AppShell.tsx —
 * see appLayoutContentWidth.test.ts, which guards the other half. The `!important`
 * overrides on `.cs-header + [class*="awsui_layout"]` remain BANNED — those did reach
 * into hashed Cloudscape classnames and are still the wrong tool. The companion rule
 * `.cs-header + * { z-index: 950 }` handles the sibling-paints-over problem without
 * touching internals.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const CSS = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "..", "components", "commons", "PageHeader.css"),
  "utf8"
);

/** Strip block comments so prose describing removed rules is never matched as a rule. */
const RULES = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

describe("PageHeader gap cancellation", () => {
  it("anti-vacuity: PageHeader.css is readable and contains a .cs-header rule", () => {
    expect(CSS.length).toBeGreaterThan(0);
    expect(RULES).toMatch(/\.cs-header\s*\{/);
  });

  it.each([["margin-top", "main-gap"]])(
    "%s cancels the applied --awsui-%s custom property",
    (property, gapName) => {
      const re = new RegExp(
        `${property}:\\s*calc\\(\\s*-1\\s*\\*\\s*var\\(\\s*--awsui-${gapName}-[a-z0-9]+\\s*,`
      );
      expect(
        re.test(RULES),
        `${property} must be calc(-1 * var(--awsui-${gapName}-<hash>, 0px)). ` +
          "Removing it reintroduces a white gap between the banner and the top bar " +
          "— a regression a user had to report on 2026-09-05."
      ).toBe(true);
    }
  );

  it("the vertical gap var() carries a 0px fallback, never a pixel literal", () => {
    const varCalls = RULES.match(/var\(\s*--awsui-main-gap-[a-z0-9]+\s*,[^)]*\)/g) ?? [];
    expect(varCalls.length, "expected one main-gap var() call").toBe(1);
    for (const call of varCalls) {
      expect(
        /,\s*0px\s*\)/.test(call),
        `fallback must be 0px so an unapplied gap cancels nothing: ${call}. ` +
          "A pixel fallback would pull the banner out of position below the 689px " +
          "breakpoint, where the gap rule does not apply."
      ).toBe(true);
    }
  });

  it("the horizontal edges bleed by one gap track, derived — not to the viewport", () => {
    // Reversed twice. `--awsui-content-gap-left/right` hold the gap TRACK'S MINIMUM, not
    // its used width, so cancelling them under-reaches and leaves a white strip. But
    // CMS's `width: 100vw` full bleed OVER-reaches here: the content column is centred in
    // the space right of the nav, so a 100vw band's left edge lands at nav/2 — halfway
    // across the nav panel, which it then paints over (band 900 > nav-container 830).
    // Both were observed on the live page. The used gap track is derivable from
    // --awsui-main-offset-left, which AppLayout sets on the layout element.
    expect(
      RULES,
      "margin-left/right must not cancel --awsui-content-gap-*; it under-reaches the used track"
    ).not.toMatch(/margin-(?:left|right|inline[a-z-]*):\s*calc\(\s*-1\s*\*\s*var\(\s*--awsui-content-gap/);

    expect(
      RULES,
      "no `width: 100vw` full bleed — it lands the band's left edge inside the nav panel"
    ).not.toMatch(/width:\s*100vw/);
    expect(
      RULES,
      "no `left: 50%` viewport-centring shift — same reason"
    ).not.toMatch(/left:\s*50%/);

    expect(
      RULES,
      "the bleed must derive the used gap track from --awsui-main-offset-left"
    ).toMatch(/calc\(\s*100vw\s*-\s*var\(\s*--awsui-main-offset-left-[a-z0-9]+\s*,\s*0px\s*\)\s*-\s*100%\s*\)/);

    for (const side of ["left", "right"] as const) {
      expect(
        RULES,
        `margin-${side} must be the negated bleed, floored at 0 so a hash change cannot ` +
          "produce an unbounded negative margin across the nav"
      ).toMatch(new RegExp(`margin-${side}:\\s*calc\\(\\s*-1\\s*\\*\\s*max\\(\\s*0px\\s*,`));
    }
  });

  it("banner text is pulled back to the content column by the same derived value", () => {
    // The bleed distance IS the gap track, so padding it back lands text on the content
    // column's left edge. CMS hardcodes `margin-left: 140px; width: calc(100% - 320px)`,
    // which encodes one nav-open/viewport arithmetic and misaligns when the nav collapses.
    const pads = RULES.match(/padding-(?:left|right):\s*max\(\s*\d+px\s*,\s*var\(\s*--cs-band-bleed\s*\)\s*\)/g) ?? [];
    expect(
      pads.length,
      "expected padding-left and padding-right as max(<floor>, var(--cs-band-bleed)). The " +
        "floor keeps text off the band edge on narrow viewports."
    ).toBe(2);

    expect(
      RULES,
      "no hardcoded CMS nav-arithmetic literals (140px / 320px) — 140 is navigationWidth/2 " +
        "and misaligns the moment the nav collapses"
    ).not.toMatch(/(?:140px|320px)/);
  });

  it("does not reintroduce the inert SpaceBetween margin-cancellation rule", () => {
    // `0db551ab` / `cdadc1ac` added `[data-settle-marker] + * { margin-block-start: 0 }`
    // on the premise that SpaceBetween injects `> *:not(:first-child) { margin-block-start }`.
    // On components@3.0.1354 SpaceBetween is flex + row-gap, so there is no margin to
    // cancel and a display:none marker is not a flex item at all. The rule was inert and
    // its comment asserted a mechanism that does not exist in this version.
    expect(RULES).not.toMatch(/\[data-settle-marker\]/);
  });

  it("overlap band is present (positive padding-bottom + sibling offset + z-index)", () => {
    // The band is the banner's internal dark strip; the overlap is the sibling being
    // pulled up into it. Exact pixel values are tuned by eye and NOT pinned here — the
    // pattern is what matters.
    const paddingMatch = RULES.match(/padding-bottom:\s*(\d+)px/);
    expect(
      paddingMatch,
      "padding-bottom must be present inside .cs-header to create the internal dark band"
    ).not.toBeNull();
    expect(
      Number(paddingMatch![1]),
      "padding-bottom must be a positive pixel value (dark band depth)"
    ).toBeGreaterThan(0);

    expect(
      RULES,
      "z-index: 900 puts the banner below the sibling that paints over it"
    ).toMatch(/z-index:\s*900/);

    const sibling = RULES.match(/\.cs-header\s*\+\s*\.cs-page-body\s*\{([^}]*)\}/);
    expect(sibling, ".cs-header + .cs-page-body rule must exist — it carries the overlap").not.toBeNull();
    expect(
      sibling![1],
      ".cs-header + .cs-page-body needs z-index: 950 so it paints over the band"
    ).toMatch(/z-index:\s*950/);
    expect(
      sibling![1],
      ".cs-header + .cs-page-body needs position: relative — z-index and top both depend on it"
    ).toMatch(/position:\s*relative/);

    const topMatch = sibling![1].match(/top:\s*-(\d+)px/);
    expect(
      topMatch,
      "the overlap is a NEGATIVE top offset on .cs-header + .cs-page-body. Porting CMS's " +
        "`margin-bottom: -25px` on the banner instead measured ~2px of a 25px pull on " +
        "the live page; a relative offset is not subject to margin resolution and cannot " +
        "be partially absorbed. See PageHeader.css."
    ).not.toBeNull();
    expect(
      Number(topMatch![1]),
      "top offset magnitude must be > 0 — this is the overlap depth"
    ).toBeGreaterThan(0);
    expect(
      Number(topMatch![1]),
      "the offset must not exceed the band depth — beyond that the page body clears the " +
        "banner's bottom edge entirely and the tuck reads as a gap. NOTE this bounds the " +
        "OFFSET, not the visible overlap: ~23px of unexplained gap sits below the band, " +
        "so visible overlap = |top| - 23. See PageHeader.css and " +
        "issues/2026-09-14-cs-banner-overlap-23px-unexplained-gap/."
    ).toBeLessThanOrEqual(Number(paddingMatch![1]));

    // The offset is a calibrated constant, not a derived one. If someone later changes
    // padding-bottom assuming the two track each other, they will get a wrong overlap
    // silently. Require the calibration to stay documented next to it.
    expect(
      CSS,
      "the top offset is calibrated against a measured ~23px gap, not derived. That has " +
        "to stay written down, or the next person re-tunes a number whose relationship " +
        "to padding-bottom is not what it looks like."
    ).toMatch(/CALIBRATED, not derived/);
  });

  it("does NOT reintroduce the banner's negative bottom margin", () => {
    // Belt and braces on the mechanism swap: re-adding `margin-bottom: -Npx` to
    // .cs-header would stack an unpredictable partial pull on top of the deterministic
    // `top` offset, giving an overlap nobody chose.
    const band = RULES.match(/\.cs-header\s*\{([^}]*)\}/);
    expect(band, ".cs-header rule must exist").not.toBeNull();
    expect(
      band![1],
      "no margin-bottom on .cs-header — the overlap lives on `.cs-header + .cs-page-body { top }`"
    ).not.toMatch(/margin-bottom/);
  });

  it("AppShell actually renders the .cs-page-body element the overlap rule selects", () => {
    // The failure this exists for: the rule above moved from `.cs-header + *` to a named
    // class, so it now selects nothing at all if AppShell stops emitting that class. A
    // CSS rule pointing at an element nobody renders fails silently and looks exactly
    // like the ~2px symptom that motivated the change — no error, no test failure, no
    // overlap. Assert both halves of the contract in the file that owns the DOM.
    const shell = readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), "..", "components", "layout", "AppShell.tsx"),
      "utf8"
    );
    expect(shell.length, "anti-vacuity: AppShell.tsx must be readable").toBeGreaterThan(2000);

    expect(
      shell,
      'AppShell must render <div className="cs-page-body"> — the overlap rule selects it by name'
    ).toMatch(/className=["']cs-page-body["']/);

    // It must be the banner's immediate next sibling, or the `+` combinator misses. The
    // PageHeader element closes before it, and nothing may sit between them.
    const headerAt = shell.indexOf("<PageHeader");
    const bodyAt = shell.indexOf('className="cs-page-body"');
    expect(headerAt, "AppShell must render PageHeader").toBeGreaterThan(-1);
    expect(bodyAt, "AppShell must render the cs-page-body wrapper").toBeGreaterThan(headerAt);

    const between = shell.slice(
      shell.indexOf("/>", headerAt) + 2,
      // Stop at the START of the wrapper's own tag, not at its className attribute,
      // or the slice contains the `<div` we are looking for and can never be empty.
      shell.lastIndexOf("<", bodyAt)
    );
    expect(
      between.replace(/\{\/\*[\s\S]*?\*\/\}/g, "").replace(/\s/g, ""),
      "nothing may render between PageHeader and .cs-page-body — an element there would " +
        "become the banner's adjacent sibling and the `+` selector would miss the wrapper"
    ).toBe("");

    // And it must WRAP the screen, not sit empty beside it. Checking "does `children`
    // appear somewhere after the wrapper" is not enough — an empty `<div
    // className="cs-page-body"/>` followed by a second div holding the screen satisfies
    // that while rendering nothing inside the wrapper, which is exactly the silent
    // failure this test exists to catch (verified: that mutation passed the naive
    // check). So walk div depth from the wrapper's opening tag to its true close.
    const openEnd = shell.indexOf(">", bodyAt) + 1;
    let depth = 1;
    let i = openEnd;
    while (depth > 0 && i < shell.length) {
      const open = shell.indexOf("<div", i);
      const close = shell.indexOf("</div>", i);
      if (close === -1) break;
      if (open !== -1 && open < close) {
        depth += 1;
        i = open + 4;
      } else {
        depth -= 1;
        i = close + 6;
      }
    }
    expect(depth, "could not find the wrapper's matching </div>").toBe(0);

    const inner = shell.slice(openEnd, i - 6);
    for (const token of ["children", "PlaceholderPanel"] as const) {
      expect(
        inner,
        `.cs-page-body must CONTAIN ${token} — an empty wrapper beside the screen means ` +
          "the overlap rule moves a zero-height element and nothing visibly overlaps"
      ).toContain(token);
    }
  });

  it("does NOT reach into Cloudscape's generated internals for the overlap", () => {
    // These are the overrides that WERE the real problem — reaching into
    // hashed classnames to hide sibling paint. The universal `+ *` sibling
    // selector above replaces them with a hash-independent rule.
    expect(RULES).not.toMatch(/\.cs-header\s*\+\s*\[class\*=/);
    expect(RULES).not.toMatch(/background:\s*transparent\s*!important/);
  });
});

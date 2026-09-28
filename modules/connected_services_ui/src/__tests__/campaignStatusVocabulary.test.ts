/**
 * Guard: a data-collection campaign screen must never claim a campaign is *running*.
 *
 * `RUNNING` is the stored status on an **assignment** row and it means **assigned**.
 * Nothing reconciles it against telemetry — a vehicle can hold a `RUNNING` row and
 * transmit nothing, which is exactly the state
 * `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/` created. A
 * template's stored status is `ACTIVE`, so the pre-restructure screen rendered two
 * vocabularies in one column and invited the reading "Running = transmitting".
 *
 * Structural rather than a render test because the property is about the whole screen
 * family, and because a rule that lives only in a spec or a review comment gets crossed.
 * Same philosophy as `campaignAssignCallers.test.ts`.
 *
 * ## This guard is the net, NOT the owner of the property
 *
 * Read this before adding an assertion here and believing it holds. Review Cycle 2
 * defeated both of this file's load-bearing assertions with the full 1196-test suite
 * green:
 *
 *   - **EV-A** — `` {`Running`} ``. The forbidden-word regex delimits on quotes and
 *     angle brackets; a backtick was in neither character class. Not a contrived form:
 *     the line it replaced *is* a template literal. Backticks are now in both classes.
 *   - **EV-D** — a block-bodied `cell:` that hoists `row.template?.status` into a local
 *     and renders the local. The raw-status assertion is line-scoped, so the hoist walks
 *     past it — and hoisting is the workaround the guarded file's own comments document
 *     as house style.
 *
 * The lesson generalises: the spec's constraint is a **runtime** property ("no row
 * renders a liveness word") and a source scan is a **source** property. A source scan
 * cannot own a runtime claim, because a claim about rendered output can always be spelled
 * a way the scanner does not read.
 *
 * So the owning assertion is a RENDER test:
 * `components/screens/software/__tests__/DataCollectionCampaignsView.test.tsx`
 * § "renders no liveness word anywhere, for any row shape the table can hold" — it reads
 * `document.body.textContent`, so it is indifferent to spelling, and it FAILS on both
 * EV-A and EV-D. This file keeps its family-wide role: it covers every current and future
 * file under `data-model/`, including ones with no render test of their own.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/spec.md` § Constraints.
 */

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve, relative } from "node:path";

const SRC = resolve(__dirname, "..");
const SCAN_DIR = join(SRC, "components", "screens", "data-model");

/**
 * Files pinned by path, not merely counted.
 *
 * A bare "scanned more than zero files" floor cannot distinguish "the rule holds" from
 * "the scanner stopped seeing the file". The predecessor guard
 * (`campaignAssignCallers.test.ts`) was found able to lose a caller silently for exactly
 * that reason, so this one names what it must see.
 *
 * Four new files were added by spec 2026-09-20-cs-campaigns-screen-restructure Group 3
 * (T3.1–T3.4). The per-assignment panel (CampaignVehiclesPanel) is the most tempting
 * place for a liveness word; the shell (CampaignDetailView) passes assignment state down
 * to it; and the two remaining panels (CampaignSignalsPanel, CampaignCoveragePanel)
 * complete the family. All four are pinned so the scanner cannot silently lose any of them.
 *
 * T3.1 Accept 2: `CampaignVehiclesPanel.tsx` and the other three Group-3 files join
 * MUST_SCAN here.
 */
const MUST_SCAN = [
  "components/screens/data-model/DataCollectionCampaignsView.tsx",
  "components/screens/data-model/CampaignDetailView.tsx",
  "components/screens/data-model/CampaignSignalsPanel.tsx",
  "components/screens/data-model/CampaignCoveragePanel.tsx",
  "components/screens/data-model/CampaignVehiclesPanel.tsx",
];

function walk(dir: string, out: string[] = []): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const e of entries) {
    if (e === "__tests__" || e === "node_modules") continue;
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(e)) out.push(p);
  }
  return out;
}

/**
 * Strip comments before scanning.
 *
 * The **block**-comment stripper is load-bearing: this guard's own subject matter is the
 * word "Running", and `DataCollectionCampaignsView.tsx` legitimately discusses it in two
 * JSDoc blocks explaining why it does not render it. Disabling that stripper makes those
 * explanations violations — a guard written in terms of the string it forbids matching its
 * own documentation. Verified: mutation MS5b (disable block stripping) IS caught.
 *
 * The **line**-comment stripper is currently NOT load-bearing — mutation MS5 (disable it)
 * is not caught, because no `//` comment in the scanned files carries a quoted "Running"
 * (0 in line comments). The block count is a SNAPSHOT and will move whenever the file's
 * prose changes: it was 2 when first written, review Cycle 2 measured 3, and Fix Group 1's
 * own edits made it 4. Run the command below rather than trusting any of those numbers.
 * The line stripper is kept regardless, because the next
 * `// never render "Running" here` comment someone adds would otherwise fail this guard
 * for saying the right thing.
 *
 * Stated this precisely because "load-bearing" was claimed without qualification three
 * times in this session's guards and was wrong or partial each time — and then the
 * qualified claim carried a wrong number, and the correction went stale inside one commit.
 * That is the argument for a re-derivation command over a figure:
 * <!-- verify: grep -c '"Running"' modules/connected_services_ui/src/components/screens/data-model/*.tsx -->
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

interface Scanned {
  readonly rel: string;
  readonly code: string;
}

function scanned(): Scanned[] {
  return walk(SCAN_DIR).map((p) => ({
    rel: relative(SRC, p),
    code: stripComments(readFileSync(p, "utf8")),
  }));
}

/**
 * Extract a top-level function declaration by BRACE MATCHING, not by a lazy regex.
 *
 * Fix Group 2 exists because the lazy form was used first:
 * `/function CampaignDefinitionIndicator[\s\S]*?\n}/` terminates at the `}` that closes
 * the DESTRUCTURED PARAMETER LIST, so it captured 68 characters and no JSX at all. Every
 * assertion over that slice passed on every mutation — including the exact pre-fix
 * `{storedVerbatim}` form it was written to forbid. Review cycle 3 dumped the slice
 * mechanically; reading the regex did not reveal it.
 *
 * Parens are matched first (so braces inside the parameter list are skipped), then braces
 * from the body's opening `{`.
 *
 * LIMITATION, stated because the name is generic enough to be reused: this counts
 * characters, it does not tokenise. An unbalanced `}` inside a string literal, a regex
 * literal, or a JSX attribute will truncate the slice early — review cycle 4 probed eight
 * shapes and 3 truncated. That is safe HERE only because the caller pairs it with a floor
 * asserting the slice contains all five branch labels, so a truncated extraction fails
 * loudly. Any new caller must bring its own floor; do not assume this returns a whole body.
 */
function extractTopLevelFunction(code: string, name: string): string {
  const start = code.indexOf(`function ${name}`);
  if (start === -1) return "";
  let i = code.indexOf("(", start);
  if (i === -1) return "";
  let depth = 0;
  for (; i < code.length; i++) {
    if (code[i] === "(") depth++;
    else if (code[i] === ")" && --depth === 0) {
      i++;
      break;
    }
  }
  const bodyStart = code.indexOf("{", i);
  if (bodyStart === -1) return "";
  depth = 0;
  for (let j = bodyStart; j < code.length; j++) {
    if (code[j] === "{") depth++;
    else if (code[j] === "}" && --depth === 0) return code.slice(start, j + 1);
  }
  return "";
}

describe("campaign status vocabulary", () => {
  it("scans the files it must scan (no silent membership loss)", () => {
    const rels = new Set(scanned().map((s) => s.rel));
    const missing = MUST_SCAN.filter((m) => !rels.has(m));
    expect(
      missing,
      "These files are no longer being scanned, so every check below passes " +
        "vacuously for them. If a file was legitimately moved or removed, update " +
        "MUST_SCAN in the same change:\n" + missing.map((m) => `  - ${m}`).join("\n"),
    ).toHaveLength(0);
  });

  it("no data-model screen renders the word 'Running'", () => {
    // `RUNNING` means assigned; rendering it as "Running" on a screen read for liveness is
    // a claim the data cannot support.
    //
    // Backticks are in both character classes. Without them `` {`Running`} `` passed —
    // review Cycle 2's evasion EV-A, with the whole suite green. The render test named in
    // the file docstring is what actually owns this property; this assertion is the net
    // that covers files the render test does not mount.
    const offenders = scanned()
      .filter((s) => /["'`>\s]Running["'`<\s]/.test(s.code))
      .map((s) => s.rel);
    expect(
      offenders,
      "These files render the word 'Running'. A campaign assignment's stored RUNNING " +
        "status means ASSIGNED — nothing reconciles it against telemetry, so a vehicle " +
        "can read Running and transmit nothing:\n" +
        offenders.map((o) => `  - ${o}`).join("\n"),
    ).toHaveLength(0);
  });

  it("no data-model screen maps a status value to a liveness word", () => {
    // Catches the mapping even if the rendered label is spelled differently — e.g.
    // `=== "RUNNING"` returning "Live", "Active now", "Transmitting".
    //
    // `Live` and `Online` were added after Cycle 2 noted that `Live now` was covered while
    // the nearer neighbour `Live` was not. `Active` is deliberately NOT here: `ACTIVE` is
    // the stored TEMPLATE status and the word appears in legitimate prose ("active
    // assignments"), so it would false-positive on the file it guards — the failure mode
    // the comment strippers below exist to avoid.
    const LIVENESS = /(Transmitting|Live|Streaming|Online|Reporting|Currently running)/;
    const offenders = scanned()
      .filter((s) => LIVENESS.test(s.code))
      .map((s) => s.rel);
    expect(
      offenders,
      "These files use a liveness word for campaign state:\n" +
        offenders.map((o) => `  - ${o}`).join("\n"),
    ).toHaveLength(0);
  });

  it("the campaigns view glosses definition state rather than rendering it raw in a cell", () => {
    // A cell that puts the stored value on screen — directly, or via a local hoisted
    // inside a block-bodied `cell:` — would restore the vocabulary the two glossed columns
    // exist to replace.
    //
    // Scope honestly: these are single-line patterns, and review Cycle 2's EV-D walked
    // past them by hoisting across lines. They are kept because they catch the direct
    // forms cheaply, and `row.template?.status` (optional-chained) was uncovered until
    // Cycle 2. The multi-line hoist is owned by the render test, plus the assertion below
    // that the fallback renders a fixed string rather than any interpolation.
    const view = scanned().find((s) => s.rel.endsWith("DataCollectionCampaignsView.tsx"));
    expect(view, "the campaigns view must be scanned").toBeDefined();
    expect(view!.code).not.toMatch(/cell:[^\n]*\brow\.status\b/);
    expect(view!.code).not.toMatch(/cell:[^\n]*row\.template\??\.status/);
  });

  it("the unknown-definition-state fallback renders a fixed string, not the stored value", () => {
    // Cycle 2 Critical 4: the fallback rendered `template.status` verbatim, so a template
    // carrying RUNNING put "RUNNING" on screen — reachable via `PUT /campaigns/{id}`, which
    // takes `status` with no allowlist. A word-scan cannot see it; the string comes from data.
    // So scan for the SHAPE instead: the badge must not interpolate any `.status` expression.
    //
    // ## This guard FOLLOWED the property to a different file (2026-09-20)
    //
    // It used to extract `CampaignDefinitionIndicator` from `DataCollectionCampaignsView.tsx`.
    // User UAT removed that column ("I'm not sure what 'definition' means… we probably don't
    // need that as a column header"), so the indicator was deleted rather than left dead, and
    // the closed-gloss vocabulary now reaches a screen ONLY through `CampaignDetailView`'s
    // `DefinitionStatusBadge`. The guard points there.
    //
    // The alternative — keeping the dead function so this kept passing — is how a guard comes
    // to assert something no operator can see. Same reasoning that removed the `withoutVin`
    // no-op from `CampaignVehiclesPanel` in Group 3 wave 1.
    //
    // The branch SET changed with the move, and the change is not a weakening:
    //   - `missing` is gone. A null template is no longer this component's concern — the
    //     detail view renders a separate "Definition unavailable" alert for it (asserted in
    //     `CampaignDetailView.test.tsx`), and the list marks it inline on the name cell.
    //   - `absent` is new: the detail view renders "—" for a template whose `status` is the
    //     empty string, a case the list column never had to represent.
    const detail = scanned().find((s) => s.rel.endsWith("CampaignDetailView.tsx"));
    expect(
      detail,
      "CampaignDetailView.tsx must be scanned — it is in MUST_SCAN and now owns the " +
        "definition-state vocabulary.",
    ).toBeDefined();
    const badge = extractTopLevelFunction(detail!.code, "DefinitionStatusBadge");

    // Anti-vacuity floor, paired with the assertions below so a broken extraction fails
    // LOUDLY instead of silently voiding them. This is the pairing cycle 3 found missing: an
    // earlier regex captured a parameter list only, and every mutation passed.
    expect(
      badge,
      "DefinitionStatusBadge could not be extracted from CampaignDetailView.tsx. If it was " +
        "renamed or restructured, update this guard in the same change rather than letting it " +
        "pass vacuously — a previous version of this assertion scanned 68 characters of " +
        "parameter list and caught nothing.",
    ).not.toBe("");

    // The extracted slice must contain every branch label. Double duty: pins the vocabulary at
    // source level AND proves the extraction reached the JSX.
    for (const label of ["Available", "Suspended", "Stopped", "Unrecognized state"]) {
      expect(
        badge,
        `The extracted DefinitionStatusBadge body does not contain "${label}". Either a ` +
          "branch was removed or relabelled — these are the permitted glosses, and " +
          "CampaignDetailView.test.tsx asserts them at render level — or the extraction is " +
          "truncated and the assertions below are scanning nothing. Check which first.",
      ).toContain(label);
    }

    // Checked against the BODY only, not the whole slice.
    //
    // `extractTopLevelFunction` returns from `function Name` onward, which includes the
    // parameter list — and this component destructures `({ status })`, which the regex below
    // matches as if it were a JSX interpolation. The predecessor `CampaignDefinitionIndicator`
    // destructured `{ template, campaignName }` and so never tripped it, which is why the
    // false positive only appeared when the guard followed the property to this component.
    // Trimming to the body keeps the assertion aimed at rendered output, where the property is.
    const badgeBody = badge.slice(badge.indexOf("{", badge.indexOf(")")));
    expect(
      badgeBody,
      "The definition badge must not interpolate a status value into JSX. Render a fixed " +
        "gloss; the stored token is diagnostic information, not operator information.",
    ).not.toMatch(/\{\s*[\w.?]*[Ss]tatus\w*\s*\}/);

    // NARROW the allowlist. Cycle 4 Warning 1: per-label `toContain` checks are additive — they
    // prove the known branches survive and say nothing about a SIXTH. Mutation Y8 added a
    // `PENDING` branch glossed "Collecting" and passed 1216 tests, because no fixture rendered
    // `PENDING` and adding a label removes none of the others. Counting the branch testids
    // narrows it.
    //
    // ⚠️ IT DOES NOT CLOSE IT, and four fix groups spent on this block have earned the warning.
    // Y8 has been reopened five times: a branch with no testid (F10.4), a paren-less return
    // (F11.2), a duplicate testid (F13.1), a `null ||`-guarded return that this file SUBTRACTS as
    // non-rendering while it renders, and — the one that ends the argument — a sixth gloss added
    // as a ternary inside an existing branch's JSX, which adds no return, no testid and no
    // element, so every assertion in this block is blind to it. All three remain open. See
    // `issues/2026-09-21-definition-status-vocabulary-guard-cannot-close-y8/`.
    //
    // Do not widen these regexes again. The approach is the problem: "does this return render?"
    // requires evaluating an expression, and the premise these assertions rest on is contingent
    // on the component's present if-chain. The fix is structural — extract the vocabulary to an
    // exported map and reduce the component to one lookup, at which point the closed set is three
    // exact assertions with no regex at all. Tracked in the issue above.
    const definitionTestIds = [
      ...badge.matchAll(/dc-campaign-definition-([a-z]+)/g),
    ].map((m) => m[1]);
    expect(
      new Set(definitionTestIds),
      "DefinitionStatusBadge declares a branch set other than the permitted glosses. A new " +
        "branch needs a render-level fixture in CampaignDetailView.test.tsx in the same " +
        "change, or it ships unasserted — which is how a 'Collecting' gloss passed 1216 " +
        "tests in review cycle 4.",
    ).toEqual(new Set(["available", "suspended", "stopped", "absent", "other"]));

    // EACH TESTID APPEARS ONCE. (W1, Fix Group 12 review — the fourth door into mutation Y8.)
    //
    // The set assertion above collapses duplicates and the count assertion below compared against
    // `definitionTestIds.length`, which does not. So a 6th branch that REUSES an existing testid
    // inflated the return count and the array length in lockstep, satisfying both: verified to
    // pass this file, the render-level `CampaignDetailView.test.tsx`, and the full 81-file suite.
    // The trigger is the most ordinary one available — copy a branch, edit the gloss, forget to
    // change the testid — and the block's own message overclaimed it ("a new branch needs a new
    // testid"), since a duplicate is not a new testid. That message has now overclaimed its body
    // three times in this file's history, which is why this is asserted separately rather than
    // folded into the count: a dedicated check names the duplicated testid instead of reporting
    // an arithmetic mismatch that has to be diagnosed backwards.
    const duplicatedTestIds = [
      ...new Set(definitionTestIds.filter((id, i) => definitionTestIds.indexOf(id) !== i)),
    ];
    expect(
      duplicatedTestIds,
      "DefinitionStatusBadge uses the same data-testid on more than one branch: " +
        `${duplicatedTestIds.join(", ")}. Two branches sharing a testid are indistinguishable ` +
        "to every render-level assertion, and the duplicate makes the branch count below agree " +
        "with the testid count while one gloss ships unasserted. Give each branch its own " +
        "testid AND a render-level fixture in CampaignDetailView.test.tsx.",
    ).toHaveLength(0);

    // COUNT THE BRANCHES, not just the testids.
    //
    // The set comparison above is satisfiable by a branch that carries NO testid: such a branch
    // contributes nothing to `definitionTestIds`, so the set still equals the expected five and
    // the assertion passes. Review cycle 1 of Fix Group 9 verified this — a sixth branch glossed
    // "Collecting" with no testid passed all 1370 tests, reopening Cycle 4's mutation Y8 through
    // a different door. The message above even asserted the opposite ("a new branch needs a new
    // testid"), which was the tell.
    //
    // Every branch in this component returns exactly once **as the component is written today**,
    // so the count of RENDERING returns is the branch count. That premise is CONTINGENT, not
    // structural: it holds for an if-chain of single-return branches and stops holding silently
    // the moment a branch returns conditionally or a gloss is selected inside JSX. A ternary
    // inside an existing branch adds a sixth gloss with no new return at all, and this assertion
    // cannot see it. Requiring the count to equal the DISTINCT testid count makes an untestid'd
    // branch fail — it raises the return count while leaving the testid count alone — but only
    // within the shapes the premise covers. Compared against the distinct count, so this
    // assertion does not depend on the uniqueness check above having run: the two fail
    // independently rather than one masking the other.
    //
    // W2 (Fix Group 10 review) → F11.2 → Suggestion (Fix Group 11 review). F10.4 counted
    // `/\breturn\s*\(/`, which made the premise above FALSE: a paren-less `return <Badge …/>` is
    // a branch that returns once and was not counted, so a paren-less untestid'd branch reopened
    // Cycle 4's mutation Y8 through a third door. F11.2 widened to a bare `\breturn\b`, which
    // over-corrected in the other direction: `if (!status) return null;` is idiomatic, renders
    // nothing, legitimately has no testid, and would have failed with a message prescribing a
    // testid and a fixture — both wrong for the cause.
    //
    // So count rendering returns: every return, minus the ones that explicitly render nothing.
    // Subtracting a KNOWN-NON-RENDERING set rather than matching a known-rendering shape keeps
    // this fail-closed — `return someElement;` is not a shape this component uses, and if it
    // ever appears it counts as a branch and demands a testid rather than slipping past.
    //
    // (An earlier version of this note blamed prettier's `printWidth` for producing the
    // paren-less form. That was false and is corrected here: prettier is not in this module's
    // dependencies, there is no format or lint script, and nothing in the Makefile or CI runs it
    // over this module's TSX. The fix stands; the stated mechanism did not.)
    const allReturns = [...badge.matchAll(/\breturn\b/g)].length;
    // Semicolon optional and `<></>` included, per two Suggestions from the Fix Group 12 review:
    // `return null` (ASI, no semicolon) and `return <></>` both render nothing, and omitting them
    // reintroduced the same cry-wolf false-fail F12.2 had just removed. `\b` after the
    // alternation matters — without it, `return nullish` would be subtracted as non-rendering.
    const nonRenderingReturns = [
      ...badge.matchAll(/\breturn\s+(?:null|undefined)\b\s*;?/g),
      ...badge.matchAll(/\breturn\s*<>\s*<\/>\s*;?/g),
    ].length;
    const returnCount = allReturns - nonRenderingReturns;
    const distinctTestIdCount = new Set(definitionTestIds).size;
    expect(
      returnCount,
      `DefinitionStatusBadge has ${String(returnCount)} rendering branches but ` +
        `${String(distinctTestIdCount)} distinct testid(s). A branch without a testid is ` +
        "invisible to the set assertion above and ships unasserted — add a testid AND a " +
        "render-level fixture in CampaignDetailView.test.tsx, or remove the branch.",
    ).toBe(distinctTestIdCount);
  });
});

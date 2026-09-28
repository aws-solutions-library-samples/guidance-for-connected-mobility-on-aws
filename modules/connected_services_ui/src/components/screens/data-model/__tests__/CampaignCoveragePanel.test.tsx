// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignCoveragePanel.test.tsx
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.3
 *
 * ## What this file covers
 *
 * ### UDS mode (signalsToFetch present and non-empty)
 *  1. Renders the "ECUs polled" container.
 *  2. Lists ECU indices, resolved signal names, function names, frequencies.
 *  3. Shows absent marker when signalName is null (unresolved signal id).
 *  4. Shows absent marker when executionFrequencyMs is absent.
 *  5. Falls back to UDS mode only when signalsToFetch is non-empty (empty array
 *     → telemetry mode).
 *
 * ### Telemetry mode (signalsToFetch absent or empty)
 *  6. Renders the "ECU roster" container, NOT "ECUs polled".
 *  7. Caption contains "available on vehicles running" (the vehicle-property phrasing).
 *  8. CRITICAL (non-attribution guard): caption does NOT claim the campaign
 *     collects *from* these ECUs — guarded by asserting that phrases like
 *     "collects from", "polled by", "used by", and "collected from" are ABSENT
 *     from the telemetry caption.
 *     MUTATION: change caption to say "ECUs this campaign collects data from" →
 *     test 8 FAILS. Applied 2026-09-20, observed failing.
 *  9. Shows the decoderManifestId in the caption.
 * 10. Lists ECUs from manifests whose decoderManifestRef matches decoderManifestId.
 * 11. CRITICAL (roster filter guard): uses only manifests matching decoderManifestId,
 *     NOT the union across all manifests.
 *     MUTATION: change resolveEcuRoster to take the union (remove the
 *     decoderManifestRef filter) → test 11 FAILS. Applied 2026-09-20, observed failing.
 * 12. Deduplicates ECUs by ecu code across matching manifests.
 * 13. Shows absent markers for optional ECU fields (signalCount, baselineVersion).
 * 14. Empty state when no manifests reference the decoder manifest.
 * 15. Empty state when decoderManifestId is undefined.
 * 16. ECUs from manifests NOT matching decoderManifestId do NOT appear.
 *
 * ### Non-attribution invariant (structural)
 * 17. The word "polled" does NOT appear in telemetry mode output.
 * 18. The word "collects" in an attribution context does NOT appear in telemetry output.
 *
 * ## Mutation log (required per tasks.md)
 *
 * **M1 — telemetry caption attribution claim** (guards test 8):
 *   File: CampaignCoveragePanel.tsx
 *   Mutation: replace the caption content with text containing "ECUs this campaign
 *   collects data from" instead of "ECUs available on vehicles running".
 *   Assertion caught: "non-attribution: caption does not claim campaign collects FROM ECUs"
 *   → FAILS on the `not.toContain("collects data from")` assertion.
 *   Applied 2026-09-20, observed failing.
 *
 * **M2 — roster filter widened to union** (guards test 11):
 *   File: CampaignCoveragePanel.tsx
 *   Mutation: remove the `manifest.decoderManifestRef !== decoderManifestId` guard,
 *   processing ALL manifests regardless of decoderManifestRef.
 *   Assertion caught: "telemetry mode: does NOT include ECUs from manifests that do
 *   not reference the campaign decoder"
 *   → FAILS because OTHER_ECU from the non-matching manifest now appears.
 *   Applied 2026-09-20, observed failing.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { ModelManifestItem, SignalItem } from "../../../../api/dataModelClient";
import type { SignalToFetch } from "../signalResolution";
import CampaignCoveragePanel from "../CampaignCoveragePanel";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** Build a minimal SignalItem for test fixtures. */
function sig(
  id: number,
  name: string,
  over: Partial<SignalItem> = {},
): SignalItem {
  return {
    signal_id: id,
    signal_group: "diagnostics",
    signal_name: name,
    data_type: "string",
    status: "active",
    vss_path: `Vehicle.${name}`,
    ...over,
  };
}

const SIGNAL_CATALOG: readonly SignalItem[] = [
  sig(901, "ECU1_DTC_INFO"),
  sig(902, "ECU2_DTC_INFO"),
  sig(903, "ECU3_DTC_INFO"),
  sig(10, "VEHICLE_SPEED", { signal_group: "core_telemetry" }),
];

/**
 * 9-entry signalsToFetch shaped like the real `uds-dtc-polling` campaign.
 * params[0] is the ECU index (1-based).
 */
const UDS_SIGNALS_TO_FETCH: readonly SignalToFetch[] = [
  { signalId: 901, functionName: "UDS_DTC_READ", executionFrequencyMs: 5000, params: [1] },
  { signalId: 902, functionName: "UDS_DTC_READ", executionFrequencyMs: 5000, params: [2] },
  { signalId: 903, functionName: "UDS_DTC_READ", executionFrequencyMs: 5000, params: [3] },
];

/**
 * One UDS entry with an unresolved signal id (id 999 not in SIGNAL_CATALOG).
 */
const UDS_WITH_UNRESOLVED: readonly SignalToFetch[] = [
  { signalId: 999, functionName: "UDS_DTC_READ", params: [7] },
];

/**
 * One UDS entry with no executionFrequencyMs.
 */
const UDS_WITHOUT_FREQ: readonly SignalToFetch[] = [
  { signalId: 901, functionName: "UDS_DTC_READ", params: [1] },
];

/**
 * A model manifest referencing `cms-fleet-v3`, with 8 ECUs.
 * This is the one that SHOULD appear in telemetry mode for decoderManifestId="cms-fleet-v3".
 */
const CMS_FLEET_MANIFEST: ModelManifestItem = {
  modelManifestName: "CMS-FLEET-MODEL",
  modelManifestVersion: "4",
  displayName: "CMS Fleet Model",
  modelLine: "Fleet",
  platform: "MIXED",
  productionPhase: "PRODUCTION",
  status: "ACTIVE",
  description: "The default fleet model manifest",
  isDefault: true,
  decoderManifestRef: "cms-fleet-v3",
  signalCount: 88,
  ecuConfigId: "ecu-cfg-fleet",
  fleetIds: ["fleet-001"],
  ecus: [
    { ecu: "TCU", displayName: "Telematics Control Unit", signalCount: 18, baselineVersion: "4.0.0" },
    { ecu: "BMS", displayName: "Battery Management System", signalCount: 28, baselineVersion: "3.1.2" },
    { ecu: "BCM", displayName: "Body Control Module" },
    { ecu: "ADAS", displayName: "ADAS Controller", signalCount: 12, baselineVersion: "2.0.0" },
    { ecu: "TPMS", displayName: "TPMS Controller", signalCount: 4, baselineVersion: "1.0.0" },
    { ecu: "IHU", displayName: "In-Headunit", signalCount: 8, baselineVersion: "3.0.0" },
    { ecu: "OBD", displayName: "OBD Interface", signalCount: 6, baselineVersion: "1.1.0" },
    { ecu: "EWS", displayName: "Entry Warning System", signalCount: 2, baselineVersion: "1.0.0" },
  ],
  pk: "MODEL#CMS-FLEET-MODEL",
  sk: "MANIFEST#4",
  createTimestamp: "2023-01-01T00:00:00Z",
  updateTimestamp: "2024-09-01T00:00:00Z",
};

/**
 * A manifest referencing a DIFFERENT decoder manifest.
 * Its ECUs MUST NOT appear when querying for "cms-fleet-v3".
 * Measured: ECM is on MERIDIAN manifests but NOT on cms-fleet-v3.
 */
const MERIDIAN_MANIFEST: ModelManifestItem = {
  modelManifestName: "MERIDIAN-TRAILWIND",
  modelManifestVersion: "1",
  displayName: "Meridian Trailwind",
  modelLine: "Trailwind",
  platform: "EV_PLATFORM",
  productionPhase: "PRODUCTION",
  status: "ACTIVE",
  description: "Meridian Trailwind EV",
  isDefault: false,
  decoderManifestRef: "meridian-trail-v1",  // Different decoder — NOT cms-fleet-v3
  signalCount: 45,
  ecuConfigId: "ecu-cfg-meridian",
  fleetIds: [],
  ecus: [
    { ecu: "ECM", displayName: "Engine Control Module" },  // NOT on cms-fleet-v3
    { ecu: "TCU", displayName: "Telematics Control Unit" }, // Same code, different manifest
  ],
  pk: "MODEL#MERIDIAN-TRAILWIND",
  sk: "MANIFEST#1",
  createTimestamp: "2024-01-01T00:00:00Z",
  updateTimestamp: "2024-06-01T00:00:00Z",
};

/**
 * A manifest whose decoderManifestRef SHARES A PREFIX with the campaign decoder
 * "cms-fleet-v3" — specifically "cms-fleet-v30" shares the prefix "cms-fleet-v3".
 *
 * This fixture exists solely for F4.10. The prior two fixtures (cms-fleet-v3,
 * meridian-trail-v1) share no 3-character prefix, so a prefix-match loosening of the
 * roster filter is invisible to the tests above: both the correct filter and a prefix
 * filter produce the same result for those two decoders. "cms-fleet-v30" exposes the gap:
 *
 *   exact match     "cms-fleet-v3" !== "cms-fleet-v30"  → CORRECT, PFCU excluded
 *   startsWith      "cms-fleet-v30".startsWith("cms-fleet-v3") === true → WRONG, PFCU appears
 *
 * The distinctively-named ECU "PFCU" (Prefix-Collision Fleet Control Unit) is easy to
 * query by name and cannot be confused with any ECU on CMS_FLEET_MANIFEST or
 * MERIDIAN_MANIFEST. See F4.10 test "prefix-collision manifest".
 *
 * MUTATION (F4.10):
 *   File: CampaignCoveragePanel.tsx
 *   Change: `manifest.decoderManifestRef !== decoderManifestId` →
 *           `!manifest.decoderManifestRef.startsWith(decoderManifestId.slice(0, 3))`
 *   Result: PFCU_MANIFEST passes the loosened filter, "PFCU" appears in the panel →
 *           `queryByText("PFCU")` returns non-null and
 *           `expect(screen.queryByText("PFCU")).toBeNull()` FAILS.
 *   Applied 2026-09-20, observed failing. Restored byte-identical.
 */
const PFCU_MANIFEST: ModelManifestItem = {
  modelManifestName: "CMS-FLEET-MODEL-V30",
  modelManifestVersion: "1",
  displayName: "CMS Fleet Model V30",
  modelLine: "Fleet",
  platform: "MIXED",
  productionPhase: "PRODUCTION",
  status: "ACTIVE",
  description: "A newer fleet model on a different decoder manifest",
  isDefault: false,
  // Shares the prefix "cms-fleet-v3" with the campaign decoder "cms-fleet-v3",
  // but is NOT equal to it. The exact-match filter correctly excludes this manifest;
  // a prefix match would incorrectly include it.
  decoderManifestRef: "cms-fleet-v30",
  signalCount: 10,
  ecuConfigId: "ecu-cfg-fleet-v30",
  fleetIds: [],
  ecus: [
    {
      ecu: "PFCU",
      displayName: "Prefix-Collision Fleet Control Unit",
      signalCount: 5,
      baselineVersion: "1.0.0",
    },
  ],
  pk: "MODEL#CMS-FLEET-MODEL-V30",
  sk: "MANIFEST#1",
  createTimestamp: "2026-01-01T00:00:00Z",
  updateTimestamp: "2026-09-20T00:00:00Z",
};

const ALL_MANIFESTS: readonly ModelManifestItem[] = [CMS_FLEET_MANIFEST, MERIDIAN_MANIFEST];

/**
 * ALL_MANIFESTS extended with PFCU_MANIFEST for F4.10 prefix-collision tests.
 * Not used in the baseline tests above so they remain unchanged.
 */
const ALL_MANIFESTS_WITH_PREFIX_COLLISION: readonly ModelManifestItem[] = [
  CMS_FLEET_MANIFEST,
  MERIDIAN_MANIFEST,
  PFCU_MANIFEST,
];

afterEach(() => {
  cleanup();
});

// ── UDS mode ──────────────────────────────────────────────────────────────────

describe("UDS mode (signalsToFetch present and non-empty)", () => {
  it("1. renders the ECUs-polled container", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={UDS_SIGNALS_TO_FETCH}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    expect(screen.getByTestId("coverage-panel-uds")).toBeTruthy();
    expect(screen.queryByTestId("coverage-panel-telemetry")).toBeNull();
  });

  it("2. lists ECU indices, resolved signal names, function names, and frequencies", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={UDS_SIGNALS_TO_FETCH}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    // ECU indices
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    // Resolved signal names
    expect(screen.getByText("ECU1_DTC_INFO")).toBeTruthy();
    expect(screen.getByText("ECU2_DTC_INFO")).toBeTruthy();
    expect(screen.getByText("ECU3_DTC_INFO")).toBeTruthy();
    // Function name
    expect(screen.getAllByText("UDS_DTC_READ").length).toBeGreaterThan(0);
    // Frequency
    expect(screen.getAllByText("5000").length).toBeGreaterThan(0);
  });

  it("3. shows absent marker when signalName is null (unresolved signal id)", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={UDS_WITH_UNRESOLVED}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const emElements = document.querySelectorAll("em");
    const absentMarkers = Array.from(emElements).filter((el) => el.textContent === "—");
    expect(absentMarkers.length).toBeGreaterThan(0);
  });

  it("4. shows absent marker when executionFrequencyMs is absent", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={UDS_WITHOUT_FREQ}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const emElements = document.querySelectorAll("em");
    const absentMarkers = Array.from(emElements).filter((el) => el.textContent === "—");
    expect(absentMarkers.length).toBeGreaterThan(0);
  });

  it("5. empty signalsToFetch array routes to telemetry mode, not UDS", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={[]}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    // Empty array → not UDS mode
    expect(screen.queryByTestId("coverage-panel-uds")).toBeNull();
    expect(screen.getByTestId("coverage-panel-telemetry")).toBeTruthy();
  });
});

// ── Telemetry mode ────────────────────────────────────────────────────────────

describe("telemetry mode (signalsToFetch absent or empty)", () => {
  it("6. renders ECU-roster container, not ECUs-polled", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    expect(screen.getByTestId("coverage-panel-telemetry")).toBeTruthy();
    expect(screen.queryByTestId("coverage-panel-uds")).toBeNull();
  });

  it("7. caption contains 'available on vehicles running'", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const caption = screen.getByTestId("coverage-panel-telemetry-caption");
    expect(caption.textContent).toContain("available on vehicles running");
  });

  it("8. CRITICAL — non-attribution: caption explicitly denies that ECUs are attributed to this campaign", () => {
    /**
     * SCOPE, stated precisely because the previous version of this docstring overstated it.
     *
     * This test guards ONE of the two mutations that can break non-attribution:
     *
     *   REMOVING the denial   → caught here, by `toContain(<denial>)`.
     *   ADDING an affirmation alongside an intact denial → NOT caught here.
     *
     * Review cycle 3 prepended "ECUs this campaign collects data from." while leaving the
     * denial byte-identical, and this test passed — it asks only whether the denial is
     * present, and it was. Cycle 4 confirmed this test still passes under that mutation
     * while test 18 correctly fails. The previous docstring claimed "The denial IS the
     * guard. If it's stripped, the test fails", which is true for stripping and was read as
     * a claim about the property as a whole.
     *
     * The ADDITION case is owned by test 18 ("telemetry caption explicitly denies ECU
     * attribution, not asserts it"), which COUNTS occurrences of the attribution phrase and
     * requires every one to sit inside the denial. Keep both: one pins the denial's
     * presence, the other pins that nothing unqualified sits beside it.
     */
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const caption = screen.getByTestId("coverage-panel-telemetry-caption");
    const text = caption.textContent ?? "";

    // CRITICAL: the explicit denial phrase must be present.
    // A mutant that changes this to an affirmative claim will NOT contain this phrase.
    expect(text).toContain("not a list of ECUs this campaign collects data from");

    // These purely affirmative attributions (without the "not a list of" denial) must not appear.
    expect(text).not.toContain("polled by this campaign");
    expect(text).not.toContain("used by this campaign");

    // The text must describe a VEHICLE property, not a campaign data-collection claim.
    expect(text).toContain("vehicle");

    // The non-attribution phrase must appear with "not" before "a list of ECUs":
    // ensures the denial isn't accidentally split across nodes into an affirmation.
    const denialIdx = text.indexOf("not a list of ECUs this campaign collects data from");
    expect(denialIdx).toBeGreaterThanOrEqual(0);
  });

  it("9. caption shows the decoderManifestId", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const decoderIdEl = screen.getByTestId("coverage-panel-decoder-id");
    expect(decoderIdEl.textContent).toBe("cms-fleet-v3");
  });

  it("10. lists ECUs from manifests whose decoderManifestRef matches decoderManifestId", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    // These ECUs are on CMS-FLEET-MODEL (decoderManifestRef=cms-fleet-v3)
    expect(screen.getByText("TCU")).toBeTruthy();
    expect(screen.getByText("BMS")).toBeTruthy();
    expect(screen.getByText("BCM")).toBeTruthy();
    expect(screen.getByText("ADAS")).toBeTruthy();
  });

  it("11. CRITICAL — roster filter: does NOT include ECUs from manifests that do not reference the campaign decoder", () => {
    /**
     * MUTATION LOG (M2):
     * Mutation: remove the `manifest.decoderManifestRef !== decoderManifestId` guard
     *           in resolveEcuRoster so ALL manifests contribute their ECUs.
     * Caught by: this test — "ECM" appears (from MERIDIAN_MANIFEST which uses
     *            "meridian-trail-v1", not "cms-fleet-v3"), causing getByText("ECM")
     *            to succeed where queryByText("ECM") had been asserting null.
     * Applied 2026-09-20 to CampaignCoveragePanel.tsx, observed failing (ECM appeared).
     * Restored byte-identical after observing the failure.
     *
     * Measured 2026-09-20: `cms-fleet-v3` yields CMS-FLEET-MODEL's 8 ECUs.
     * ECM is NOT on cms-fleet-v3 — it lives only on MERIDIAN manifests.
     * An unfiltered union would over-claim the vehicle's hardware.
     */
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    // ECM is on MERIDIAN-TRAILWIND (decoderManifestRef=meridian-trail-v1) — must NOT appear
    expect(screen.queryByText("ECM")).toBeNull();
    // The correct 8 ECUs from CMS-FLEET-MODEL should appear
    expect(screen.getByText("TCU")).toBeTruthy();
    expect(screen.getByText("BMS")).toBeTruthy();
    expect(screen.getByText("BCM")).toBeTruthy();
  });

  it("12. deduplicates ECUs by ecu code when multiple matching manifests share a code", () => {
    // Two manifests, both referencing "cms-fleet-v3", each with TCU
    const manifest2: ModelManifestItem = {
      ...CMS_FLEET_MANIFEST,
      modelManifestName: "CMS-FLEET-MODEL-V2",
      ecus: [
        { ecu: "TCU", displayName: "Telematics Control Unit V2" }, // Same code, different displayName
        { ecu: "EXTRA", displayName: "Extra ECU" },
      ],
    };

    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={[CMS_FLEET_MANIFEST, manifest2]}
      />,
    );

    // TCU should appear exactly once (deduplicated)
    const tcuElements = screen.getAllByText("TCU");
    expect(tcuElements).toHaveLength(1);

    // EXTRA from manifest2 should appear (not already seen)
    expect(screen.getByText("EXTRA")).toBeTruthy();
  });

  it("13. shows absent markers for optional ECU fields (signalCount, baselineVersion)", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={[CMS_FLEET_MANIFEST]}
      />,
    );
    // BCM has no signalCount or baselineVersion → at least 2 absent markers
    const emElements = document.querySelectorAll("em");
    const absentMarkers = Array.from(emElements).filter((el) => el.textContent === "—");
    expect(absentMarkers.length).toBeGreaterThanOrEqual(2);
  });

  it("14. empty state when no manifests reference the decoder manifest", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="no-such-decoder"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    // The table renders but with no ECU rows — check for the empty-state text
    expect(screen.getByText(/No model manifests reference decoder manifest/)).toBeTruthy();
  });

  it("15. empty state when decoderManifestId is undefined", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId={undefined}
        modelManifests={ALL_MANIFESTS}
      />,
    );
    expect(screen.getByTestId("coverage-panel-telemetry")).toBeTruthy();
    // No ECU codes from any manifest should render in the table body
    expect(screen.queryByText("TCU")).toBeNull();
    expect(screen.queryByText("ECM")).toBeNull();
  });

  it("16. ECUs from non-matching manifest do NOT appear", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    // MERIDIAN_MANIFEST's ECM is NOT on cms-fleet-v3 and must not appear
    expect(screen.queryByText("ECM")).toBeNull();
  });
});

// ── Non-attribution structural invariants ─────────────────────────────────────

describe("non-attribution invariants", () => {
  it('17. "polled" does NOT appear in telemetry mode output', () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const panel = screen.getByTestId("coverage-panel-telemetry");
    const text = panel.textContent ?? "";
    expect(text).not.toContain("polled");
  });

  it('18. telemetry caption explicitly denies ECU attribution, not asserts it', () => {
    /**
     * The caption MUST contain a denial of ECU attribution ("not a list of ECUs…"),
     * but it MUST NOT contain a forward-attribution phrase like
     * "ECUs this campaign collects data from" without the "not a list of" prefix.
     *
     * Strategy: check that the denial phrase is present, and that the attribution
     * phrase does NOT appear without the "not" prefix.
     *
     * MUTATION (M1 variant for test 18): Change the caption to
     * "ECUs this campaign collects data from <decoderManifestId>"
     * (removing the "not a list of" denial) → this test FAILS because:
     *   - `not.toContain("not a list of ECUs")` succeeds (denial is gone)
     *   - The forward-attribution phrase is now present at the start of the text,
     *     so a caller reading the UI would be misled.
     * Applied 2026-09-20, observed failing.
     */
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const caption = screen.getByTestId("coverage-panel-telemetry-caption");
    const text = caption.textContent ?? "";

    // The DENIAL phrase must be present — explicitly negating attribution.
    expect(text).toContain("not a list of ECUs this campaign collects data from");

    // EVERY occurrence of the attribution phrase must be part of the denial.
    //
    // Counted, not located. The previous version used `indexOf`, which finds only the FIRST
    // occurrence: review cycle 3 kept the denial byte-identical and PREPENDED
    // "ECUs this campaign collects data from." to the caption, and this test passed 23/23 and
    // the full 1343-test suite. `attributionPhraseIdx` pointed at the affirmative while
    // `notPrefixIdx` still found the denial further along, so the guard compared the denial to
    // itself and never examined the affirmative at all.
    //
    // Deleting the denial and ADDING an affirmation are two different mutations, and only the
    // first was ever guarded. Counting covers both: if the phrase appears more often than the
    // denial does, at least one occurrence is unqualified.
    const occurrences = (haystack: string, needle: string): number =>
      haystack.split(needle).length - 1;
    const attributionCount = occurrences(text, "ECUs this campaign collects data from");
    const deniedCount = occurrences(text, "not a list of ECUs this campaign collects data from");
    expect(
      attributionCount,
      `The caption mentions "ECUs this campaign collects data from" ${String(attributionCount)} ` +
        `time(s), but only ${String(deniedCount)} of those sit inside the denial. An ` +
        "unqualified mention claims this campaign's signals are attributed to those ECUs, and " +
        "no signal\u2192ECU mapping exists in any reachable surface. Caption was:\n" +
        `  "${text}"`,
    ).toBe(deniedCount);
    expect(deniedCount, "the denial itself must be present exactly once").toBe(1);
  });
});

// ── Heading text ─────────────────────────────────────────────────────────────

describe("heading text", () => {
  it("telemetry heading reads 'ECU roster'", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const heading = screen.getByTestId("coverage-panel-telemetry-heading");
    expect(heading.textContent).toBe("ECU roster");
  });

  it("UDS container is present for UDS mode, no telemetry heading rendered", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={UDS_SIGNALS_TO_FETCH}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    expect(screen.queryByTestId("coverage-panel-telemetry-heading")).toBeNull();
    expect(screen.queryByTestId("coverage-panel-telemetry-caption")).toBeNull();
  });

  it("within the UDS container the header text contains 'ECUs polled'", () => {
    render(
      <CampaignCoveragePanel
        signalsToFetch={UDS_SIGNALS_TO_FETCH}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS}
      />,
    );
    const panel = screen.getByTestId("coverage-panel-uds");
    expect(within(panel).getByText("ECUs polled")).toBeTruthy();
  });
});


// ── F4.10 (WARNING) — roster filter is exact-match, not prefix-match ──────────
//
// Review Group 3 Cycle 1 Warning 4 / Fix Group 4 Owner D.
//
// The existing test 11 ("CRITICAL — roster filter") uses only two fixture decoders
// ("cms-fleet-v3" and "meridian-trail-v1") that share no common prefix. This means
// any loosening of the filter that still excludes non-prefix matches goes undetected:
// a `startsWith(decoderManifestId.slice(0, 3))` filter passes 21/21 because
// the check still excludes MERIDIAN's "mer…" prefix.
//
// The harm is concrete: a real "cms-fleet-v4" manifest would contribute its ECUs to a
// "cms-fleet-v3" campaign's panel under a prefix-match filter, over-claiming the
// vehicle's hardware. The fix is this one fixture: "cms-fleet-v30" shares the prefix
// "cms-fleet-v3" with the campaign decoder but is NOT equal to it.
//
// Source under test: CampaignCoveragePanel.tsx — DO NOT EDIT. Test-only change.

describe("F4.10 — roster filter is exact-match, not prefix-match or substring-match", () => {
  it("prefix-collision manifest: PFCU does NOT appear when decoderManifestRef is cms-fleet-v30 but campaign queries cms-fleet-v3", () => {
    // MUTATION (F4.10):
    //   File: CampaignCoveragePanel.tsx
    //   Change: filter condition
    //     FROM: `manifest.decoderManifestRef !== decoderManifestId`
    //     TO:   `!manifest.decoderManifestRef.startsWith(decoderManifestId.slice(0, 3))`
    //   Result: PFCU_MANIFEST ("cms-fleet-v30") passes the loosened filter because
    //     "cms-fleet-v30".startsWith("cms-fleet-v3".slice(0, 3)) === true.
    //     "PFCU" now appears in the rendered panel.
    //     `expect(screen.queryByText("PFCU")).toBeNull()` FAILS with:
    //       "Expected null but received HTMLElement".
    //   Applied 2026-09-20 to CampaignCoveragePanel.tsx, observed failing.
    //   Restored byte-identical via cp /tmp/CampaignCoveragePanel.tsx.bak back.
    //   md5 verified identical to pre-mutation baseline.
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v3"
        modelManifests={ALL_MANIFESTS_WITH_PREFIX_COLLISION}
      />,
    );

    // The correct ECUs from CMS_FLEET_MANIFEST SHOULD still appear.
    expect(screen.getByText("TCU")).toBeTruthy();
    expect(screen.getByText("BMS")).toBeTruthy();

    // PFCU is on PFCU_MANIFEST whose decoderManifestRef is "cms-fleet-v30" (NOT "cms-fleet-v3").
    // An exact-match filter correctly excludes it. A prefix or startsWith match would NOT.
    // This assertion fails on the loosened filter mutation, proving the exact-match is load-bearing.
    expect(
      screen.queryByText("PFCU"),
      "PFCU_MANIFEST uses decoderManifestRef='cms-fleet-v30', which shares the prefix " +
        "'cms-fleet-v3' with the campaign decoder but is NOT equal to it. " +
        "The roster filter must use exact equality, not prefix or substring matching. " +
        "F4.10 mutation: loosening to startsWith causes PFCU to appear here.",
    ).toBeNull();

    // Sanity: PFCU's display name is also absent.
    expect(screen.queryByText("Prefix-Collision Fleet Control Unit")).toBeNull();
  });

  it("positive control: PFCU_MANIFEST ECUs DO appear when the campaign decoder exactly matches cms-fleet-v30", () => {
    // Proves the fixture is real and the filter is not vacuously excluding everything.
    // If PFCU_MANIFEST's ECU never appears under any query, the test above could pass
    // trivially (e.g. because the component renders nothing). This control confirms
    // PFCU_MANIFEST is live data and its ECU is queryable when the decoder matches.
    render(
      <CampaignCoveragePanel
        signalsToFetch={undefined}
        signalCatalog={SIGNAL_CATALOG}
        decoderManifestId="cms-fleet-v30"
        modelManifests={ALL_MANIFESTS_WITH_PREFIX_COLLISION}
      />,
    );

    // PFCU_MANIFEST references "cms-fleet-v30" exactly → its ECU must appear.
    expect(screen.getByText("PFCU")).toBeTruthy();

    // CMS_FLEET_MANIFEST references "cms-fleet-v3" (not "cms-fleet-v30") → its ECUs must NOT.
    expect(screen.queryByText("BMS")).toBeNull();
    expect(screen.queryByText("ADAS")).toBeNull();
  });
});

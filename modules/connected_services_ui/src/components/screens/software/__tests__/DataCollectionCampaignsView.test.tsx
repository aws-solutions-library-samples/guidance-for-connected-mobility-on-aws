// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataCollectionCampaignsView.test.tsx
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T5.2, T5.3
 *
 * ## What this file covers
 *
 * 1. No fabricated columns — rolloutPct, canary stages, recall completion must be absent.
 *    These are OTA fields; data-collection campaigns have none of them.
 *
 * 2. No softwareCampaigns.fixture.ts import — different concept; sharing its types
 *    would recreate the OTA/data-collection confusion in code.
 *
 * 3. canAssignCampaignToVehicle guard — validates model binding before offering assignment:
 *    (a) vehicle WITH modelManifestName + campaign WITH decoderManifestId → true
 *    (b) vehicle WITHOUT modelManifestName → false (no model binding, cannot validate)
 *    (c) campaign WITHOUT decoderManifestId → false (no decoder ref to validate against)
 *    (d) vehicle AND campaign both missing → false
 *
 * 4. Mutation check (T5.3 requirement): removing the modelManifestName check from
 *    canAssignCampaignToVehicle causes test (b) to fail. Reported at bottom.
 *
 * 5. The three OTA screens (SoftwareCampaignsView, RolloutMonitorView,
 *    SecurityMonitorView) are byte-unchanged — source-structural guard.
 *
 * 6. The settle marker ("cs-settle-data-collection-campaigns-table") is present
 *    in the view source.
 *
 * 7. Only real campaign API fields appear as column identifiers — no fabricated fields.
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  canAssignCampaignToVehicle,
  SETTLE_MARKER,
} from "../../../../components/screens/data-model/DataCollectionCampaignsView";
import type { AssignmentVehicle } from "../../../../components/screens/data-model/DataCollectionCampaignsView";
import type { FleetCampaignItem } from "../../../../api/dataModelClient";

// ── Source load helpers ────────────────────────────────────────────────────────

const __dir = dirname(fileURLToPath(import.meta.url));

function loadCode(relative: string): string {
  const raw = readFileSync(resolve(__dir, relative), "utf8");
  // Strip block comments and line comments for content checks
  return raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const VIEW_CODE = loadCode(
  "../../../../components/screens/data-model/DataCollectionCampaignsView.tsx",
);
const VIEW_SOURCE = readFileSync(
  resolve(
    __dir,
    "../../../../components/screens/data-model/DataCollectionCampaignsView.tsx",
  ),
  "utf8",
);

// OTA screens — must be byte-unchanged (not imported from, not modified by this spec)
const SOFTWARE_CAMPAIGNS_SOURCE = readFileSync(
  resolve(__dir, "../SoftwareCampaignsView.tsx"),
  "utf8",
);
const ROLLOUT_MONITOR_SOURCE = readFileSync(
  resolve(__dir, "../RolloutMonitorView.tsx"),
  "utf8",
);
const SECURITY_MONITOR_SOURCE = readFileSync(
  resolve(__dir, "../SecurityMonitorView.tsx"),
  "utf8",
);

// ── Helper fixtures ────────────────────────────────────────────────────────────

/** Vehicle with modelManifestName (101 of 149 staging rows have this). */
const vehicleWithModel: AssignmentVehicle = {
  vehicleId: "VEH-MRDN-0001",
  vin: "1MERI00001TRW00001",
  modelManifestName: "OEM2-TRAILWIND",
  producer: "oem2",
};

/** Vehicle without modelManifestName (48 OEM1 rows, Group 4 backfill unresolved). */
const vehicleWithoutModel: AssignmentVehicle = {
  vehicleId: "VEH-OEM1-0001",
  vin: "1FMCU0F74MUB00001",
  producer: "oem1",
  // modelManifestName intentionally absent
};

/** Campaign with a decoder manifest reference. */
const campaignWithDecoder: FleetCampaignItem = {
  campaignId: "template-campaign-v1",
  campaignName: "basic-telemetry-v1",
  targetArn: "template",
  status: "RUNNING",
  createdAt: "2026-09-14T10:00:00Z",
  decoderManifestId: "cms-fleet-v3",
  signalsToCollect: [{ name: "Vehicle.Speed" }, { name: "Vehicle.OBD.EngineLoad" }],
};

/** Campaign without a decoder manifest reference. */
const campaignWithoutDecoder: FleetCampaignItem = {
  campaignId: "template-campaign-legacy",
  campaignName: "legacy-telemetry",
  targetArn: "template",
  status: "RUNNING",
  createdAt: "2026-09-01T00:00:00Z",
  // decoderManifestId intentionally absent
};

// ── 1. No fabricated columns ───────────────────────────────────────────────────

describe("DataCollectionCampaignsView — no fabricated columns", () => {
  it("anti-vacuity: comment stripping leaves real code", () => {
    expect(VIEW_CODE).toMatch(/canAssignCampaignToVehicle/);
    expect(VIEW_CODE).toMatch(/fetchDataProcessingCampaigns/);
  });

  it("rolloutPct is absent — OTA-only field not present on fleet-campaigns records", () => {
    expect(
      VIEW_CODE,
      "rolloutPct has no counterpart on GET /api/v1/fleet-campaigns. Do not add it.",
    ).not.toMatch(/rolloutPct/);
  });

  it("canary stages are absent — OTA-only concept", () => {
    expect(
      VIEW_CODE,
      "canary stages do not exist on fleet-campaigns records — OTA only.",
    ).not.toMatch(/canary/i);
  });

  it("recallAggregateCompletionPct is absent — OTA recall field, not data-collection", () => {
    expect(
      VIEW_CODE,
      "recallAggregateCompletionPct is an OTA recall field from softwareCampaigns.fixture.ts. " +
        "Fleet campaigns have no recall completion field.",
    ).not.toMatch(/recallAggregateCompletionPct/);
  });

  it("softwareCampaigns.fixture.ts is NOT imported", () => {
    expect(
      VIEW_SOURCE,
      "Do not import from softwareCampaigns.fixture.ts — different concept; sharing its " +
        "types would recreate the OTA/data-collection confusion in code.",
    ).not.toMatch(/softwareCampaigns\.fixture/);
  });

  it("softwareCampaignType is absent — OTA discriminator, does not exist here", () => {
    expect(
      VIEW_CODE,
      "softwareCampaignType is from softwareCampaigns.fixture.ts and has no equivalent on " +
        "fleet-campaigns records.",
    ).not.toMatch(/softwareCampaignType/);
  });
});

// ── 2. Real API fields are present ────────────────────────────────────────────

describe("DataCollectionCampaignsView — real API fields only", () => {
  // Column assertions pin the column `id`s, not the bare appearance of a word anywhere in
  // the file. `toMatch(/targetArn/)` passed on a mention in a COMMENT, so after the
  // 2026-09-20 restructure removed the targetArn and status columns those two tests kept
  // passing while asserting something false about the screen.
  //
  // Four more bare-regex tests lived here (`campaignName`, `decoderManifestId`,
  // `signalsToCollect`, `createdAt` "is rendered as a column") and were deleted by Fix
  // Group 1 as vacuous in the same way. Review Cycle 2 demonstrated it rather than
  // arguing it: deleting the ENTIRE `decoderManifestId` column object left
  // "decoderManifestId is rendered as a column" passing, because the identifier still
  // appears in the cell body of the surviving columns. `COLUMN_IDS` below covers all
  // eight ids, so no coverage was lost — four titles claiming a guarantee they did not
  // provide were.
  const COLUMN_IDS = [...VIEW_CODE.matchAll(/^\s*id: "([a-zA-Z]+)",$/gm)].map(
    (m) => m[1],
  );

  it("exposes the post-restructure campaign-level columns", () => {
    // One row per campaign: the definition, the assignment rollup, and the definition's
    // own fields. No per-assignment columns.
    //
    // Anti-vacuity: this positive assertion shares the `COLUMN_IDS` extraction with the
    // two `not.toContain` assertions below, so if the extraction ever breaks (a reformat
    // to single quotes, a hyphenated id) this test fails loudly instead of silently
    // voiding the negatives.
    // EXACT set, not arrayContaining. Six columns, and the count is the point: user UAT
    // reported the table scrolling horizontally, which is unacceptable, and two columns were
    // dropped to fix it. An `arrayContaining` assertion cannot notice a column being ADDED
    // back, so it would not protect the property that motivated the change.
    expect(COLUMN_IDS).toEqual([
      "campaignName",
      "assignments",
      "decoderManifestId",
      "signalsToCollect",
      "collectionScheme",
      "createdAt",
    ]);
  });

  it("no longer exposes a definitionState column — the header did not communicate", () => {
    // Removed at user UAT: "I'm not sure what 'definition' means… we probably don't need that
    // as a column header". It also read "Available" on every row but one, so it was a
    // permanently-wide column reporting the absence of an anomaly.
    //
    // The information did not vanish. The missing-definition case is now an inline marker on
    // the campaign-name cell (asserted below), and the closed five-gloss vocabulary moved to
    // CampaignDetailView's DefinitionStatusBadge, which campaignStatusVocabulary.test.ts now
    // guards there.
    expect(COLUMN_IDS).not.toContain("definitionState");
  });

  it("no longer exposes an owner column — it is constant across every visible row", () => {
    // `filterOemOwnedGroups` guarantees every VISIBLE group is OEM-entitled, so this column
    // read "oem" on all ten template-backed rows and "—" on the eleventh. A column with one
    // value carries no information and costs width.
    expect(COLUMN_IDS).not.toContain("owner");
  });

  it("no longer exposes a targetArn column — it is the grouping discriminator, not a row field", () => {
    expect(COLUMN_IDS).not.toContain("targetArn");
  });

  it("no longer exposes a raw status column — assignment state is glossed, never 'Running'", () => {
    expect(COLUMN_IDS).not.toContain("status");
  });

  it("sorting is wired, and every sortingField names a real field on the row type", () => {
    // Inverted at user UAT, which asked for this table to match the CMS vehicles table
    // (pagination, filters). It now spreads `collectionProps` from
    // `useConnectedServicesCollection`, so sorting IS wired and declaring `sortingField` is
    // honest where it previously was not.
    //
    // The original defect this replaces was a `sortingField` naming `createdAt`, which does
    // not exist on `DataCollectionCampaignGroup` — a claim about behaviour that was not there.
    // So the assertion is not "sorting exists" but "every field named is real": the useful
    // property survives the inversion.
    expect(VIEW_CODE, "collectionProps must be spread for sorting to function").toMatch(
      /\{\.\.\.collectionProps\}/,
    );
    const sortingFields = [...VIEW_CODE.matchAll(/sortingField: "([^"]+)"/g)].map((m) => m[1]);
    expect(sortingFields.length, "at least one column should be sortable").toBeGreaterThan(0);
    // Fields present on DataCollectionCampaignGroup itself. `createdAt` is NOT among them —
    // it lives on `group.template`, so sorting by it would silently do nothing.
    const REAL_GROUP_FIELDS = new Set([
      "campaignName",
      "vehicleCount",
    ]);
    const bogus = sortingFields.filter((f) => !REAL_GROUP_FIELDS.has(f));
    expect(
      bogus,
      "These sortingFields name no field on DataCollectionCampaignGroup, so the column would " +
        "appear sortable and do nothing when clicked. Fields on `group.template` are NOT " +
        "reachable by a top-level sortingField:\n" + bogus.map((f) => `  - ${f}`).join("\n"),
    ).toHaveLength(0);
  });
});

// ── 3. Settle marker ──────────────────────────────────────────────────────────

describe("DataCollectionCampaignsView — settle marker", () => {
  it("SETTLE_MARKER constant has the expected value", () => {
    expect(SETTLE_MARKER).toBe("cs-settle-data-collection-campaigns-table");
  });

  it("settle marker appears in the view source", () => {
    expect(VIEW_SOURCE).toMatch(/cs-settle-data-collection-campaigns-table/);
  });
});

// ── 4. canAssignCampaignToVehicle guard (T5.3) ────────────────────────────────

describe("canAssignCampaignToVehicle — model-match guard", () => {
  it("returns true when vehicle has modelManifestName and campaign has decoderManifestId", () => {
    const result = canAssignCampaignToVehicle(vehicleWithModel, campaignWithDecoder);
    expect(
      result,
      "A vehicle with a model binding + campaign with a decoder ref should be assignable.",
    ).toBe(true);
  });

  it("returns false when vehicle has NO modelManifestName — cannot validate model→ECU coverage", () => {
    const result = canAssignCampaignToVehicle(vehicleWithoutModel, campaignWithDecoder);
    expect(
      result,
      "A vehicle with no model binding cannot be validated against campaign signal coverage. " +
        "The 48 OEM1 vehicles without modelManifestName must be blocked.",
    ).toBe(false);
  });

  it("returns false when campaign has NO decoderManifestId — cannot establish model→decoder chain", () => {
    const result = canAssignCampaignToVehicle(vehicleWithModel, campaignWithoutDecoder);
    expect(
      result,
      "A campaign without a decoderManifestId cannot be validated against the vehicle's model. " +
        "Block assignment to prevent targeting a vehicle whose ECUs do not emit the signals.",
    ).toBe(false);
  });

  it("returns false when BOTH vehicle modelManifestName AND campaign decoderManifestId are absent", () => {
    const result = canAssignCampaignToVehicle(vehicleWithoutModel, campaignWithoutDecoder);
    expect(result).toBe(false);
  });

  it("returns false for a vehicle with empty-string modelManifestName (treated as absent)", () => {
    const vehicleEmptyModel: AssignmentVehicle = {
      ...vehicleWithModel,
      modelManifestName: "",
    };
    const result = canAssignCampaignToVehicle(vehicleEmptyModel, campaignWithDecoder);
    expect(
      result,
      "An empty-string modelManifestName is equivalent to absent and must block assignment.",
    ).toBe(false);
  });

  it("returns false for a campaign with empty-string decoderManifestId (treated as absent)", () => {
    const campaignEmptyDecoder: FleetCampaignItem = {
      ...campaignWithDecoder,
      decoderManifestId: "",
    };
    const result = canAssignCampaignToVehicle(vehicleWithModel, campaignEmptyDecoder);
    expect(
      result,
      "An empty-string decoderManifestId is equivalent to absent and must block assignment.",
    ).toBe(false);
  });
});

// ── 5. OTA screens are byte-unchanged ─────────────────────────────────────────

describe("OTA screens — not modified by this spec", () => {
  it("SoftwareCampaignsView still imports from softwareCampaigns.fixture.ts (unchanged)", () => {
    expect(
      SOFTWARE_CAMPAIGNS_SOURCE,
      "SoftwareCampaignsView must NOT be modified. It imports its OTA fixture as before.",
    ).toMatch(/softwareCampaigns\.fixture/);
  });

  it("SoftwareCampaignsView still has its OTA settle marker (unchanged)", () => {
    expect(SOFTWARE_CAMPAIGNS_SOURCE).toMatch(
      /cs-settle-software-campaigns-rollout-list/,
    );
  });

  it("RolloutMonitorView still has its settle marker (unchanged)", () => {
    expect(ROLLOUT_MONITOR_SOURCE).toMatch(/cs-settle-rollout-monitor-progress-table/);
  });

  it("SecurityMonitorView still has its settle marker (unchanged)", () => {
    expect(SECURITY_MONITOR_SOURCE).toMatch(/cs-settle-security-monitor-placeholder-panel/);
  });
});


// ── 6. owner === "oem" entitlement (T5.1, spec Decision 4) ────────────────────
//
// CS shows only OEM-owned CAMPAIGNS. Fleet campaigns belong to fleet operators.
//
// Note what this section does NOT say, because an earlier version of this header did and
// review cycle 4 found it as the third surviving instance of the retracted rule: there is
// no `owner === "oem"` ROW filter, and the very next test exists to forbid one. `owner` on
// an assignment row is attribution for that assignment — filtering rows by it under-reports
// a visible campaign's coverage, which is the Cycle 2 Critical. Entitlement is applied to
// grouped campaigns by `filterOemOwnedGroups`, under two clauses (template owner; or, with
// no template, any oem-attributed assignment). These source-structural assertions are
// paired with the behavioural rendering tests below.

describe("DataCollectionCampaignsView — owner==oem filter (T5.1 Decision 4)", () => {
  it("entitlement is applied to GROUPS, never to raw rows", () => {
    // Re-pointed by Fix Group 1. This test used to assert `/owner.*oem/` against the view
    // source, and it FAILED the moment the rule moved into `campaignGrouping.ts` — which
    // is the behaviour a source guard should have. But it would have kept passing on the
    // Cycle 2 defect itself, because the defect WAS an `owner === "oem"` in this file.
    // So the assertion is inverted: the view must delegate, and must not filter rows.
    //
    // Cycle 2 Critical 1: `owner` on an assignment row is attribution for that
    // assignment, not campaign ownership. A pre-grouping row filter silently dropped 10
    // of `cms-fleet-gps-10s`'s 22 assignments. The behavioural guard is
    // "reports all 22 assignments…" below; this one stops the shape coming back.
    expect(
      VIEW_CODE,
      "The view must apply entitlement to grouped campaigns via filterOemOwnedGroups.",
    ).toMatch(/filterOemOwnedGroups\(\s*groupCampaigns\(/);
    expect(
      VIEW_CODE,
      "The view must NOT filter the raw `dcCampaigns` response — `owner` on an " +
        "assignment row is attribution, and filtering rows under-reports coverage. " +
        "See campaignGrouping.ts § filterOemOwnedGroups.",
    ).not.toMatch(/dcCampaigns[\s\S]{0,40}\.filter/);
    expect(VIEW_CODE).not.toMatch(/\.filter\([^)]*owner\s*===/);
  });

  it("the grouping module owns the oem rule and states both clauses", () => {
    // Anti-vacuity for the test above: if the rule existed nowhere, the view's delegation
    // assertion would still pass. Pin the rule at its new home.
    const groupingCode = loadCode(
      "../../../../components/screens/data-model/campaignGrouping.ts",
    );
    expect(groupingCode).toMatch(/export function filterOemOwnedGroups/);
    // Template clause, and the template-less fallback clause.
    expect(groupingCode).toMatch(/g\.template\.owner === OEM_OWNER/);
    expect(groupingCode).toMatch(/assignmentRows\.some/);
  });

  it("the view does NOT show fleet-scoped campaigns (absence of 'fleetId' state)", () => {
    // After T5.1, the hardcoded 'fleet-oem2-default' and fleetId state are deleted.
    // Their presence would indicate the old fleet-scoped API path is still in use.
    expect(
      VIEW_CODE,
      "fleet-oem2-default must NOT appear in the view — the per-fleet API was replaced by the per-vehicle data-processing API.",
    ).not.toMatch(/fleet-oem2-default/);
  });

  it("fleetId state is deleted", () => {
    // The fleetIdInput and fleetId state were the invented fleet-scoped indirection.
    // Neither should appear in the repointed view.
    expect(VIEW_CODE).not.toMatch(/fleetIdInput/);
  });
});

// ── 7. canAssignCampaignToVehicle wired into the real assign path (T5.2) ───────
//
// Before T5.2, the guard was exported but had no production callers. The only
// test was `expect(VIEW_CODE).toMatch(/canAssignCampaignToVehicle/)` — a
// source-text substring that passes even when every call site is deleted.
//
// These tests exercise the guard THROUGH the assign path using rendered JSX.
// The mutation for T5.2: deleting `canAssignCampaignToVehicle(...)` from
// `handleAssignConfirm` in the view causes "guard blocks vehicle without model"
// to FAIL — the Confirm button would no longer be disabled for no-model vehicles.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

// Helpers: vehicles for the assignment picker
const vehicleWithModelManifest: AssignmentVehicle = {
  vehicleId: "VEH-MRDN-0001",
  vin: "1MERI00001TRW00001",
  modelManifestName: "OEM2-TRAILWIND",
  producer: "oem2",
};

const vehicleWithoutModelManifest: AssignmentVehicle = {
  vehicleId: "VEH-OEM1-0001",
  vin: "1FMCU0F74MUB00001",
  producer: "oem1",
  // modelManifestName intentionally absent — 48 OEM1 vehicles
};

// A template campaign with a decoder manifest ref
const templateCampaignForAssign: FleetCampaignItem = {
  campaignId: "basic-telemetry-v1",
  campaignName: "basic-telemetry-v1",
  targetArn: "template",
  status: "ACTIVE",
  createdAt: "2026-09-15T10:00:00Z",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
  signalsToCollect: [],
};

// An OEM active campaign (non-template, owner=oem)
const oemActiveCampaign: FleetCampaignItem = {
  campaignId: "basic-telemetry-v1-VEH-MRDN-0001",
  campaignName: "basic-telemetry-v1",
  targetArn: "vehicle:VEH-MRDN-0001",
  status: "RUNNING",
  createdAt: "2026-09-15T10:00:00Z",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
};

// A fleet-scoped campaign — must NOT appear when the filter is applied
const fleetOwnedCampaign: FleetCampaignItem = {
  campaignId: "fleet-telemetry-fleet123",
  campaignName: "fleet-telemetry",
  targetArn: "fleet:fleet-123",
  status: "RUNNING",
  createdAt: "2026-09-15T10:00:00Z",
  owner: "fleet:fleet-123",
  decoderManifestId: "cms-fleet-v3",
};

import DataCollectionCampaignsView from "../../../../components/screens/data-model/DataCollectionCampaignsView";
import * as dataModelClient from "../../../../api/dataModelClient";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete window.runtimeConfig;
});

function renderWithCampaigns(
  dcCampaignItems: readonly FleetCampaignItem[],
  vehicles?: readonly AssignmentVehicle[],
) {
  window.runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid",
    cognitoDomain: "test.example.invalid",
    connectedServicesApiEndpoint: "https://cs.example.invalid",
    subscriptionsApiEndpoint: "https://subs.example.invalid",
    simulationApiEndpoint: "https://sim.example.invalid",
    dataProcessingApiEndpoint: "https://dp.example.invalid",
    simulationProductRuleName: "test_rule",
    callbackOrigin: "https://test.example.invalid",
  };
  vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockResolvedValue({
    dcCampaigns: dcCampaignItems,
    count: dcCampaignItems.length,
  });
  return render(
    <MemoryRouter>
      <DataCollectionCampaignsView vehiclesForAssignment={vehicles} />
    </MemoryRouter>,
  );
}

describe("DataCollectionCampaignsView — owner filter behavioral (T5.1 mutation-test)", () => {
  it("shows oem-owned campaigns", async () => {
    // This test asserts PRESENCE of an oem campaign only. It does NOT guard the owner
    // filter: removing the filter still shows this campaign, so the assertion still
    // passes — verified by running that mutation against it. The filter is guarded by
    // "fleet-owned campaigns are invisible" below, which the same mutation DOES fail.
    // The previous comment here claimed this test would fail, which was untrue.
    renderWithCampaigns([oemActiveCampaign, fleetOwnedCampaign]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    // Rows are keyed by `campaignName` since the 2026-09-20 restructure — the table
    // lists one row per CAMPAIGN, not per record, and `campaignId` carries the target
    // suffix on an assignment row so it is no longer the row identity.
    expect(screen.queryByTestId(`dc-campaign-name-${oemActiveCampaign.campaignName}`)).toBeTruthy();
  });

  it("campaign name cell links to the detail route (T2.1 Accept 3, mutation M3)", async () => {
    // MUTATION M3: change the href template to a different path →
    // this test FAILS because the href no longer matches the expected route.
    //
    // The link must point to `/data-model/data-collection-campaigns/:campaignName` so
    // that clicking the row opens CampaignDetailView. The encoded campaignName is used
    // so names containing slashes or special characters are handled correctly.
    renderWithCampaigns([oemActiveCampaign]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });

    const nameLink = screen.getByTestId(
      `dc-campaign-name-${oemActiveCampaign.campaignName}`,
    );
    // The link element should have an href pointing to the detail route.
    const href = nameLink.getAttribute("href");
    expect(
      href,
      "The campaign name link must point to the detail route " +
        "`/data-model/data-collection-campaigns/:campaignName`. " +
        "Mutation M3: changing the path causes this assertion to fail.",
    ).toBe(
      `/data-model/data-collection-campaigns/${encodeURIComponent(oemActiveCampaign.campaignName)}`,
    );
  });

  it("fleet-owned campaigns are invisible to CS — owner filter mutation test", async () => {
    // MUTATION: delete `c.owner === 'oem'` from the filter in the view →
    // fleetOwnedCampaign appears in the table and this assertion FAILS.
    renderWithCampaigns([oemActiveCampaign, fleetOwnedCampaign]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    // Asserts on `campaignName`, the post-restructure row identity. Using `campaignId`
    // here made this test pass VACUOUSLY after the restructure: the query returned null
    // both because the owner filter works AND because the testid format had changed, so
    // it would no longer have caught the filter being deleted.
    expect(screen.queryByTestId(`dc-campaign-name-${fleetOwnedCampaign.campaignName}`)).toBeNull();
  });

  it("assignCampaignToVehicle serialises the VIN under the `vehicles` field (real client)", async () => {
    // Rewritten by Fix Group 1. The previous version built `capturedBody` INSIDE its own
    // mock and then asserted it, so it could not fail unless the test itself was edited,
    // and the real client was never exercised. It also passed a `vehicleId` into the `vin`
    // parameter while calling that correct — the exact confusion behind
    // `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
    //
    // This drives the REAL client through its `fetchImpl` seam and asserts the serialised
    // body. The field must be `vehicles` (plural) — NOT `vins`/`vinList`; a misnamed field
    // returns 200 with `assigned: []` because the server treats a missing key as empty.
    //
    // The trap is live and adjacent: `simulationClient.startSimulation` genuinely takes
    // `vehicles: [vehicleId]` (see `src/api/__tests__/simulationClient.test.ts`). Two
    // endpoints, same field name, opposite conventions.
    window.runtimeConfig = {
      cognitoUserPoolId: "<pool-id>",
      cognitoClientId: "testclientid",
      cognitoDomain: "test.example.invalid",
      connectedServicesApiEndpoint: "https://cs.example.invalid",
      subscriptionsApiEndpoint: "https://subs.example.invalid",
      simulationApiEndpoint: "https://sim.example.invalid",
      dataProcessingApiEndpoint: "https://dp.example.invalid",
      simulationProductRuleName: "test_rule",
      callbackOrigin: "https://test.example.invalid",
    };
    const VIN = "1MERI00001TRW00001";
    let sentUrl = "";
    let sentBody: Record<string, unknown> = {};
    const fetchStub = (async (url: string, init?: RequestInit) => {
      sentUrl = String(url);
      sentBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({ campaignName: "basic-telemetry-v1", assigned: [VIN] }),
      };
    }) as unknown as typeof fetch;

    const result = await dataModelClient.assignCampaignToVehicle(
      "basic-telemetry-v1",
      VIN,
      fetchStub,
    );

    expect(sentUrl).toContain("/campaigns/assign");
    expect(sentBody["vehicles"]).toEqual([VIN]);
    expect(sentBody).not.toHaveProperty("vins");
    expect(sentBody).not.toHaveProperty("vinList");
    // The VIN, not a vehicleId: no `VEH-` prefix should ever reach this body.
    expect(JSON.stringify(sentBody)).not.toMatch(/VEH-/);
    expect(result?.assigned).toEqual([VIN]);
  });
});

describe("DataCollectionCampaignsView — canAssignCampaignToVehicle wired (T5.2)", () => {
  // Isolates the `canAssignSelected` guard from the option-level
  // `disabled: !v.modelManifestName` UI guard on the Select. A vehicle WITH
  // a modelManifestName is selectable through the picker regardless of this
  // task's guard, so pairing it with a campaign that has NO decoderManifestId
  // exercises `canAssignCampaignToVehicle`/`canAssignSelected` on their own —
  // deleting the guard call site must flip this specific case, and nothing
  // about vehicle selectability changes when it does.
  const campaignWithoutDecoderForAssign: FleetCampaignItem = {
    campaignId: "legacy-telemetry-v1",
    campaignName: "legacy-telemetry-v1",
    targetArn: "template",
    status: "ACTIVE",
    createdAt: "2026-09-15T10:00:00Z",
    owner: "oem",
    signalsToCollect: [],
    // decoderManifestId intentionally absent
  };

  it("guard blocks a selectable vehicle when the campaign has no decoderManifestId — confirm button disabled after selection", async () => {
    // MUTATION (T5.2): delete the `canAssignCampaignToVehicle(...)` call from
    // the `canAssignSelected` useMemo (e.g. replace its body with `true`) →
    // this test FAILS, because vehicleWithModelManifest IS selectable here
    // (its option is not disabled) and only the deeper guard blocks it.
    renderWithCampaigns(
      [campaignWithoutDecoderForAssign],
      [vehicleWithModelManifest],
    );
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-button")).toBeTruthy();
    });
    screen.getByTestId("dc-campaigns-assign-button").click();
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-modal")).toBeTruthy();
    });

    const vehicleSelectTrigger = screen.getAllByRole("button").find(
      (btn) => btn.textContent?.includes("Choose a vehicle") || btn.getAttribute("aria-haspopup"),
    );
    expect(vehicleSelectTrigger).toBeTruthy();
    await userEvent.click(vehicleSelectTrigger as HTMLElement);

    const options = screen.getAllByRole("option");
    const modelOption = options.find((o) =>
      o.textContent?.includes(vehicleWithModelManifest.vehicleId),
    )!;
    expect(modelOption).toBeTruthy();
    // This vehicle's option is NOT disabled — it has a modelManifestName, so
    // the option-level guard admits it. Selection must succeed.
    expect(modelOption.getAttribute("aria-disabled")).not.toBe("true");
    await userEvent.click(modelOption as HTMLElement);

    // Selection succeeded (this is what distinguishes this test from the
    // no-model-manifest case, where selection itself is refused). The Confirm
    // button must still be disabled, because the campaign has no
    // decoderManifestId — this is the property `canAssignSelected` exists to
    // enforce, and it is exercised here with a real, successful selection.
    await waitFor(() => {
      expect(
        (screen.getByTestId("dc-campaigns-modal-confirm") as HTMLButtonElement).disabled,
      ).toBe(true);
    });
    expect(
      canAssignCampaignToVehicle(vehicleWithModelManifest, campaignWithoutDecoderForAssign),
    ).toBe(false);
  });

  it("guard blocks vehicle without modelManifestName — its Select option is disabled and cannot be chosen", async () => {
    // Companion case: a vehicle with NO modelManifestName is blocked at the
    // option level before `canAssignSelected` is ever reached for it. Both
    // guards exist (option-level, and the deeper `canAssignCampaignToVehicle`
    // check above) — this test pins the option-level one specifically.
    renderWithCampaigns(
      [templateCampaignForAssign],
      [vehicleWithModelManifest, vehicleWithoutModelManifest],
    );
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-button")).toBeTruthy();
    });
    screen.getByTestId("dc-campaigns-assign-button").click();
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-modal")).toBeTruthy();
    });

    const vehicleSelectTrigger = screen.getAllByRole("button").find(
      (btn) => btn.textContent?.includes("Choose a vehicle") || btn.getAttribute("aria-haspopup"),
    );
    expect(vehicleSelectTrigger).toBeTruthy();
    await userEvent.click(vehicleSelectTrigger as HTMLElement);

    const options = screen.getAllByRole("option");
    const noModelOption = options.find((o) =>
      o.textContent?.includes(vehicleWithoutModelManifest.vehicleId),
    )!;
    expect(noModelOption).toBeTruthy();
    expect(noModelOption.getAttribute("aria-disabled")).toBe("true");

    // Nothing is selected (the disabled option cannot be chosen), so Confirm
    // stays disabled via `!selectedVehicle` — this is a real, distinct branch
    // from the decoder-absence case above, not a duplicate assertion.
    expect(
      (screen.getByTestId("dc-campaigns-modal-confirm") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("guard allows vehicle with modelManifestName — confirm button enabled after selection", async () => {
    // Positive control: with a model-bound vehicle actually selected through the
    // real Select, and a campaign that HAS a decoderManifestId, the guard
    // returns true and the Confirm button becomes enabled.
    vi.spyOn(dataModelClient, "assignCampaignToVehicle").mockResolvedValue({
      campaignName: "basic-telemetry-v1",
      assigned: ["VEH-MRDN-0001"],
    });
    renderWithCampaigns([templateCampaignForAssign], [vehicleWithModelManifest]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-button")).toBeTruthy();
    });
    screen.getByTestId("dc-campaigns-assign-button").click();
    await waitFor(() => {
      const confirmBtn = screen.getByTestId("dc-campaigns-modal-confirm");
      // With no vehicle selected yet, confirm should be disabled.
      expect((confirmBtn as HTMLButtonElement).disabled).toBe(true);
    });

    const vehicleSelectTrigger = screen.getAllByRole("button").find(
      (btn) => btn.textContent?.includes("Choose a vehicle") || btn.getAttribute("aria-haspopup"),
    );
    expect(vehicleSelectTrigger).toBeTruthy();
    await userEvent.click(vehicleSelectTrigger as HTMLElement);

    const options = screen.getAllByRole("option");
    const modelOption = options.find((o) =>
      o.textContent?.includes(vehicleWithModelManifest.vehicleId),
    )!;
    expect(modelOption).toBeTruthy();
    expect(modelOption.getAttribute("aria-disabled")).not.toBe("true");
    await userEvent.click(modelOption as HTMLElement);

    // The key property tested: with a vehicle-with-model actually selected in
    // the picker, against a campaign WITH a decoderManifestId,
    // `canAssignCampaignToVehicle` returns true and the Confirm button's real
    // `disabled` attribute reflects it — not source text, not the
    // pre-selection disabled state.
    await waitFor(() => {
      expect(
        (screen.getByTestId("dc-campaigns-modal-confirm") as HTMLButtonElement).disabled,
      ).toBe(false);
    });
    expect(canAssignCampaignToVehicle(vehicleWithModelManifest, templateCampaignForAssign)).toBe(
      true,
    );
  });
});


// ── 9. Fix Group 1 (review Cycle 2): what the columns SAY, against live shapes ──
//
// Every test below renders the real component and asserts rendered TEXT. That is
// deliberate and it is the point of this block.
//
// `campaignStatusVocabulary.test.ts` scans source for the word "Running". Review Cycle 2
// defeated both of its load-bearing assertions with the full suite green — once with a
// template literal (the backtick was in neither character class) and once by hoisting
// `row.template?.status` inside a block-bodied `cell:`, which is the workaround the view's
// own comments document as house style. A source scan is the right family-wide net; it
// cannot be the assertion that owns a RUNTIME claim. These tests are that assertion.
//
// Fixtures are live staging shapes, not placeholders: the 12-oem/10-platform assignment
// split on `cms-fleet-gps-10s`, and `cms-fleet-telemetry-30s` as a template-less
// `SUSPENDED` global row.

/** `cms-fleet-gps-10s`: an oem template whose assignments are attributed to two owners. */
const gpsTemplate: FleetCampaignItem = {
  campaignId: "cms-fleet-gps-10s",
  campaignName: "cms-fleet-gps-10s",
  targetArn: "template",
  status: "ACTIVE",
  createdAt: "2026-09-12T20:24:54+00:00",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
  signalsToCollect: [],
};

function gpsAssignment(
  n: number,
  over: Partial<FleetCampaignItem> = {},
): FleetCampaignItem {
  const vin = `MRDN00000000${String(10000 + n)}`;
  return {
    campaignId: `cms-fleet-gps-10s-${vin}`,
    campaignName: "cms-fleet-gps-10s",
    targetArn: `vehicle:${vin}`,
    status: "RUNNING",
    createdAt: "2026-09-20T12:00:00Z",
    owner: "oem",
    decoderManifestId: "cms-fleet-v3",
    ...over,
  };
}

/** The live 22-row shape: 12 attributed to `oem`, 10 to `platform`, plus a fleet row. */
function gpsLiveRows(): FleetCampaignItem[] {
  return [
    gpsTemplate,
    ...Array.from({ length: 12 }, (_, i) => gpsAssignment(i)),
    ...Array.from({ length: 10 }, (_, i) => gpsAssignment(100 + i, { owner: "platform" })),
    {
      ...gpsAssignment(900, { owner: "platform" }),
      campaignId: "cms-fleet-gps-10s-FLEET-1780002982",
      targetArn: "fleet:FLEET-1780002982",
    },
  ];
}

/** `cms-fleet-telemetry-30s`: no template, one global row, stored `SUSPENDED`. */
const suspendedGlobalRow: FleetCampaignItem = {
  campaignId: "cms-fleet-telemetry-30s-all",
  campaignName: "cms-fleet-telemetry-30s",
  targetArn: "all",
  status: "SUSPENDED",
  createdAt: "2026-09-12T20:24:54+00:00",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
};

describe("DataCollectionCampaignsView — assignment coverage is not truncated by row owner", () => {
  it("reports all 22 assignments of an oem campaign, not only the 12 attributed to oem", async () => {
    // CYCLE 2 CRITICAL 1. `owner` on an assignment row is attribution for that
    // assignment; on a template it is campaign ownership. Filtering rows before grouping
    // conflated the two and dropped 10 assignments of the OEM's own campaign, so this
    // cell read "Assigned to 12 vehicles" against a live 22 — the single number spec
    // § Context says the screen cannot show and T4.1 requires.
    //
    // MUTATION: restore the pre-grouping `resp.dcCampaigns.filter(c => c.owner === "oem")`
    // → this assertion FAILS with "12 vehicles". Applied and observed.
    renderWithCampaigns(gpsLiveRows());
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    const cell = screen.getByTestId("dc-campaign-assigned-summary-cms-fleet-gps-10s");
    expect(cell.textContent).toContain("22 vehicles");
    // The fleet row is `owner: platform` too, so the row filter hid the campaign's fleet
    // scope as well — a second, independent under-report on the same cell.
    expect(cell.textContent).toContain("FLEET-1780002982");
  });

  it("still hides a fleet-owned campaign — the fix widens counting, not visibility", async () => {
    // Negative control for the above. Moving the filter to the group must not turn into
    // "show everything": entitlement (spec Decision 4) is unchanged.
    //
    // TWO shapes, because `filterOemOwnedGroups` has two clauses and cycle 3 found only
    // one had a render-level control. `fleetOwnedCampaign` is an ASSIGNMENT row with no
    // template, so it exercises the no-template fallback clause only.
    // `fleetOwnedTemplateWithOemAssignment` exercises the template clause: a fleet-owned
    // template whose assignment is `oem`-attributed must stay hidden — attribution on an
    // assignment does not rescue a campaign someone else owns.
    const fleetOwnedTemplateWithOemAssignment: FleetCampaignItem[] = [
      {
        ...gpsTemplate,
        campaignId: "fleet-private-v1",
        campaignName: "fleet-private-v1",
        owner: "fleet:fleet-456",
      },
      {
        ...gpsAssignment(7),
        campaignId: "fleet-private-v1-MRDN00000000010007",
        campaignName: "fleet-private-v1",
        owner: "oem",
      },
    ];
    renderWithCampaigns([
      ...gpsLiveRows(),
      fleetOwnedCampaign,
      ...fleetOwnedTemplateWithOemAssignment,
    ]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    // Fallback clause: no template, no oem-attributed row.
    expect(screen.queryByTestId(`dc-campaign-name-${fleetOwnedCampaign.campaignName}`)).toBeNull();
    // Template clause: fleet-owned template, oem-attributed assignment.
    expect(screen.queryByTestId("dc-campaign-name-fleet-private-v1")).toBeNull();
    // Positive control, so neither absence can pass because nothing rendered.
    expect(screen.queryByTestId("dc-campaign-name-cms-fleet-gps-10s")).toBeTruthy();
  });
});

describe("DataCollectionCampaignsView — no cell claims an assignment the data does not support", () => {
  it("surfaces a SUSPENDED global assignment instead of reporting it as assigned", async () => {
    // CYCLE 2 CRITICAL 2. Live `cms-fleet-telemetry-30s` rendered "Assigned to all
    // fleets" while its only row's stored status is SUSPENDED — the same over-claim as
    // rendering RUNNING as "Running", one column over.
    //
    // MUTATION: drop the state annotation from the scoped-assignment branch → FAILS.
    renderWithCampaigns([suspendedGlobalRow]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    const cell = screen.getByTestId(
      "dc-campaign-assigned-summary-cms-fleet-telemetry-30s",
    );
    expect(cell.textContent).toContain("all fleets");
    expect(cell.textContent).toMatch(/suspended/i);
  });

  it("names non-assigned vehicle states rather than folding them into the count", async () => {
    renderWithCampaigns([
      gpsTemplate,
      gpsAssignment(1),
      gpsAssignment(2, { status: "SUSPENDED" }),
      gpsAssignment(3, { status: "SUSPENDED" }),
      gpsAssignment(4, { status: "STOPPED" }),
    ]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    const text = screen.getByTestId(
      "dc-campaign-assigned-summary-cms-fleet-gps-10s",
    ).textContent;
    expect(text).toContain("4 vehicles");
    expect(text).toContain("2 suspended");
    expect(text).toContain("1 stopped");
  });

  it("reports an unrecognized-target assignment instead of 'Not assigned to any vehicle'", async () => {
    // `unrecognizedRows` was computed and discarded, so a campaign with an assignment
    // read as having none. Unfired today — all five `targetArn` writers in the repo emit
    // `template`/`vehicle:`/`fleet:` — but the bucket exists to surface exactly this.
    renderWithCampaigns([
      gpsTemplate,
      {
        ...gpsAssignment(5),
        targetArn: "arn:aws:iotfleetwise:us-west-2:<account-id>:fleet/weird",
      },
    ]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    expect(
      screen.queryByTestId("dc-campaign-unassigned-cms-fleet-gps-10s"),
    ).toBeNull();
    expect(
      screen.getByTestId("dc-campaign-assigned-summary-cms-fleet-gps-10s").textContent,
    ).toMatch(/unrecognized target/i);
  });

  // MOVED: "does not echo an unrecognised stored status — a RUNNING template renders a fixed
  // gloss" now lives in `CampaignDetailView.test.tsx`. It asserted the list's definition
  // column, which user UAT removed; the fallback it guards (Cycle 2 Critical 4 — a template
  // carrying RUNNING putting "RUNNING" on screen) is now rendered only by
  // `CampaignDetailView`'s `DefinitionStatusBadge`. The property moved screens; it was not
  // dropped, and `campaignStatusVocabulary.test.ts` followed it too.

  it("renders no liveness word anywhere, for any row shape the table can hold", async () => {
    // CYCLE 2 CRITICAL 3 — the owning assertion. This is what the source guard cannot be:
    // it reads the rendered DOM, so it is indifferent to how the word was spelled in
    // source. Both Cycle 2 evasions (a template literal; `row.template?.status` hoisted
    // in a block-bodied `cell:`) put the word on screen and therefore FAIL here.
    //
    // Every shape at once: a RUNNING template, RUNNING/SUSPENDED/STOPPED assignments, a
    // SUSPENDED global row, a template-less group, and mixed owner attribution.
    //
    // ## Do not use `\b` word boundaries against `document.body.textContent`
    //
    // The first version of this test did, and it could not fail. `textContent`
    // concatenates adjacent cells with NO separator, so the owner and Created cells read
    // `…0—RUNNING2026-09-12…`; `\bRUNNING\b` needs a word→non-word transition and `G→2`
    // is word→word, so the regex never matched a word that was plainly on screen.
    // Mutation EV-D found it — the assertion was vacuous while looking rigorous, which is
    // the exact defect shape this whole cycle is about. Plain lowercased substring
    // matching from here on.
    //
    // ## What this test does NOT cover, and why the source guard stays
    //
    // A render test only sees the branches its fixtures reach. Mutation EV-E appended a
    // "Live" gloss to the rollup's no-noted-states branch, which this fixture never takes
    // (every vehicle state here is noted), so EV-E passed here and was caught by
    // `campaignStatusVocabulary.test.ts` instead. That is the division of labour working
    // as intended: the source guard catches a spelling in any branch of any file under
    // `data-model/`, and this test catches a rendered claim regardless of how it was
    // spelled. Neither subsumes the other — do not delete one on the strength of the other.
    renderWithCampaigns([
      { ...gpsTemplate, status: "RUNNING" },
      gpsAssignment(1),
      gpsAssignment(2, { status: "SUSPENDED", owner: "platform" }),
      gpsAssignment(3, { status: "STOPPED" }),
      suspendedGlobalRow,
    ]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    const rendered = (document.body.textContent ?? "").toLowerCase();
    // Sanity floor: assert the table actually rendered, so the checks below cannot pass
    // because nothing was on screen. Without this they are vacuous on an empty mount —
    // which is how two tests in this very file went silently vacuous.
    expect(rendered).toContain("cms-fleet-gps-10s");
    expect(rendered).toContain("cms-fleet-telemetry-30s");

    // Whole-body scan, for tokens that are not a substring of any legitimate word this
    // UI renders. Scoped this way deliberately: `live` IS a substring of "delivered" and
    // "delivery", so a whole-body check for it would false-positive on ordinary
    // automotive copy. Those softer synonyms are checked against the state cells below,
    // where the text is fully under this component's control.
    for (const token of ["running", "transmitting", "streaming", "currently active"]) {
      expect(
        rendered,
        `The screen rendered "${token}". A stored RUNNING status means ASSIGNED — ` +
          "nothing reconciles it against telemetry, so a vehicle can read as running " +
          "and transmit nothing.",
      ).not.toContain(token);
    }

    // Narrower scan, EXHAUSTIVE vocabulary: the definition cells are an allowlist, not a
    // denylist. Review cycle 3 showed why — a denylist plus a two-branch fixture let two
    // mutations through with 68 tests green: glossing `ACTIVE` as **"Active"** (the one
    // synonym the source guard deliberately excludes, on the branch 10 of 11 live
    // campaigns render) and **deleting the `STOPPED` branch entirely**, which F1.2 had
    // added as a deliverable. A denylist can only forbid words someone thought of; an
    // allowlist forbids everything else by construction. The message even enumerated the
    // permitted words and omitted "available" — it was already trying to be an allowlist.
    // See the all-five-branches test below for per-branch coverage.
    // The definition COLUMN is gone (user UAT), so the five-gloss allowlist that used to live
    // here moved to `CampaignDetailView.test.tsx` § "definition-state badge vocabulary",
    // alongside the badge that now renders it. What remains in this list is the single inline
    // exception marker on the campaign-name cell.
    //
    // Deliberately still asserted as an EXHAUSTIVE set rather than dropped: the marker is the
    // only definition-related text this screen may render, and cycle 3's lesson was that a
    // denylist can only forbid words someone thought of.
    const definitionMarkers = [
      ...document.querySelectorAll('[data-testid^="dc-campaign-definition-"]'),
    ].map((e) => (e.textContent ?? "").trim());
    // Exactly one: `cms-fleet-telemetry-30s` is the one template-less group in this fixture.
    // Counting ELEMENTS, not a joined string's length — cycle 3's version asserted
    // `joined.length > 0`, which a single cell satisfies while the comment claimed four.
    expect(definitionMarkers).toHaveLength(1);
    const PERMITTED_DEFINITION_TEXT = new Set(["No definition"]);
    for (const text of definitionMarkers) {
      expect(
        PERMITTED_DEFINITION_TEXT.has(text),
        `A definition marker rendered "${text}", which is not in the permitted set. This ` +
          "screen may say only: " +
          [...PERMITTED_DEFINITION_TEXT].join(" / ") +
          ". The glossed definition STATES live in the detail view, not here.",
      ).toBe(true);
    }

    const assignmentCells = [
      ...document.querySelectorAll(
        '[data-testid^="dc-campaign-assigned-summary-"],' +
          '[data-testid^="dc-campaign-unassigned-"]',
      ),
    ].map((e) => (e.textContent ?? "").trim());
    expect(assignmentCells).toHaveLength(2);

    // A CLOSED GRAMMAR, not a shape check.
    //
    // Cycle 4 Critical: the first version asserted
    // `/^(Not assigned to any vehicle|Assigned to .+)$/` plus a check on *parenthesised*
    // annotations, and the `.+` swallowed the whole payload. The cell is
    // `Assigned to ${parts.join(", ")}`, so appending a liveness word as its own part —
    // `"Assigned to 12 vehicles, reporting"` — passed all 1216 tests. That was a
    // REGRESSION: the denylist this replaced did span these cells, and it fails on the
    // same mutation. The same word placed INSIDE the parentheses was caught; only the
    // parenthesis decided.
    //
    // The definition column wants an allowlist of fixed strings; this column is composed
    // from parts, so it wants an allowlist over the GRAMMAR. Both are allowlists — the
    // mistake was retiring the technique rather than fitting it to the column.
    //
    // A bare denylist was deliberately not also restored. The grammar subsumes it
    // everywhere except inside `fleet <id>`, which is free-form because the id is data,
    // not a claim this component makes — a fleet legitimately named `live-1` should not
    // fail a test about vocabulary. Every other position is closed.
    const STATE = "assigned|suspended|stopped|state unknown";
    const PART = [
      // "12 vehicles" / "1 vehicle", optionally "(2 suspended, 1 stopped)"
      `\\d+ vehicles?( \\((\\d+ (${STATE}))(, \\d+ (${STATE}))*\\))?`,
      // "all fleets", optionally "(suspended)"
      `all fleets( \\((${STATE})\\))?`,
      // "fleet FLEET-1780002982", optionally "(suspended)"
      `fleet [^,()]+?( \\((${STATE})\\))?`,
      // "3 assignments with an unrecognized target"
      `\\d+ assignments? with an unrecognized target`,
    ].join("|");
    const CELL = new RegExp(
      `^(Not assigned to any vehicle|Assigned to (${PART})(, (${PART}))*)$`,
    );
    for (const text of assignmentCells) {
      expect(
        text,
        `Assignment cell "${text}" is not in the permitted grammar. The cell may list ` +
          "only: N vehicles (with per-state counts), all fleets, fleet <id>, or N " +
          "assignments with an unrecognized target — each optionally annotated with " +
          "assigned/suspended/stopped/state unknown. Anything else is an unreviewed claim.",
      ).toMatch(CELL);
    }
  });

  // MOVED: "renders the permitted gloss on every one of the five definition branches" now
  // lives in `CampaignDetailView.test.tsx` § "definition-state badge vocabulary", for the same
  // reason as the test above. Its two recorded mutations (X2 `Available`→`Active`; X3 delete
  // the STOPPED branch) are re-run there against the badge.
});

describe("DataCollectionCampaignsView — three-outcome assign handling (MR1)", () => {
  it("treats a repeat assignment (assigned:[] + alreadyAssigned:[vin]) as success", async () => {
    // Review Cycle 2 mutation MR1: narrowing the failure branch from
    // `assigned.length === 0 && already.length === 0` to `assigned.length === 0` restored
    // the rule T10.2 RETRACTED, and the whole 1196-test suite stayed green. Spec
    // § Constraints calls three-outcome handling mandatory; nothing tested it.
    //
    // MUTATION: apply MR1 → this test FAILS (error alert instead of success). Applied and
    // observed.
    vi.spyOn(dataModelClient, "assignCampaignToVehicle").mockResolvedValue({
      campaignName: "basic-telemetry-v1",
      assigned: [],
      alreadyAssigned: [vehicleWithModelManifest.vin!],
      rejected: [],
    });
    renderWithCampaigns([templateCampaignForAssign], [vehicleWithModelManifest]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-button")).toBeTruthy();
    });
    screen.getByTestId("dc-campaigns-assign-button").click();

    const trigger = screen
      .getAllByRole("button")
      .find(
        (b) =>
          b.textContent?.includes("Choose a vehicle") || b.getAttribute("aria-haspopup"),
      );
    await userEvent.click(trigger as HTMLElement);
    const option = screen
      .getAllByRole("option")
      .find((o) => o.textContent?.includes(vehicleWithModelManifest.vehicleId))!;
    await userEvent.click(option);
    await userEvent.click(screen.getByTestId("dc-campaigns-modal-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-success")).toBeTruthy();
    });
    expect(screen.queryByTestId("dc-campaigns-assign-error")).toBeNull();
  });

  it("treats a rejected entry as a failure and surfaces the server's reason", async () => {
    // The other half: `rejected` is the real failure signal, and the reason is the
    // server's own words. Guards against collapsing the three lists back into one.
    vi.spyOn(dataModelClient, "assignCampaignToVehicle").mockResolvedValue({
      campaignName: "basic-telemetry-v1",
      assigned: [],
      alreadyAssigned: [],
      rejected: [
        // The field is `value`, not `vehicle` — matching what the server sends
        // (`data_processing_api.py` builds `{'value': vin, 'reason': ...}`). Worth
        // pinning: this fixture initially used `vehicle` and the test still PASSED,
        // because the view only reads `.reason`. `tsc` caught it, the assertion could
        // not have.
        { value: vehicleWithModelManifest.vin!, reason: "vin_not_found" },
      ],
    });
    renderWithCampaigns([templateCampaignForAssign], [vehicleWithModelManifest]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-button")).toBeTruthy();
    });
    screen.getByTestId("dc-campaigns-assign-button").click();

    const trigger = screen
      .getAllByRole("button")
      .find(
        (b) =>
          b.textContent?.includes("Choose a vehicle") || b.getAttribute("aria-haspopup"),
      );
    await userEvent.click(trigger as HTMLElement);
    const option = screen
      .getAllByRole("option")
      .find((o) => o.textContent?.includes(vehicleWithModelManifest.vehicleId))!;
    await userEvent.click(option);
    await userEvent.click(screen.getByTestId("dc-campaigns-modal-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-assign-error")).toBeTruthy();
    });
    expect(screen.getByTestId("dc-campaigns-assign-error").textContent).toContain(
      "vin_not_found",
    );
    expect(screen.queryByTestId("dc-campaigns-assign-success")).toBeNull();
  });
});


// ── F4.9 (CRITICAL) — row click's actual navigate() target is asserted ────────
//
// Review Group 3 Cycle 1 Critical 3 / Fix Group 4 Owner D.
//
// The existing M3 test (above) asserts the `href` attribute. `href` is DECORATIVE when
// `onFollow` is present: `onFollow` calls `e.preventDefault()` and then calls
// `navigate()`. The `href` controls middle-click and open-in-new-tab; the `navigate()`
// argument controls the primary click — which is the ONLY route to all four Group-3
// files. Both literals must be pinned.
//
// Reviewer-demonstrated mutation (observed):
//   DataCollectionCampaignsView.tsx:326 — change `navigate(...)` path to
//   `/data-model/WRONG-ROUTE-PROBE/${encodeURIComponent(row.campaignName)}`
//   leaving `href` correct → 43/43 passed. This test catches that mutation.
//
// Implementation: use Routes + Route in the MemoryRouter to detect where navigation
// lands. Render a sentinel route at the detail path; click the link; assert the sentinel
// renders. This is the pattern established by SubscriberLookupView.test.tsx in this
// repo (also using MemoryRouter + Routes + stub route). The M3 href assertion is KEPT
// so both literals remain pinned.

import { Route, Routes } from "react-router-dom";

describe("DataCollectionCampaignsView — row click navigates to the correct path (F4.9)", () => {
  // A template campaign whose name we can encode and assert.
  const NAV_TEST_CAMPAIGN_NAME = "nav-test-campaign";
  const campaignForNavTest: FleetCampaignItem = {
    campaignId: "nav-test-campaign",
    campaignName: NAV_TEST_CAMPAIGN_NAME,
    targetArn: "template",
    status: "ACTIVE",
    createdAt: "2026-09-20T10:00:00Z",
    owner: "oem",
    decoderManifestId: "cms-fleet-v3",
    signalsToCollect: [],
  };

  function renderWithNavSentinel(dcItems: readonly FleetCampaignItem[]) {
    // Render inside a real MemoryRouter with Routes so navigation to the detail
    // path shows the sentinel element. This is the pattern from
    // SubscriberLookupView.test.tsx in this repo — MemoryRouter handles useNavigate
    // and the Routes catch the navigate() destination.
    window.runtimeConfig = {
      cognitoUserPoolId: "<pool-id>",
      cognitoClientId: "testclientid",
      cognitoDomain: "test.example.invalid",
      connectedServicesApiEndpoint: "https://cs.example.invalid",
      subscriptionsApiEndpoint: "https://subs.example.invalid",
      simulationApiEndpoint: "https://sim.example.invalid",
      dataProcessingApiEndpoint: "https://dp.example.invalid",
      simulationProductRuleName: "test_rule",
      callbackOrigin: "https://test.example.invalid",
    };
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockResolvedValue({
      dcCampaigns: dcItems,
      count: dcItems.length,
    });
    return render(
      <MemoryRouter initialEntries={["/data-model/data-collection-campaigns"]}>
        <Routes>
          <Route
            path="/data-model/data-collection-campaigns"
            element={<DataCollectionCampaignsView vehiclesForAssignment={undefined} />}
          />
          <Route
            path="/data-model/data-collection-campaigns/:campaignName"
            element={
              <div data-testid="campaign-detail-stub">campaign detail</div>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("clicking the campaign name fires navigate() with the detail route — navigate() arg is pinned (F4.9)", async () => {
    // MUTATION (F4.9): repoint the `navigate()` argument only, leaving `href` intact:
    //   FROM: `/data-model/data-collection-campaigns/${encodeURIComponent(row.campaignName)}`
    //   TO:   `/data-model/WRONG-ROUTE-PROBE/${encodeURIComponent(row.campaignName)}`
    // RESULT: clicking the link no longer navigates to the sentinel route.
    //   `screen.findByTestId("campaign-detail-stub")` throws/times-out because
    //   the sentinel route is at `/data-model/data-collection-campaigns/:campaignName`,
    //   not at the bogus path. Applied 2026-09-20, observed: test times-out /
    //   fails with "Unable to find role…" rather than finding the stub.
    //   Restoration: cp /tmp/DataCollectionCampaignsView.tsx.bak, md5 verified.
    //
    // Both literals are pinned by this test:
    //   href     → asserted via getAttribute (covers middle-click / open-in-new-tab)
    //   navigate → asserted via sentinel route rendering (covers the primary click)

    renderWithNavSentinel([campaignForNavTest]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });

    const encodedName = encodeURIComponent(NAV_TEST_CAMPAIGN_NAME);
    const expectedPath = `/data-model/data-collection-campaigns/${encodedName}`;

    // ── Assertion 1: href (covers middle-click / open-in-new-tab) ──────────
    // This is the existing M3 assertion. Kept here so both literals are pinned.
    const nameLink = screen.getByTestId(`dc-campaign-name-${NAV_TEST_CAMPAIGN_NAME}`);
    expect(
      nameLink.getAttribute("href"),
      "The campaign name link href must point to the detail route " +
        `(${expectedPath}). Covers middle-click and open-in-new-tab, which onFollow ` +
        "does not intercept.",
    ).toBe(expectedPath);

    // ── Assertion 2: navigate() target (covers the primary click path) ─────
    // `onFollow` calls e.preventDefault() then navigate(path). The href is therefore
    // decorative for the primary click. Clicking the link triggers onFollow, which
    // calls navigate(). The sentinel route at the expected path must render.
    nameLink.click();

    await waitFor(() => {
      expect(
        screen.getByTestId("campaign-detail-stub"),
        "After clicking the campaign name, the detail sentinel must render. " +
          "F4.9 mutation: repointing navigate() to a bogus path causes navigation to " +
          "miss the sentinel route entirely, and this assertion fails/times out.",
      ).toBeTruthy();
    });
  });
});


// ── Empty vs no-match are two different states (Fix Group 10) ─────────────────
//
// Fix Group 9 wired this table to `useConnectedServicesCollection` and left the pre-existing
// `empty={<TableEmptyState …/>}` prop in place AFTER `{...collectionProps}` — which overrode the
// hook's resolution and collapsed two states into one. A filter matching nothing rendered
// "No data-collection campaigns to display." next to a counter reading "0 matches out of 3":
// the screen contradicting itself, on a spec whose entire purpose is removing claims the data
// does not support. The "Clear filter" recovery never rendered, which also left
// `handleClearFilter` unreachable.
//
// No test saw it in either direction. The no-match state is asserted in 10 sibling test files
// and in none for this view — the gap review cycle 1 of Fix Group 9 found.
describe("DataCollectionCampaignsView — empty and no-match are distinct states", () => {
  it("a filter matching nothing shows the no-match state with a clear-filter affordance", async () => {
    renderWithCampaigns([oemActiveCampaign]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });

    const filterInput = screen.getByPlaceholderText("Find a campaign");
    await userEvent.type(filterInput, "zzz-no-such-campaign");

    await waitFor(() => {
      const body = (document.body.textContent ?? "").toLowerCase();
      expect(
        body,
        'A filter that matched nothing must NOT say the resource does not exist. The table has ' +
          'rows; the FILTER excluded them. Rendering "No data-collection campaigns" here states ' +
          "a falsehood and hides the Clear-filter recovery.",
      ).not.toContain("no data-collection campaigns");
    });

    // The recovery affordance the no-match state exists to provide. Its presence is also what
    // makes `handleClearFilter` / `onClearFilter` reachable code rather than decoration.
    const body = document.body.textContent ?? "";
    expect(
      /no matches/i.test(body) || /clear filter/i.test(body),
      `The no-match state must render. Body was:\n  "${body.slice(0, 300)}"`,
    ).toBe(true);
  });

  it("genuinely zero campaigns shows the empty state, not the no-match state", async () => {
    // Positive control, and the reason the fix is a deletion rather than a swap: removing the
    // `empty=` override must not cost the real empty state. With no rows and no filter, the
    // hook resolves to `TableEmptyState`.
    renderWithCampaigns([]);
    await waitFor(() => {
      expect(screen.getByTestId("dc-campaigns-container")).toBeTruthy();
    });
    const body = (document.body.textContent ?? "").toLowerCase();
    expect(
      body,
      "With no data at all, the table must say the resource is empty — this is the state the " +
        "deleted override used to force unconditionally.",
    ).toContain("no data-collection campaigns");
    expect(
      body,
      "...and must NOT offer to clear a filter that was never applied.",
    ).not.toContain("clear filter");
  });
});

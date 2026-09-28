/**
 * T1.1 — `groupCampaigns`.
 *
 * Fixtures use realistic shapes taken from staging (`cms-fleet-gps-10s` with
 * `MRDN…`/`CSPR…` VINs, and the mis-keyed `VEH-CS-DEMO-0003` row that actually exists),
 * not `"a"`/`"b"` placeholders — a placeholder fixture cannot exercise the
 * `campaignId` vs `campaignName` divergence that is the whole point of the grouping key.
 *
 * Each mutation below was APPLIED and the named test OBSERVED failing. Reading the note
 * is not evidence.
 */

import { describe, expect, it } from "vitest";
import {
  ASSIGNMENT_STATE_LABEL,
  assignmentState,
  filterOemOwnedGroups,
  groupCampaigns,
  type AssignmentState,
} from "../campaignGrouping";
import type { DataProcessingCampaignItem } from "../../../../api/dataModelClient";

function template(
  campaignName: string,
  over: Partial<DataProcessingCampaignItem> = {},
): DataProcessingCampaignItem {
  return {
    campaignId: campaignName,
    campaignName,
    targetArn: "template",
    status: "ACTIVE",
    createdAt: "2026-09-12T20:24:54+00:00",
    owner: "oem",
    decoderManifestId: "cms-fleet-v3",
    ...over,
  };
}

function assignment(
  campaignName: string,
  target: string,
  over: Partial<DataProcessingCampaignItem> = {},
): DataProcessingCampaignItem {
  return {
    // NOTE the divergence this fixture exists to model: campaignId carries the target
    // suffix, campaignName does not.
    campaignId: `${campaignName}-${target}`,
    campaignName,
    targetArn: `vehicle:${target}`,
    status: "RUNNING",
    createdAt: "2026-09-20T14:39:11+00:00",
    owner: "oem",
    ...over,
  };
}

describe("groupCampaigns", () => {
  it("groups by campaignName, not campaignId", () => {
    // MUTATION: key the map on `row.campaignId` -> this test FAILS (3 groups, not 1).
    // APPLIED 2026-09-20, OBSERVED failing.
    const groups = groupCampaigns([
      template("cms-fleet-gps-10s"),
      assignment("cms-fleet-gps-10s", "MRDN0000000000002"),
      assignment("cms-fleet-gps-10s", "CSPR0000000000021"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].campaignName).toBe("cms-fleet-gps-10s");
    expect(groups[0].vehicleCount).toBe(2);
  });

  it("counts only vehicle: rows — fleet: and all are excluded", () => {
    // MUTATION: set `vehicleCount` to vehicles.length + scoped.length -> FAILS.
    // APPLIED 2026-09-20, OBSERVED failing.
    //
    // This is the defect the flat screen had, in the other direction: a `fleet:` row is
    // not a vehicle, and counting it would make the number untrustworthy again.
    const groups = groupCampaigns([
      template("cms-fleet-gps-10s"),
      assignment("cms-fleet-gps-10s", "MRDN0000000000002"),
      {
        ...template("cms-fleet-gps-10s"),
        campaignId: "cms-fleet-gps-10s-fleet:FLEET-1780002982",
        targetArn: "fleet:FLEET-1780002982",
        status: "RUNNING",
      },
      {
        ...template("cms-fleet-telemetry-30s"),
        campaignId: "cms-fleet-telemetry-30s-all",
        targetArn: "all",
        status: "SUSPENDED",
      },
    ]);
    const gps = groups.find((g) => g.campaignName === "cms-fleet-gps-10s")!;
    expect(gps.vehicleCount).toBe(1);
    expect(gps.scopedAssignments).toHaveLength(1);
    expect(gps.scopedAssignments[0].scope).toBe("fleet");
    expect(gps.scopedAssignments[0].fleetId).toBe("FLEET-1780002982");

    const t30 = groups.find((g) => g.campaignName === "cms-fleet-telemetry-30s")!;
    expect(t30.vehicleCount).toBe(0);
    expect(t30.scopedAssignments[0].scope).toBe("all");
    expect(t30.scopedAssignments[0].fleetId).toBeNull();
  });

  it("emits a group with template: null when a campaign has assignments but no definition", () => {
    // MUTATION: skip rows whose group has no template -> FAILS.
    // APPLIED 2026-09-20, OBSERVED failing.
    //
    // Reachable: the template is a separate record and nothing enforces its existence.
    // The assignments must survive — hiding them would hide live telemetry config.
    const groups = groupCampaigns([
      assignment("orphaned-campaign", "MRDN0000000000002"),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].template).toBeNull();
    expect(groups[0].vehicleCount).toBe(1);
  });

  it("emits vehicleCount 0 for a template with no assignments", () => {
    // 8 of 10 staging campaigns are in this state, so it is the common case.
    const groups = groupCampaigns([template("cms-safety-speeding")]);
    expect(groups).toHaveLength(1);
    expect(groups[0].vehicleCount).toBe(0);
    expect(groups[0].vehicleAssignments).toHaveLength(0);
    expect(groups[0].template).not.toBeNull();
  });

  it("flags a vehicleId target as invalid without dropping it", () => {
    // MUTATION: drop flagged rows instead of flagging them -> FAILS on the length
    // assertion. APPLIED 2026-09-20, OBSERVED failing.
    //
    // `cms-fleet-gps-10s-VEH-CS-DEMO-0003` really exists on staging, written by the
    // pre-fix defect. It reads as a valid assignment; dropping it is how it hid.
    const groups = groupCampaigns([
      template("cms-fleet-gps-10s"),
      assignment("cms-fleet-gps-10s", "VEH-CS-DEMO-0003"),
      assignment("cms-fleet-gps-10s", "MRDN0000000000002"),
    ]);
    const g = groups[0];
    expect(g.vehicleCount).toBe(2);
    const flagged = g.vehicleAssignments.filter((a) => a.targetLooksInvalid);
    expect(flagged).toHaveLength(1);
    expect(flagged[0].target).toBe("VEH-CS-DEMO-0003");
  });

  it("does NOT flag real VINs that fail a strict VIN charset", () => {
    // The anti-regression test for the heuristic's design. A strict 17-char VIN regex
    // would reject 8 of 155 staging VINs: seven `DEMO…` contain the letter O (outside
    // the real VIN alphabet) and `4T1B11HK0LU98765` is 16 chars.
    //
    // MUTATION: replace the heuristic with /^[A-HJ-NPR-Z0-9]{17}$/ -> this test FAILS on
    // both values, while the vehicleId test above still PASSES — which is the trap: a
    // shape guard looks correct until it meets real data.
    // APPLIED 2026-09-20, OBSERVED failing.
    const groups = groupCampaigns([
      template("c"),
      assignment("c", "DEMO0000000000005"),
      assignment("c", "4T1B11HK0LU98765"),
    ]);
    expect(groups[0].vehicleAssignments.every((a) => !a.targetLooksInvalid)).toBe(true);
  });

  it("collects rows whose targetArn matches no known shape", () => {
    const groups = groupCampaigns([
      template("c"),
      { ...template("c"), campaignId: "c-weird", targetArn: "somethingelse" },
    ]);
    expect(groups[0].unrecognizedRows).toHaveLength(1);
    expect(groups[0].vehicleCount).toBe(0);
  });

  it("skips a row with no campaignName rather than inventing a group", () => {
    const groups = groupCampaigns([
      template("cms-fleet-gps-10s"),
      { ...assignment("x", "MRDN0000000000002"), campaignName: "" },
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].campaignName).toBe("cms-fleet-gps-10s");
    expect(groups[0].vehicleCount).toBe(0);
  });

  it("returns groups sorted by campaignName for a stable render order", () => {
    // The API's order is a DynamoDB scan order and is not stable across calls.
    const groups = groupCampaigns([
      template("uds-dtc-polling"),
      template("cms-fleet-gps-10s"),
      template("cms-safety-speeding"),
    ]);
    expect(groups.map((g) => g.campaignName)).toEqual([
      "cms-fleet-gps-10s",
      "cms-safety-speeding",
      "uds-dtc-polling",
    ]);
  });

  it("handles an empty input", () => {
    expect(groupCampaigns([])).toHaveLength(0);
  });
});


// ── Fix Group 1 (review Cycle 2) ───────────────────────────────────────────────

describe("assignmentState", () => {
  it("maps the three stored assignment statuses onto the displayed vocabulary", () => {
    // Spec § "Status vocabulary". `RUNNING` means ASSIGNED, not transmitting.
    expect(assignmentState({ status: "RUNNING" })).toBe("assigned");
    expect(assignmentState({ status: "SUSPENDED" })).toBe("suspended");
    expect(assignmentState({ status: "STOPPED" })).toBe("stopped");
  });

  it("is case-insensitive on the stored value", () => {
    expect(assignmentState({ status: "running" })).toBe("assigned");
    expect(assignmentState({ status: "Suspended" })).toBe("suspended");
  });

  it("returns `unknown` for an unrecognised status and never echoes it", () => {
    // Reachable: `PUT /campaigns/{id}` takes `status` from the request body with no
    // allowlist, so any string can land on a row. The return type is a closed union
    // precisely so a caller cannot render the stored token by accident — which is the
    // Cycle 2 defect in `CampaignDefinitionIndicator`.
    for (const s of ["ACTIVE", "PENDING", "", "DELETED", "RUNNING_SLOWLY"]) {
      expect(assignmentState({ status: s })).toBe("unknown");
    }
  });

  it("every state has a label and no label is a liveness word", () => {
    const states: AssignmentState[] = ["assigned", "suspended", "stopped", "unknown"];
    for (const s of states) {
      expect(ASSIGNMENT_STATE_LABEL[s]).toBeTruthy();
    }
    const joined = Object.values(ASSIGNMENT_STATE_LABEL).join(" ");
    expect(joined).not.toMatch(/running|live|transmitting|streaming/i);
  });
});

describe("groupCampaigns — assignment state is carried onto each assignment", () => {
  it("annotates vehicle assignments with their state", () => {
    const groups = groupCampaigns([
      template("cms-fleet-gps-10s"),
      assignment("cms-fleet-gps-10s", "MRDN0000000000002"),
      assignment("cms-fleet-gps-10s", "MRDN0000000000003", { status: "SUSPENDED" }),
      assignment("cms-fleet-gps-10s", "MRDN0000000000004", { status: "STOPPED" }),
    ]);
    expect(groups[0].vehicleAssignments.map((v) => v.state)).toEqual([
      "assigned",
      "suspended",
      "stopped",
    ]);
  });

  it("annotates fleet and global assignments with their state", () => {
    // The live shape that broke: `cms-fleet-telemetry-30s` is a single `all` row whose
    // stored status is SUSPENDED, and the rollup reported it as an active assignment.
    const groups = groupCampaigns([
      {
        ...template("cms-fleet-telemetry-30s"),
        targetArn: "all",
        status: "SUSPENDED",
      },
      assignment("cms-fleet-gps-10s", "x", {
        targetArn: "fleet:FLEET-1780002982",
        status: "RUNNING",
      }),
    ]);
    const byName = new Map(groups.map((g) => [g.campaignName, g]));
    expect(byName.get("cms-fleet-telemetry-30s")!.scopedAssignments[0].state).toBe(
      "suspended",
    );
    expect(byName.get("cms-fleet-gps-10s")!.scopedAssignments[0].state).toBe("assigned");
  });
});

describe("filterOemOwnedGroups", () => {
  it("keeps an oem campaign's assignments even when the ASSIGNMENT rows are not oem-owned", () => {
    // The Cycle 2 Critical, as a fixture. Live: `cms-fleet-gps-10s` has 22 `vehicle:`
    // rows — 12 `owner: oem` and 10 `owner: platform`, the latter written by
    // `_ensure_telemetry_campaign` / `deploy_vehicle_campaign.py`. Those 10 are
    // assignments OF THE OEM'S OWN CAMPAIGN. A pre-grouping row filter dropped them and
    // the screen reported 12 where spec § Context asks for 22.
    const rows = [
      template("cms-fleet-gps-10s"),
      ...Array.from({ length: 12 }, (_, i) =>
        assignment("cms-fleet-gps-10s", `MRDN000000000${String(1000 + i)}`),
      ),
      ...Array.from({ length: 10 }, (_, i) =>
        assignment("cms-fleet-gps-10s", `MRDN000000000${String(2000 + i)}`, {
          owner: "platform",
        }),
      ),
      assignment("cms-fleet-gps-10s", "x", {
        targetArn: "fleet:FLEET-1780002982",
        owner: "platform",
      }),
    ];
    const visible = filterOemOwnedGroups(groupCampaigns(rows));
    expect(visible).toHaveLength(1);
    // The number the whole restructure exists to produce.
    expect(visible[0].vehicleCount).toBe(22);
    // ...and the fleet scope, which the row filter also hid.
    expect(visible[0].scopedAssignments).toHaveLength(1);
  });

  it("hides a campaign whose TEMPLATE is fleet-owned, assignments and all", () => {
    const visible = filterOemOwnedGroups(
      groupCampaigns([
        template("fleet-telemetry", { owner: "fleet:fleet-123" }),
        assignment("fleet-telemetry", "MRDN0000000000009", { owner: "oem" }),
        template("cms-fleet-gps-10s"),
      ]),
    );
    // An `oem`-attributed assignment does NOT rescue a fleet-owned campaign: the
    // template decides, and it is the only thing that decides when one exists.
    expect(visible.map((g) => g.campaignName)).toEqual(["cms-fleet-gps-10s"]);
  });

  it("keeps a template-less group when an assignment row is oem-owned", () => {
    // Live: `cms-fleet-telemetry-30s` — one `all` row, `owner: oem`, no template. It is
    // visible on the screen today and must stay visible. Recorded as a decision rather
    // than left to a pre-grouping filter to settle by accident.
    const visible = filterOemOwnedGroups(
      groupCampaigns([
        {
          ...template("cms-fleet-telemetry-30s"),
          targetArn: "all",
          status: "SUSPENDED",
          owner: "oem",
        },
      ]),
    );
    expect(visible.map((g) => g.campaignName)).toEqual(["cms-fleet-telemetry-30s"]);
    expect(visible[0].template).toBeNull();
  });

  it("keeps a template-less group on a MIXED assignment set — `some`, not `every`", () => {
    // Security review S1/S2: the two tests either side of this one both use a uniform
    // assignment set, where `some` and `every` return the same answer — so mutating the
    // fallback clause from `.some` to `.every` survived both. The clause is deliberately
    // `some`: `cms-fleet-gps-10s` shows that an oem campaign legitimately carries
    // `platform`-attributed assignment rows, so requiring EVERY row to be oem-owned would
    // hide exactly the campaigns this fix exists to count correctly.
    //
    // MUTATION: `assignmentRows.some` → `assignmentRows.every` → this test FAILS.
    // Applied and observed.
    const visible = filterOemOwnedGroups(
      groupCampaigns([
        assignment("template-less-mixed", "MRDN0000000000021", { owner: "oem" }),
        assignment("template-less-mixed", "MRDN0000000000022", { owner: "platform" }),
      ]),
    );
    expect(visible.map((g) => g.campaignName)).toEqual(["template-less-mixed"]);
    expect(visible[0].vehicleCount).toBe(2);
  });

  it("hides a template-less group with no oem-owned assignment", () => {
    const visible = filterOemOwnedGroups(
      groupCampaigns([
        assignment("stray-fleet-campaign", "MRDN0000000000007", {
          owner: "fleet:fleet-123",
        }),
        assignment("stray-platform-campaign", "MRDN0000000000008", {
          owner: "platform",
        }),
      ]),
    );
    expect(visible).toHaveLength(0);
  });

  it("considers an unrecognized-target row when deciding a template-less group", () => {
    // `unrecognizedRows` is part of the group's assignment set, so it must count toward
    // the ownership signal too — otherwise a group whose only row has an odd `targetArn`
    // would be hidden for a reason unrelated to ownership.
    const visible = filterOemOwnedGroups(
      groupCampaigns([
        assignment("odd-target-campaign", "x", {
          targetArn: "arn:aws:iotfleetwise:us-west-2:<account-id>:fleet/weird",
          owner: "oem",
        }),
      ]),
    );
    expect(visible.map((g) => g.campaignName)).toEqual(["odd-target-campaign"]);
    expect(visible[0].unrecognizedRows).toHaveLength(1);
  });

  it("handles an empty input", () => {
    expect(filterOemOwnedGroups([])).toHaveLength(0);
  });
});

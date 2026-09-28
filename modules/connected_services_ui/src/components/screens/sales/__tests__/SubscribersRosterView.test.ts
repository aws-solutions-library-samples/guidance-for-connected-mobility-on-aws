// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SubscribersRosterView — grouping logic and de-fabrication guard.
 *
 * Two things are worth asserting here and they are different in kind.
 *
 * 1. `groupSubscriptionsByConsumer` collapses subscription rows into one row per consumer.
 *    The property that is easy to get wrong is the VIN count: it must be a UNION across a
 *    consumer's subscriptions, not a sum of `vehicle_scope_count`, because two
 *    subscriptions to different products can cover the same VIN. A sum reports a fleet
 *    larger than it is, and it does so plausibly — the number just looks high.
 *
 * 2. The five fabricated organisations this screen used to ship are gone and must not
 *    come back. That is a source-level assertion because the failure it guards is someone
 *    re-adding a "demo texture" row to make a sparse screen look fuller, which is exactly
 *    how the screen got that way.
 *
 * Deliberately NOT tested here: the rendered table. Rendering it needs the subscriptions
 * client stubbed, and a stub cannot fail the way the real endpoint fails — the endpoint is
 * caller-scoped, so what it returns for an OEM operator versus a fleet-portal principal is
 * exactly the thing worth knowing and exactly what a stub decides for you. That check
 * belongs against the deployed API, not here.
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  groupSubscriptionsByConsumer,
} from "../SubscribersRosterView";
import type { SubscriptionRow } from "../../../../api/subscriptionsClient";

const SOURCE = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "..", "SubscribersRosterView.tsx"),
  "utf8",
);

/**
 * Source with comments stripped.
 *
 * The absence assertions below MUST run against this, not against SOURCE. The screen's own
 * docstring documents what was removed — it names `STUB_SUBSCRIBERS` and the five invented
 * organisations, deliberately, so the next reader knows this screen was fabricated and
 * what that looked like. Asserting absence over raw source makes that history trip the
 * guard, i.e. the only way to pass would be to delete the explanation.
 *
 * Same shape as the trap in `~/.kiro/steering/public-mirror-publish.md`: a denylist
 * written in terms of the thing it forbids embeds that thing. Caught here by the guard
 * failing on first run against a correctly-rewritten file.
 */
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

/** Minimal SubscriptionRow factory — only the fields the grouping reads. */
function sub(over: Partial<SubscriptionRow> & Pick<SubscriptionRow, "consumer_id">): SubscriptionRow {
  return {
    subscription_id: "sub-1",
    product_id: "telemetry-hifi-v1",
    vehicle_scope: [],
    vehicle_scope_count: 0,
    state: "active",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

describe("SubscribersRosterView — corpus sanity", () => {
  it("read a non-trivial source file", () => {
    expect(SOURCE.length).toBeGreaterThan(2000);
  });

  it("the grouping function is exported and callable", () => {
    expect(typeof groupSubscriptionsByConsumer).toBe("function");
    expect(groupSubscriptionsByConsumer([])).toEqual([]);
  });
});

describe("groupSubscriptionsByConsumer", () => {
  it("collapses several subscriptions from one consumer into a single row", () => {
    const rows = groupSubscriptionsByConsumer([
      sub({ consumer_id: "cms", subscription_id: "s1", product_id: "telemetry-hifi-v1" }),
      sub({ consumer_id: "cms", subscription_id: "s2", product_id: "diagnostics-v1" }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].subscriptions).toBe(2);
    expect(rows[0].products).toEqual(["diagnostics-v1", "telemetry-hifi-v1"]);
  });

  it("counts VINs as a UNION across subscriptions, not a sum", () => {
    // Two products covering an overlapping VIN set: {A,B} and {B,C} = 3 distinct VINs.
    // Summing vehicle_scope_count would report 4.
    const rows = groupSubscriptionsByConsumer([
      sub({
        consumer_id: "cms",
        subscription_id: "s1",
        product_id: "telemetry-hifi-v1",
        vehicle_scope: ["VIN-A", "VIN-B"],
        vehicle_scope_count: 2,
      }),
      sub({
        consumer_id: "cms",
        subscription_id: "s2",
        product_id: "diagnostics-v1",
        vehicle_scope: ["VIN-B", "VIN-C"],
        vehicle_scope_count: 2,
      }),
    ]);
    expect(rows[0].enrolledVins).toBe(3);
  });

  it("keeps distinct consumers as separate rows, sorted by alias", () => {
    const rows = groupSubscriptionsByConsumer([
      sub({ consumer_id: "zeta-analytics" }),
      sub({ consumer_id: "cms" }),
    ]);
    expect(rows.map((r) => r.consumerId)).toEqual(["cms", "zeta-analytics"]);
  });

  it("labels a recognised CMS consumer and leaves others under their raw id", () => {
    const rows = groupSubscriptionsByConsumer([
      sub({ consumer_id: "meridian-cms-staging" }),
      sub({ consumer_id: "some-third-party" }),
    ]);
    const byId = new Map(rows.map((r) => [r.consumerId, r]));

    expect(byId.get("meridian-cms-staging")!.alias).toBe("meridian-cms");
    expect(byId.get("meridian-cms-staging")!.organization).toContain("Meridian");

    // An unrecognised consumer must NOT be given an invented organisation.
    expect(byId.get("some-third-party")!.alias).toBe("some-third-party");
    expect(byId.get("some-third-party")!.organization).toBe("—");
  });

  it("reports the EARLIEST created_at as activeSince", () => {
    const rows = groupSubscriptionsByConsumer([
      sub({ consumer_id: "cms", subscription_id: "s1", created_at: "2026-09-10T00:00:00Z" }),
      sub({ consumer_id: "cms", subscription_id: "s2", created_at: "2026-06-01T00:00:00Z" }),
    ]);
    expect(rows[0].activeSince).toBe("2026-06-01T00:00:00Z");
  });

  it("collapses duplicate states and preserves several when they differ", () => {
    const one = groupSubscriptionsByConsumer([
      sub({ consumer_id: "cms", subscription_id: "s1", state: "active" }),
      sub({ consumer_id: "cms", subscription_id: "s2", state: "active" }),
    ]);
    expect(one[0].states).toEqual(["active"]);

    const two = groupSubscriptionsByConsumer([
      sub({ consumer_id: "cms", subscription_id: "s1", state: "active" }),
      sub({ consumer_id: "cms", subscription_id: "s2", state: "suspended" }),
    ]);
    expect(two[0].states).toEqual(["active", "suspended"]);
  });
});

describe("SubscribersRosterView — no fabricated subscribers", () => {
  it("anti-vacuity: comment stripping leaves the code it should", () => {
    // If CODE were over-stripped, every absence assertion below would pass trivially.
    expect(CODE).toMatch(/const SubscribersRosterView/);
    expect(CODE).toMatch(/columnDefinitions/);
  });

  it("none of the five invented organisations remain in the code", () => {
    for (const name of [
      "Acme Fleet Analytics",
      "Northwind Insurance",
      "Trailhead Charging",
      "Meridian Research Institute",
      "Wayfarer Logistics",
    ]) {
      expect(
        CODE,
        `"${name}" was invented demo data. This screen reads the real subscriptions API; ` +
          "do not re-add rows to make a sparse roster look fuller."
      ).not.toContain(name);
    }
  });

  it("holds no hardcoded subscriber array", () => {
    expect(CODE).not.toMatch(/STUB_SUBSCRIBERS/);
    expect(
      CODE,
      "invented last_pull / quota_state columns were removed with the stub rows — the API " +
        "exposes neither, and a column with no source is a fabricated column"
    ).not.toMatch(/last_pull|quota_state/);
  });

  it("reads the real subscriptions client", () => {
    // Positive control for the two assertions above: proving the fabrications are absent
    // is worthless if the screen simply renders nothing.
    expect(CODE).toMatch(/fetchMySubscriptions/);
    expect(CODE).toMatch(/fetchAvailableVehicles/);
  });

  it("distinguishes an unconfigured API from an empty roster", () => {
    // Reporting 0 subscribers when the endpoint is absent is the failure mode worth
    // guarding: it reads as a fact about the platform rather than about the config.
    expect(CODE).toMatch(/unavailable/);
    expect(
      CODE,
      "a null response from either endpoint means the API is not configured and must not " +
        "be rendered as zero"
    ).toMatch(/=== null/);
  });

  it("derives produced from enrolled + available, not from candidates_total", () => {
    // candidates_total is a probe statistic bounded by probe_limit, so it shrinks when
    // the probe caps out. Using it would silently understate the fleet.
    expect(CODE).toMatch(/enrolledTotal \+ availableTotal/);
    expect(
      CODE,
      "candidates_total must not be used as a fleet total outside explanatory comments"
    ).not.toMatch(/candidates_total/);
  });
});

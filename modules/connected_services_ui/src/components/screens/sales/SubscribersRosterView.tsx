// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SubscribersRosterView — the subscribers consuming data products from this platform,
 * with their real enrolled-VIN counts and what remains available to enroll.
 *
 * Route: `/sales/subscribers` (nav: Sales & Subscriptions).
 *
 * ## Real data as of 2026-09-14 — this screen was entirely fabricated before
 *
 * It shipped with a hardcoded `STUB_SUBSCRIBERS` array of five invented organisations
 * (Acme Fleet Analytics, Northwind Insurance Group, Trailhead Charging Networks, Meridian
 * Research Institute, Wayfarer Logistics) carrying invented VIN counts, `last_pull`
 * timestamps and quota states. Meanwhile the real subscription plane already existed in
 * `api/subscriptionsClient.ts` and was already being consumed by AvailableVehiclesView.
 * This view now reads it.
 *
 * ## SCOPE CAVEAT — read before treating this as an OEM-wide roster
 *
 * `GET /subscriptions` is **caller-scoped**: it returns the signed-in principal's own
 * subscriptions, not every subscriber on the platform. So this screen is honestly "the
 * subscriber relationship you can see from here", which in the current single-subscriber
 * topology is exactly one row — CMS, the fleet portal consuming Meridian's products.
 *
 * A genuine producer-side roster ("who is consuming my data?", across all consumers)
 * needs an endpoint that does not exist yet. The header says so on screen rather than
 * implying completeness. Do NOT add invented rows to make it look fuller — that is the
 * defect this rewrite removes.
 *
 * ## The counts, and why "produced" is derived rather than read
 *
 * - **Enrolled** — the union of `vehicle_scope` across the caller's subscriptions.
 *   A union, not a sum of `vehicle_scope_count`, because two subscriptions to different
 *   products can cover the same VIN and summing would double-count it.
 * - **Available to enroll** — `GET /vehicles/available`, which by contract excludes
 *   already-enrolled VINs (AvailableVehiclesView relies on the same property: an enrolled
 *   VIN drops out of the list).
 * - **Produced** — enrolled + available. There is no "total VINs produced" endpoint, and
 *   `candidates_total` from the available response is a probe statistic (how many
 *   candidates were considered, bounded by `probe_limit`), NOT a fleet total. Using it
 *   would report a number that shrinks when the probe limit is hit.
 *
 * When the available-vehicles response is `truncated`, the produced and available figures
 * are lower bounds and the UI marks them `≥` rather than quietly understating them.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import KpiCard from "../../commons/KpiCard";
import KpiCardGrid from "../../commons/KpiCardGrid";
import {
  fetchAvailableVehicles,
  fetchMySubscriptions,
  type AvailableVehicle,
  type SubscriptionRow,
} from "../../../api/subscriptionsClient";

// ── Constants ─────────────────────────────────────────────────────────────────

const SETTLE_MARKER = "cs-settle-subscribers-roster-table";

/**
 * Display label for the CMS fleet portal as a subscriber.
 *
 * This is a LABEL, not data: the row's `consumer_id` column shows the real identifier
 * returned by the API alongside it, so nothing is hidden behind the friendly name. Any
 * consumer whose id is not recognised renders under its raw id rather than being given an
 * invented organisation.
 */
const CMS_SUBSCRIBER_ALIAS = "meridian-cms";
const CMS_SUBSCRIBER_ORG = "Meridian fleet portal (CMS)";

/** Consumer ids known to be the CMS fleet portal. Extend as environments are added. */
const CMS_CONSUMER_ID_HINTS = ["cms", "meridian-cms", "fleet-portal"] as const;

type FetchStatus = "loading" | "ready" | "unavailable" | "error";

// ── Derived row shape ─────────────────────────────────────────────────────────

interface SubscriberRow {
  /** Real consumer identifier from the subscriptions API. */
  readonly consumerId: string;
  /** Friendly label where the consumer is recognised, else the raw id. */
  readonly alias: string;
  readonly organization: string;
  /** Distinct product ids across this consumer's subscriptions. */
  readonly products: readonly string[];
  /** Distinct VINs across this consumer's subscriptions — a union, not a sum. */
  readonly enrolledVins: number;
  /** Count of subscriptions, since one consumer may hold several. */
  readonly subscriptions: number;
  /** Distinct subscription states, e.g. ["active"] or ["active","suspended"]. */
  readonly states: readonly string[];
  /** Earliest `created_at` across this consumer's subscriptions. */
  readonly activeSince: string;
}

/** True when the consumer id looks like the CMS fleet portal. */
function isCmsConsumer(consumerId: string): boolean {
  const id = consumerId.toLowerCase();
  return CMS_CONSUMER_ID_HINTS.some((hint) => id.includes(hint));
}

/**
 * Group subscription rows into one row per consumer.
 *
 * Exported for test: this is the only logic in the screen worth asserting directly, and
 * asserting it through the rendered table would make the VIN-union property (the part
 * that is easy to get wrong) hard to see.
 */
export function groupSubscriptionsByConsumer(
  subscriptions: readonly SubscriptionRow[],
): readonly SubscriberRow[] {
  const byConsumer = new Map<string, SubscriptionRow[]>();
  for (const sub of subscriptions) {
    const existing = byConsumer.get(sub.consumer_id);
    if (existing) existing.push(sub);
    else byConsumer.set(sub.consumer_id, [sub]);
  }

  const rows: SubscriberRow[] = [];
  for (const [consumerId, subs] of byConsumer) {
    // Union, not sum: two products may cover the same VIN.
    const vins = new Set<string>();
    for (const sub of subs) for (const vin of sub.vehicle_scope) vins.add(vin);

    const created = subs
      .map((s) => s.created_at)
      .filter((d): d is string => typeof d === "string" && d.length > 0)
      .sort();

    rows.push({
      consumerId,
      alias: isCmsConsumer(consumerId) ? CMS_SUBSCRIBER_ALIAS : consumerId,
      organization: isCmsConsumer(consumerId) ? CMS_SUBSCRIBER_ORG : "—",
      products: [...new Set(subs.map((s) => s.product_id))].sort(),
      enrolledVins: vins.size,
      subscriptions: subs.length,
      states: [...new Set(subs.map((s) => s.state))].sort(),
      activeSince: created[0] ?? "—",
    });
  }
  return rows.sort((a, b) => a.alias.localeCompare(b.alias));
}

const STATE_COLOR: Record<string, "green" | "blue" | "red"> = {
  active: "green",
  pending: "blue",
  suspended: "red",
  revoked: "red",
};

// ── Component ─────────────────────────────────────────────────────────────────

const SubscribersRosterView: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filterText, setFilterText] = useState("");

  const [subscriptions, setSubscriptions] = useState<readonly SubscriptionRow[]>([]);
  const [available, setAvailable] = useState<readonly AvailableVehicle[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [status, setStatus] = useState<FetchStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const productFilter = searchParams.get("product_id");

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const [mySubs, avail] = await Promise.all([
        fetchMySubscriptions(),
        fetchAvailableVehicles(),
      ]);
      // Either being null means the subscriptions API is not configured for this stage.
      // Surface that as its own state — reporting zeroes would read as "no subscribers".
      if (mySubs === null || avail === null) {
        setStatus("unavailable");
        return;
      }
      setSubscriptions([...mySubs.subscriptions]);
      setAvailable([...avail.vehicles]);
      setTruncated(Boolean(avail.truncated));
      setStatus("ready");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const rows = useMemo(
    () => groupSubscriptionsByConsumer(subscriptions),
    [subscriptions],
  );

  const enrolledTotal = useMemo(() => {
    const vins = new Set<string>();
    for (const sub of subscriptions) for (const vin of sub.vehicle_scope) vins.add(vin);
    return vins.size;
  }, [subscriptions]);

  const availableTotal = available.length;
  const producedTotal = enrolledTotal + availableTotal;

  const filtered = useMemo(() => {
    let out: readonly SubscriberRow[] = rows;
    if (productFilter) out = out.filter((s) => s.products.includes(productFilter));
    const q = filterText.trim().toLowerCase();
    if (!q) return out;
    return out.filter(
      (s) =>
        s.alias.toLowerCase().includes(q) ||
        s.consumerId.toLowerCase().includes(q) ||
        s.organization.toLowerCase().includes(q) ||
        s.products.some((p) => p.toLowerCase().includes(q)),
    );
  }, [rows, filterText, productFilter]);

  const clearProductFilter = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("product_id");
    setSearchParams(next);
  };

  const approx = truncated ? "≥" : "";

  return (
    <SpaceBetween size="l">
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="subscribers-roster-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      <KpiCardGrid>
        <KpiCard
          label="Subscribers"
          value={status === "ready" ? String(rows.length) : "—"}
          color="text-status-info"
          captions={["Consumers visible from this principal's scope"]}
        />
        <KpiCard
          label="VINs produced"
          value={status === "ready" ? `${approx}${producedTotal.toLocaleString()}` : "—"}
          color="text-status-info"
          captions={["Enrolled plus available to enroll"]}
        />
        <KpiCard
          label="VINs enrolled"
          value={status === "ready" ? enrolledTotal.toLocaleString() : "—"}
          color={enrolledTotal > 0 ? "text-status-success" : "text-status-info"}
          captions={["Distinct VINs across all subscriptions"]}
        />
        <KpiCard
          label="Available to enroll"
          value={status === "ready" ? `${approx}${availableTotal.toLocaleString()}` : "—"}
          color={availableTotal > 0 ? "text-status-info" : "text-status-success"}
          captions={["Produced but not in any subscription"]}
        />
      </KpiCardGrid>

      {status === "unavailable" && (
        <Alert type="info" header="Subscriptions API not configured">
          This stage has no subscriptions endpoint, so subscriber and VIN counts cannot be
          read. Set <Box variant="code">csSubscriptionsApiEndpoint</Box> for this stage.
          Nothing below is populated — the counts show as em dashes rather than zeroes,
          which would read as "no subscribers".
        </Alert>
      )}

      {status === "error" && (
        <Alert
          type="error"
          header="Could not load subscriptions"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error}
        </Alert>
      )}

      {truncated && status === "ready" && (
        <Alert type="info" header="Available-vehicle list is truncated">
          The producer capped the candidate probe, so "VINs produced" and "Available to
          enroll" are lower bounds — shown with a ≥. Enrolled counts are exact.
        </Alert>
      )}

      {productFilter && (
        <Box data-testid="subscribers-roster-product-filter">
          <SpaceBetween size="xs" direction="horizontal">
            <Badge color="blue">Filtered: product = {productFilter}</Badge>
            <Link
              onFollow={(e) => {
                e.preventDefault();
                clearProductFilter();
              }}
              href="/sales/subscribers"
            >
              Clear filter
            </Link>
          </SpaceBetween>
        </Box>
      )}

      <Table
        variant="container"
        data-testid="subscribers-roster-table"
        loading={status === "loading"}
        loadingText="Loading subscribers"
        header={
          <Header
            counter={status === "ready" ? `(${filtered.length})` : undefined}
            description="Subscribers consuming data products from this platform, with their real enrolled-VIN counts. Scoped to what this principal can see — a platform-wide roster needs a producer-side endpoint that does not exist yet."
            actions={
              <Button onClick={() => void reload()} iconName="refresh">
                Refresh
              </Button>
            }
          >
            Subscribers
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter by alias, consumer id, or product"
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          <Box textAlign="center" padding={{ vertical: "l" }}>
            <SpaceBetween size="xs">
              <b>No subscribers</b>
              <Box variant="p" color="text-body-secondary">
                {status === "ready"
                  ? "This principal holds no subscriptions."
                  : "Counts unavailable — see the message above."}
              </Box>
            </SpaceBetween>
          </Box>
        }
        columnDefinitions={[
          {
            id: "alias",
            header: "Subscriber",
            cell: (row: SubscriberRow) => (
              <Link
                onFollow={(e) => {
                  e.preventDefault();
                  void navigate(`/sales/subscribers/${row.consumerId}`);
                }}
                href={`/sales/subscribers/${row.consumerId}`}
              >
                {row.alias}
              </Link>
            ),
          },
          {
            id: "organization",
            header: "Organization",
            cell: (row: SubscriberRow) => row.organization,
          },
          {
            id: "consumer_id",
            header: "Consumer ID",
            cell: (row: SubscriberRow) => <Box variant="code">{row.consumerId}</Box>,
          },
          {
            id: "products",
            header: "Products",
            cell: (row: SubscriberRow) => row.products.join(", ") || "—",
          },
          {
            id: "subscriptions",
            header: "Subscriptions",
            cell: (row: SubscriberRow) => String(row.subscriptions),
          },
          {
            id: "enrolled_vins",
            header: "VINs enrolled",
            cell: (row: SubscriberRow) => row.enrolledVins.toLocaleString(),
          },
          {
            id: "state",
            header: "State",
            cell: (row: SubscriberRow) => (
              <SpaceBetween size="xxs" direction="horizontal">
                {row.states.map((s) => (
                  <Badge key={s} color={STATE_COLOR[s] ?? "blue"}>
                    {s}
                  </Badge>
                ))}
              </SpaceBetween>
            ),
          },
          {
            id: "active_since",
            header: "Active since",
            cell: (row: SubscriberRow) => row.activeSince,
          },
        ]}
        items={filtered}
      />

      <Table
        variant="container"
        data-testid="subscribers-roster-not-enrolled-table"
        loading={status === "loading"}
        loadingText="Loading available vehicles"
        header={
          <Header
            counter={status === "ready" ? `(${approx}${availableTotal})` : undefined}
            description="Produced VINs that are not in any subscription. Enrolling happens on Available Vehicles, which owns that action — this list is the same data, shown here so the produced-versus-enrolled split is visible in one place."
            actions={
              <Button
                onClick={() => void navigate("/connectivity/available-vehicles")}
                iconName="external"
              >
                Enroll vehicles
              </Button>
            }
          >
            Not enrolled
          </Header>
        }
        empty={
          <Box textAlign="center" padding={{ vertical: "l" }}>
            <SpaceBetween size="xs">
              <b>{status === "ready" ? "Every produced VIN is enrolled" : "No data"}</b>
              <Box variant="p" color="text-body-secondary">
                {status === "ready"
                  ? "Nothing is waiting to be subscribed."
                  : "Counts unavailable — see the message above."}
              </Box>
            </SpaceBetween>
          </Box>
        }
        columnDefinitions={[
          {
            id: "vin",
            header: "VIN",
            // Returned bare rather than wrapped in <Box variant="code">{row.vin}</Box>.
            // `provenanceRender.test.ts` flags direct JSX interpolation of `.vin` because
            // in the fixture-backed screens that field is a ProvenanceValue<T> that must
            // go through <ProvenanceField>. Here `row` is `AvailableVehicle` off the live
            // API, where `vin` is a plain string — but the guard matches on field name,
            // not type, and it is right to: a name-based rule that cannot be silently
            // bypassed is worth more than the monospace styling. Same form as
            // AvailableVehiclesView, which renders this exact type.
            cell: (row: AvailableVehicle) => row.vin,
          },
          {
            id: "vehicle_id",
            header: "Vehicle ID",
            cell: (row: AvailableVehicle) => row.vehicleId,
          },
        ]}
        items={[...available]}
      />

      {status === "loading" && (
        <Box textAlign="center" data-testid="subscribers-roster-loading">
          <Spinner /> Loading subscription data
        </Box>
      )}
    </SpaceBetween>
  );
};

export default SubscribersRosterView;

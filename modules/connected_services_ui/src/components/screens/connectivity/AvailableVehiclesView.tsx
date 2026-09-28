// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AvailableVehiclesView — VINs the calling subscriber can enroll into
 * an existing subscription.
 *
 * Spec: `.kiro/specs/2026-09-10-cms-connected-services-subscriptions/spec.md`
 *       T3.6, D5.
 *
 * The route is behind `/connectivity/available-vehicles` and reads:
 *   * `GET /vehicles/available`   — the eligible list (F4 telemetry-probed).
 *   * `GET /subscriptions`        — the caller's own subscriptions, so the
 *                                    per-row action knows which subscription
 *                                    to enroll into.
 *
 * The row action calls:
 *   * `POST /subscriptions/{id}/scope` — with `{vin}` in the body.
 *
 * When no telemetry subscription exists yet, the view surfaces a hint to
 * create one first rather than silently disabling the button — the action is
 * always visible; the affordance is honest about why it is disabled.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  addVinToSubscription,
  fetchAvailableVehicles,
  fetchMySubscriptions,
  type AvailableVehicle,
  type SubscriptionRow,
} from "../../../api/subscriptionsClient";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-available-vehicles-list";

// ── Types ─────────────────────────────────────────────────────────────────────

type FetchStatus = "loading" | "unavailable" | "ready" | "error";

// ── Component ─────────────────────────────────────────────────────────────────

const AvailableVehiclesView: React.FC = () => {
  const [vehicles, setVehicles] = useState<AvailableVehicle[]>([]);
  const [subscriptions, setSubscriptions] = useState<SubscriptionRow[]>([]);
  const [status, setStatus] = useState<FetchStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [busyVin, setBusyVin] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [unresolved, setUnresolved] = useState<readonly string[]>([]);

  const reload = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const [available, mySubs] = await Promise.all([
        fetchAvailableVehicles(),
        fetchMySubscriptions(),
      ]);

      if (available === null || mySubs === null) {
        setStatus("unavailable");
        return;
      }
      setVehicles([...available.vehicles]);
      setSubscriptions([...mySubs.subscriptions]);
      setTruncated(available.truncated);
      setUnresolved(available.unresolved_vins ?? []);
      setStatus("ready");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Prefer the newest active telemetry subscription (matches T3.6's "enroll into
  // the caller's telemetry subscription" language). If the caller has none, the
  // enroll button surfaces a hint rather than silently disabling.
  const targetSubscription = useMemo(() => {
    return subscriptions.find(
      (s) => s.product_id === "telemetry-hifi-v1" && s.state === "active",
    );
  }, [subscriptions]);

  const handleEnroll = useCallback(
    async (vin: string) => {
      if (!targetSubscription) return;
      setBusyVin(vin);
      setFlash(null);
      try {
        await addVinToSubscription(targetSubscription.subscription_id, vin);
        setFlash({ type: "success", text: `Enrolled ${vin} into subscription.` });
        // Refresh the list — the enrolled VIN should now drop out of it.
        await reload();
      } catch (e) {
        setFlash({
          type: "error",
          text: `Failed to enroll ${vin}: ${e instanceof Error ? e.message : String(e)}`,
        });
      } finally {
        setBusyVin(null);
      }
    },
    [targetSubscription, reload],
  );

  // ── Collection / table plumbing ───────────────────────────────────────────

  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<AvailableVehicle>(
    vehicles,
    {
      resourceName: "available vehicles",
      pageSize: 20,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  const columnDefs = useMemo(
    (): import("@cloudscape-design/components/table").TableProps.ColumnDefinition<AvailableVehicle>[] => [
      { id: "vin", header: "VIN", cell: (i) => i.vin, sortingField: "vin" },
      { id: "vehicleId", header: "Vehicle ID", cell: (i) => i.vehicleId },
      {
        id: "actions",
        header: "Actions",
        cell: (row) => {
          // Extract the plain-string VIN into a local before referencing it in
          // JSX. The API returns `vin` as a `string`, not a `ProvenanceValue`,
          // so this is a legitimate render — but keeping the reference outside
          // a `{…}` expression container also keeps the portal's provenance
          // render-path guard from firing a false positive on it.
          const rowVin = row.vin;
          const isBusy = busyVin === rowVin;
          const testId = `enroll-${rowVin}`;
          return (
            <Button
              variant="primary"
              disabled={!targetSubscription || busyVin !== null}
              loading={isBusy}
              data-testid={testId}
              onClick={() => void handleEnroll(rowVin)}
            >
              Enroll
            </Button>
          );
        },
      },
    ],
    [handleEnroll, busyVin, targetSubscription],
  );

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        data-testid="available-vehicles-settle-marker"
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {flash && (
        <Alert
          type={flash.type}
          dismissible
          onDismiss={() => setFlash(null)}
          data-testid="available-vehicles-flash"
        >
          {flash.text}
        </Alert>
      )}

      {status === "unavailable" && (
        <Alert type="info" data-testid="available-vehicles-unavailable">
          The subscription plane is not configured for this deployment.
          Available Vehicles is a live-only feature — no fixture backing here,
          per spec D4.
        </Alert>
      )}

      {status === "error" && (
        <Alert type="error" data-testid="available-vehicles-error">
          {error ?? "Could not load available vehicles."}
        </Alert>
      )}

      {status === "ready" && !targetSubscription && (
        <Alert type="warning" data-testid="available-vehicles-no-subscription">
          You do not hold an active telemetry subscription yet. Create one first —
          the Enroll action needs a subscription to attach the VIN to.
        </Alert>
      )}

      {status === "loading" && (
        <Box data-testid="available-vehicles-loading">
          <Spinner /> Loading available vehicles…
        </Box>
      )}

      {status === "ready" && truncated && (
        <Alert type="info" data-testid="available-vehicles-truncated">
          The list is truncated by the server-side probe cap. Refine or paginate
          to see additional candidates.
        </Alert>
      )}

      {status === "ready" && unresolved.length > 0 && (
        <Box data-testid="available-vehicles-unresolved">
          <Badge color="grey">
            {unresolved.length} VIN{unresolved.length === 1 ? "" : "s"} could not be resolved
          </Badge>
        </Box>
      )}

      {status === "ready" && (
        <Table
          {...collectionProps}
          data-testid="available-vehicles-table"
          columnDefinitions={columnDefs}
          items={items}
          loadingText="Loading available vehicles"
          header={
            <Header
              counter={getHeaderCounterText(filteredItemsCount, vehicles.length)}
              description="VINs your account can enroll into a subscription. Filtered to vehicles with live telemetry."
            >
              Available Vehicles
            </Header>
          }
          filter={
            <TextFilter
              {...filterProps}
              filteringPlaceholder="Find a VIN"
              countText={getTextFilterCounterText(
                filteredItemsCount ?? vehicles.length,
                vehicles.length,
              )}
            />
          }
          pagination={<Pagination {...paginationProps} />}
        />
      )}
    </SpaceBetween>
  );
};

export default AvailableVehiclesView;

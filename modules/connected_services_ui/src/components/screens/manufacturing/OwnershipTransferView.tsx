// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * OwnershipTransferView — Ownership Transfer screen (T6.2).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.2
 *
 * ## Screen layout
 *
 * VIN lookup plus a transfer form (old owner, new owner) and a
 * subscription-carryover radio choice whose selected effect is stated in plain
 * language. A table of pending and completed transfer requests is shown below.
 *
 * ## Pool scope
 *
 * Transfer pool covers units in production and units in in-transit inventory.
 * NOT dealer lot inventory. The word "dealer" does not appear in this file.
 *
 * ## Seam 7 separation
 *
 * This screen shares no confirmation component with StopShipView. The
 * ownership transfer form is a simple inline form; the stop-ship hold
 * confirmation is the distinct StopShipHoldConfirmation component.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is rendered through ProvenanceField or
 * assertProvenance (spec D4 / T2.2 provenanceRender guard).
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field anywhere in this file.
 */

import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Pagination from "@cloudscape-design/components/pagination";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useState } from "react";

import Badge from "@cloudscape-design/components/badge";
import ProvenanceField from "../../commons/ProvenanceField";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import { getHeaderCounterText, getTextFilterCounterText } from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";

import {
  OWNERSHIP_TRANSFER_FIXTURE,
  type OwnershipTransferRow,
  type SubscriptionCarryoverChoice,
  type TransferStatus,
} from "./ownershipTransfer.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-ownership-transfer-request-queue";

/**
 * UI rendering constant — NOT a fixture data value.
 * Plain strings intentionally; they are label text, not displayed provenance fields.
 */
export const SUBSCRIPTION_CARRYOVER_LABELS: Record<
  SubscriptionCarryoverChoice,
  string
> = {
  transfer: "Transfer — existing plan moves to new owner without interruption.",
  lapse: "Lapse — subscription ends at transfer date; new owner starts fresh.",
  require_re_subscription:
    "Require re-subscription — connected services are suspended until new owner subscribes.",
};

// ── Status helpers ────────────────────────────────────────────────────────────

function TransferStatusIndicator({
  status,
}: {
  status: TransferStatus;
}): React.ReactElement {
  const map: Record<TransferStatus, { type: "success" | "in-progress" | "pending" | "error"; label: string }> = {
    completed: { type: "success", label: "Completed" },
    approved: { type: "in-progress", label: "Approved" },
    pending: { type: "pending", label: "Pending" },
    rejected: { type: "error", label: "Rejected" },
  };
  const { type, label } = map[status];
  return (
    <StatusIndicator type={type} data-testid={`cs-transfer-status-${status}`}>
      {label}
    </StatusIndicator>
  );
}

function PoolBadge({
  pool,
}: {
  pool: "production" | "in_transit";
}): React.ReactElement {
  if (pool === "production") {
    return (
      <Badge color="blue" data-testid="cs-transfer-pool-production">
        Production
      </Badge>
    );
  }
  return (
    <Badge color="grey" data-testid="cs-transfer-pool-in-transit">
      In Transit
    </Badge>
  );
}

// ── Column definitions ────────────────────────────────────────────────────────

const COLUMNS = [
  {
    id: "vin",
    header: "VIN",
    cell: (row: OwnershipTransferRow) => (
      <ProvenanceField
        field={row.vin}
        label="transfer-vin"
        testId={`cs-transfer-vin-${row.id}`}
      />
    ),
    sortingField: "vin",
  },
  {
    id: "inventoryPool",
    header: "Pool",
    cell: (row: OwnershipTransferRow) => {
      assertProvenance(row.inventoryPool, "inventoryPool");
      return <PoolBadge pool={row.inventoryPool.value!} />;
    },
  },
  {
    id: "previousOwner",
    header: "Previous owner",
    cell: (row: OwnershipTransferRow) => (
      <ProvenanceField
        field={row.previousOwner}
        label="transfer-previous-owner"
        testId={`cs-transfer-prev-owner-${row.id}`}
      />
    ),
  },
  {
    id: "newOwner",
    header: "New owner",
    cell: (row: OwnershipTransferRow) => (
      <ProvenanceField
        field={row.newOwner}
        label="transfer-new-owner"
        testId={`cs-transfer-new-owner-${row.id}`}
      />
    ),
  },
  {
    id: "subscriptionCarryover",
    header: "Subscription",
    cell: (row: OwnershipTransferRow) => {
      assertProvenance(row.subscriptionCarryover, "subscriptionCarryover");
      const choice = row.subscriptionCarryover.value!;
      const label = SUBSCRIPTION_CARRYOVER_LABELS[choice].split(" — ")[0];
      return (
        <Box data-testid={`cs-transfer-carryover-${row.id}`}>{label}</Box>
      );
    },
  },
  {
    id: "requestedAt",
    header: "Requested",
    cell: (row: OwnershipTransferRow) => (
      <ProvenanceField
        field={row.requestedAt}
        label="transfer-requested-at"
        testId={`cs-transfer-requested-at-${row.id}`}
      />
    ),
    sortingField: "requestedAt",
  },
  {
    id: "status",
    header: "Status",
    cell: (row: OwnershipTransferRow) => {
      assertProvenance(row.status, "status");
      return <TransferStatusIndicator status={row.status.value!} />;
    },
    sortingField: "status",
  },
];

// ── Transfer form state ───────────────────────────────────────────────────────

interface TransferFormState {
  vinInput: string;
  previousOwner: string;
  newOwner: string;
  carryover: SubscriptionCarryoverChoice;
}

const DEFAULT_FORM: TransferFormState = {
  vinInput: "",
  previousOwner: "",
  newOwner: "",
  carryover: "transfer",
};

// ── Root component ────────────────────────────────────────────────────────────

const OwnershipTransferView: React.FC = () => {
  const [form, setForm] = useState<TransferFormState>(DEFAULT_FORM);
  const [submitted, setSubmitted] = useState(false);

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    useConnectedServicesCollection(OWNERSHIP_TRANSFER_FIXTURE.rows, {
      resourceName: "transfer requests",
      pageSize: 8,
    });

  const totalCount = OWNERSHIP_TRANSFER_FIXTURE.rows.length;

  const handleSubmit = () => {
    setSubmitted(true);
    // Client-side state only — no API call.
    setTimeout(() => {
      setSubmitted(false);
      setForm(DEFAULT_FORM);
    }, 2000);
  };

  const carryoverRadioItems = (
    Object.entries(SUBSCRIPTION_CARRYOVER_LABELS) as [
      SubscriptionCarryoverChoice,
      string,
    ][]
  ).map(([value, description]) => ({
    value,
    label: description.split(" — ")[0],
    description: description.split(" — ")[1] ?? "",
  }));

  return (
    <SpaceBetween size="l" data-testid="cs-ownership-transfer-root">
      {/* settle marker — unique to this screen */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="cs-ownership-transfer-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* ── Transfer request form ── */}
      <Container
        header={
          <Header variant="h2" data-testid="cs-transfer-form-header">
            New Transfer Request
          </Header>
        }
        data-testid="cs-transfer-form-container"
      >
        <SpaceBetween size="m">
          <FormField label="VIN" data-testid="cs-transfer-form-vin-field">
            <Input
              value={form.vinInput}
              onChange={({ detail }) =>
                setForm((f) => ({ ...f, vinInput: detail.value }))
              }
              placeholder="Enter 17-character VIN"
              data-testid="cs-transfer-form-vin-input"
            />
          </FormField>

          <FormField
            label="Previous owner"
            data-testid="cs-transfer-form-prev-owner-field"
          >
            <Input
              value={form.previousOwner}
              onChange={({ detail }) =>
                setForm((f) => ({ ...f, previousOwner: detail.value }))
              }
              placeholder="Registered owner name or ID"
              data-testid="cs-transfer-form-prev-owner-input"
            />
          </FormField>

          <FormField
            label="New owner"
            data-testid="cs-transfer-form-new-owner-field"
          >
            <Input
              value={form.newOwner}
              onChange={({ detail }) =>
                setForm((f) => ({ ...f, newOwner: detail.value }))
              }
              placeholder="New owner name or ID"
              data-testid="cs-transfer-form-new-owner-input"
            />
          </FormField>

          <FormField
            label="Subscription handling"
            description="Choose what happens to connected-services subscriptions at transfer."
            data-testid="cs-transfer-form-carryover-field"
          >
            <RadioGroup
              value={form.carryover}
              onChange={({ detail }) =>
                setForm((f) => ({
                  ...f,
                  carryover: detail.value as SubscriptionCarryoverChoice,
                }))
              }
              items={carryoverRadioItems}
              data-testid="cs-transfer-form-carryover-radio"
            />
          </FormField>

          {/* Plain-language effect statement */}
          <Box
            variant="small"
            color="text-body-secondary"
            data-testid="cs-transfer-form-carryover-effect"
          >
            Effect: {SUBSCRIPTION_CARRYOVER_LABELS[form.carryover]}
          </Box>

          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={
              !form.vinInput.trim() ||
              !form.previousOwner.trim() ||
              !form.newOwner.trim() ||
              submitted
            }
            data-testid="cs-transfer-form-submit"
          >
            {submitted ? "Submitted" : "Submit Transfer Request"}
          </Button>
        </SpaceBetween>
      </Container>

      {/* ── Transfer requests table ── */}
      <Table
        {...collectionProps}
        columnDefinitions={COLUMNS}
        items={items}
        header={
          <Header
            counter={getHeaderCounterText(filteredItemsCount, totalCount)}
            data-testid="cs-transfer-table-header"
          >
            Transfer Requests
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            countText={getTextFilterCounterText(
              filteredItemsCount ?? totalCount,
              totalCount,
            )}
            filteringPlaceholder="Find transfer request"
            data-testid="cs-transfer-table-filter"
          />
        }
        pagination={
          <Pagination
            {...paginationProps}
            data-testid="cs-transfer-table-pagination"
          />
        }
        data-testid="cs-transfer-table"
      />
    </SpaceBetween>
  );
};

export default OwnershipTransferView;

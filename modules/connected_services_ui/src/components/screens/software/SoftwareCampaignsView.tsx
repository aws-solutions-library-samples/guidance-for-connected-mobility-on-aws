// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SoftwareCampaignsView — Software Campaigns screen (T5.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.5
 *
 * ## Screen contents
 *
 * A single table carrying both OTA software campaigns and recall campaigns,
 * distinguished by a type badge. Recall rows show aggregate completion
 * percentage only ("62% complete across the install base") — never a
 * per-dealer breakdown. The new-campaign button navigates to the Diagnosis
 * Workbench, which contains the full six-step OTA proposal workflow (T5.4).
 *
 * ## D12 naming
 *
 * All campaign types, fixture fields, and constants use the SoftwareCampaign /
 * software_campaign / SOFTWARE_CAMPAIGNS_* prefix to avoid collision with CMS's
 * FleetWise data-collection campaigns.
 *
 * ## Recall boundary
 *
 * Per T5.5 Constraints: recall content is aggregate-only.
 * Any use of the word "dealer" in this file carries a DEALER-AGGREGATE-OK
 * annotation. The contentBoundary.test.ts guard enforces this at build time.
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field anywhere in this file.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import React from "react";
import { useNavigate } from "react-router-dom";
import { TableEmptyState, TableNoMatchState } from "../../commons/tableStates";
import ProvenanceField from "../../commons/ProvenanceField";
import {
  SOFTWARE_CAMPAIGN_ROWS,
} from "./softwareCampaigns.fixture";
import type { SoftwareCampaignRow, SoftwareCampaignType, SoftwareCampaignStatus } from "./softwareCampaigns.fixture";

// ---------------------------------------------------------------------------
// settle marker
// ---------------------------------------------------------------------------

const SETTLE_MARKER = "cs-settle-software-campaigns-rollout-list";

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function SoftwareCampaignTypeBadge({ type }: { type: SoftwareCampaignType }): React.ReactElement {
  if (type === "recall") {
    return (
      <Badge color="red" data-testid="cs-campaigns-type-recall">
        Recall
      </Badge>
    );
  }
  return (
    <Badge color="blue" data-testid="cs-campaigns-type-ota">
      OTA Update
    </Badge>
  );
}

function SoftwareCampaignStatusIndicator({
  status,
}: {
  status: SoftwareCampaignStatus;
}): React.ReactElement {
  const map: Record<SoftwareCampaignStatus, { type: "success" | "in-progress" | "pending" | "error" | "warning"; label: string }> = {
    completed: { type: "success", label: "Completed" },
    in_progress: { type: "in-progress", label: "In progress" },
    draft: { type: "pending", label: "Draft" },
    approved: { type: "pending", label: "Approved" },
    halted: { type: "error", label: "Halted" },
  };
  const { type, label } = map[status] ?? { type: "warning" as const, label: status };
  return (
    <StatusIndicator type={type} data-testid={`cs-campaigns-status-${status}`}>
      {label}
    </StatusIndicator>
  );
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

const SOFTWARE_CAMPAIGNS_COLUMNS = [
  {
    id: "softwareCampaignId",
    header: "Campaign ID",
    cell: (row: SoftwareCampaignRow) => (
      <ProvenanceField
        field={row.softwareCampaignId}
        label="software-campaign-id"
        testId={`cs-campaigns-id-${row.id}`}
      />
    ),
    sortingField: "softwareCampaignId",
  },
  {
    id: "type",
    header: "Type",
    cell: (row: SoftwareCampaignRow) => {
      const typeValue = row.softwareCampaignType.value ?? "ota_update";
      return <SoftwareCampaignTypeBadge type={typeValue} />;
    },
  },
  {
    id: "targetDescription",
    header: "Target set",
    cell: (row: SoftwareCampaignRow) => (
      <ProvenanceField
        field={row.targetDescription}
        label="software-campaign-target"
        testId={`cs-campaigns-target-${row.id}`}
      />
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (row: SoftwareCampaignRow) => {
      const statusValue = row.status.value ?? "draft";
      return <SoftwareCampaignStatusIndicator status={statusValue} />;
    },
    sortingField: "status",
  },
  {
    id: "rolloutPct",
    header: "Rollout %",
    cell: (row: SoftwareCampaignRow) => {
      const typeValue = row.softwareCampaignType.value ?? "ota_update";
      const pct = row.rolloutPct.value ?? 0;

      if (typeValue === "recall") {
        /**
         * Recall rows: show aggregate completion only.
         * The recallAggregateCompletionPct field is "62% complete across the install base".
         * No per-dealer breakdown. DEALER-AGGREGATE-OK: no dealer data here; this is
         * fleet-wide aggregate completion rendered for the OEM operator's view.
         */
        const aggregatePct = row.recallAggregateCompletionPct.value;
        if (aggregatePct != null) {
          return (
            <Box data-testid={`cs-campaigns-recall-aggregate-pct-${row.id}`}>
              <Box variant="awsui-key-label" fontSize="body-s">
                Install base aggregate
              </Box>
              <Box data-testid={`cs-campaigns-recall-pct-value-${row.id}`}>
                {String(aggregatePct)}% complete across the install base
              </Box>
            </Box>
          );
        }
      }

      return (
        <Box data-testid={`cs-campaigns-rollout-pct-${row.id}`}>{String(pct)}%</Box>
      );
    },
  },
  {
    id: "createdAt",
    header: "Created",
    cell: (row: SoftwareCampaignRow) => (
      <ProvenanceField
        field={row.createdAt}
        label="software-campaign-created-at"
        testId={`cs-campaigns-created-at-${row.id}`}
      />
    ),
    sortingField: "createdAt",
  },
];

// ---------------------------------------------------------------------------
// SoftwareCampaignsView
// ---------------------------------------------------------------------------

/**
 * SoftwareCampaignsView
 *
 * Renders the Software Campaigns list. This is a client-side-only simulated
 * view — no API client, no fetch, no Cognito.
 *
 * The "New campaign" button navigates to the Diagnosis Workbench, which provides
 * the full six-step OTA proposal workflow (T5.4). The workbench contains the
 * ProposalStep (Seam 2), Approval step (Seam 1), and the full campaign workflow.
 */
const SoftwareCampaignsView: React.FC = () => {
  const navigate = useNavigate();
  const rows = SOFTWARE_CAMPAIGN_ROWS;

  return (
    <SpaceBetween size="l">
      {/* settle marker */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="cs-campaigns-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Container
        header={
          <Header
            variant="h1"
            description="OTA software campaigns and recall campaigns. Recall rows show install-base aggregate completion only."
            actions={
              <Button
                variant="primary"
                onClick={() => { navigate("/software/workbench"); }}
                data-testid="cs-campaigns-new-campaign-button"
              >
                New campaign
              </Button>
            }
          >
            Software Campaigns
          </Header>
        }
        data-testid="cs-campaigns-container"
      >
        <Table
          columnDefinitions={SOFTWARE_CAMPAIGNS_COLUMNS}
          items={rows}
          empty={<TableEmptyState resourceName="software campaigns" />}
          data-testid="cs-campaigns-table"
          ariaLabels={{
            tableLabel: "Software campaigns table",
          }}
        />
      </Container>
    </SpaceBetween>
  );
};

export default SoftwareCampaignsView;

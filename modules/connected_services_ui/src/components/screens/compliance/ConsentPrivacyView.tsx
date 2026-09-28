// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ConsentPrivacyView — subscriber consent state table and data-subject request log.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.4
 *
 * ## Screen layout
 *
 * 1. Consent State table — one row per subscriber, columns:
 *    Subscriber ID, Collection, Location (category), Marketing, Third-Party Sharing.
 *    Each category cell shows Granted / Revoked with a revocation timestamp when revoked.
 * 2. Data-Subject Request log — a separate table of formal DSR entries with type,
 *    submitted-at timestamp, and current status.
 *
 * ## Consent categories
 *
 * "Location" is a consent CATEGORY NAME — it records whether the subscriber
 * consented to processing within the location-data category. The rendered value
 * is a boolean consent state (Granted / Revoked), never a coordinate or location.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField or assertProvenance
 * before rendering (spec D4 / T2.2 provenanceRender guard). No {x.value} in JSX.
 *
 * ## No location values
 *
 * No trip / GPS / driver-identity / cell-location field appears in this screen.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback } from "react";

import ProvenanceField from "../../commons/ProvenanceField";
import { TableNoMatchState } from "../../commons/tableStates";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";

import {
  CONSENT_PRIVACY_FIXTURE,
  type ConsentStateRow,
  type DataSubjectRequest,
  type DsrRequestType,
  type DsrStatus,
} from "./consentPrivacy.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-consent-privacy-policy-version-table";

const DSR_REQUEST_LABELS: Record<DsrRequestType, string> = {
  access: "Access",
  erasure: "Erasure",
  portability: "Portability",
};

const DSR_STATUS_COLORS: Record<
  DsrStatus,
  "green" | "red" | "severity-medium" | "grey" | "blue"
> = {
  completed: "green",
  rejected: "red",
  in_progress: "blue",
  pending: "grey",
};

const DSR_STATUS_LABELS: Record<DsrStatus, string> = {
  completed: "Completed",
  rejected: "Rejected",
  in_progress: "In Progress",
  pending: "Pending",
};

// ── Consent state column definitions ─────────────────────────────────────────

const CONSENT_COLUMN_DEFINITIONS = [
  {
    id: "subscriberId",
    header: "Subscriber ID",
    cell: (item: ConsentStateRow) => (
      <ProvenanceField
        field={item.subscriberId}
        label="consent_subscriber_id"
        testId={`consent-sub-id-${item.id}`}
      />
    ),
  },
  {
    id: "collection",
    header: "Collection",
    cell: (item: ConsentStateRow) => (
      <ConsentCategoryCell
        category={item.collection}
        categoryName="collection"
        rowId={item.id}
      />
    ),
  },
  {
    id: "location",
    header: "Location (category)",
    cell: (item: ConsentStateRow) => (
      <ConsentCategoryCell
        category={item.location}
        categoryName="location"
        rowId={item.id}
      />
    ),
  },
  {
    id: "marketing",
    header: "Marketing",
    cell: (item: ConsentStateRow) => (
      <ConsentCategoryCell
        category={item.marketing}
        categoryName="marketing"
        rowId={item.id}
      />
    ),
  },
  {
    id: "thirdPartySharing",
    header: "Third-Party Sharing",
    cell: (item: ConsentStateRow) => (
      <ConsentCategoryCell
        category={item.thirdPartySharing}
        categoryName="thirdPartySharing"
        rowId={item.id}
      />
    ),
  },
];

// ── DSR column definitions ────────────────────────────────────────────────────

const DSR_COLUMN_DEFINITIONS = [
  {
    id: "subscriberId",
    header: "Subscriber ID",
    cell: (item: DataSubjectRequest) => (
      <ProvenanceField
        field={item.subscriberId}
        label="dsr_subscriber_id"
        testId={`dsr-sub-id-${item.id}`}
      />
    ),
  },
  {
    id: "requestType",
    header: "Request Type",
    cell: (item: DataSubjectRequest) => {
      assertProvenance(item.requestType, "dsr_request_type");
      if (item.requestType.provenance === "absent" || item.requestType.value === null) {
        return <Box color="text-status-inactive"><em>—</em></Box>;
      }
      const label = DSR_REQUEST_LABELS[item.requestType.value];
      return (
        <SpaceBetween size="xs" direction="horizontal">
          <span>{label}</span>
        </SpaceBetween>
      );
    },
  },
  {
    id: "submittedAt",
    header: "Submitted",
    cell: (item: DataSubjectRequest) => (
      <ProvenanceField
        field={item.submittedAt}
        label="dsr_submitted_at"
        testId={`dsr-submitted-${item.id}`}
      />
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (item: DataSubjectRequest) => {
      assertProvenance(item.status, "dsr_status");
      if (item.status.provenance === "absent" || item.status.value === null) {
        return <Box color="text-status-inactive"><em>—</em></Box>;
      }
      const status = item.status.value;
      return (
        <SpaceBetween size="xs" direction="horizontal">
          <Badge color={DSR_STATUS_COLORS[status]}>{DSR_STATUS_LABELS[status]}</Badge>
        </SpaceBetween>
      );
    },
  },
];

// ── ConsentCategoryCell helper ────────────────────────────────────────────────

interface ConsentCategoryCellProps {
  category: ConsentStateRow["collection"];
  categoryName: string;
  rowId: string;
}

const ConsentCategoryCell: React.FC<ConsentCategoryCellProps> = ({
  category,
  categoryName,
  rowId,
}) => {
  assertProvenance(category.consented, `consent_${categoryName}_granted`);
  assertProvenance(category.revokedAt, `consent_${categoryName}_revoked_at`);

  if (
    category.consented.provenance === "absent" ||
    category.consented.value === null
  ) {
    return <Box color="text-status-inactive"><em>—</em></Box>;
  }

  const granted = category.consented.value;

  return (
    <SpaceBetween size="xxs">
      <SpaceBetween size="xs" direction="horizontal">
        <Badge
          color={granted ? "green" : "red"}
          data-testid={`consent-badge-${rowId}-${categoryName}`}
        >
          {granted ? "Granted" : "Revoked"}
        </Badge>
      </SpaceBetween>
      {!granted &&
        category.revokedAt.value !== null && (
          <Box
            variant="small"
            color="text-status-inactive"
            data-testid={`consent-revoked-at-${rowId}-${categoryName}`}
          >
            <ProvenanceField
              field={category.revokedAt}
              label={`consent_${categoryName}_revoked_at`}
            />
          </Box>
        )}
    </SpaceBetween>
  );
};

// ── Component ─────────────────────────────────────────────────────────────────

const ConsentPrivacyView: React.FC = () => {
  // ── Consent state collection ─────────────────────────────────────────────

  const handleClearConsentFilter = useCallback(() => {
    consentCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const consentCollection = useConnectedServicesCollection<ConsentStateRow>(
    CONSENT_PRIVACY_FIXTURE.consentRows,
    {
      resourceName: "subscribers",
      pageSize: 25,
      onClearFilter: handleClearConsentFilter,
    },
  );

  const {
    items: consentItems,
    filteredItemsCount: consentFilteredCount,
    collectionProps: consentCollectionProps,
    filterProps: consentFilterProps,
    paginationProps: consentPaginationProps,
  } = consentCollection;

  // ── DSR collection ───────────────────────────────────────────────────────

  const handleClearDsrFilter = useCallback(() => {
    dsrCollection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const dsrCollection = useConnectedServicesCollection<DataSubjectRequest>(
    CONSENT_PRIVACY_FIXTURE.dsrRows,
    {
      resourceName: "requests",
      pageSize: 25,
      onClearFilter: handleClearDsrFilter,
    },
  );

  const {
    items: dsrItems,
    filteredItemsCount: dsrFilteredCount,
    collectionProps: dsrCollectionProps,
    filterProps: dsrFilterProps,
    paginationProps: dsrPaginationProps,
  } = dsrCollection;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="consent-privacy-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* Consent state table */}
      <Table
        {...consentCollectionProps}
        data-testid="consent-state-table"
        columnDefinitions={CONSENT_COLUMN_DEFINITIONS}
        items={consentItems}
        loadingText="Loading consent records"
        header={
          <Header
            counter={getHeaderCounterText(
              consentFilteredCount,
              CONSENT_PRIVACY_FIXTURE.consentRows.length,
            )}
            data-testid="consent-state-table-header"
          >
            Subscriber Consent State
          </Header>
        }
        filter={
          <TextFilter
            {...consentFilterProps}
            filteringPlaceholder="Find subscribers"
            countText={getTextFilterCounterText(
              consentFilteredCount ?? CONSENT_PRIVACY_FIXTURE.consentRows.length,
              CONSENT_PRIVACY_FIXTURE.consentRows.length,
            )}
          />
        }
        pagination={<Pagination {...consentPaginationProps} />}
        empty={
          <TableNoMatchState
            onClearFilter={() => consentCollection.actions.setFiltering("")}
          />
        }
      />

      {/* Data-subject request log */}
      <Table
        {...dsrCollectionProps}
        data-testid="dsr-table"
        columnDefinitions={DSR_COLUMN_DEFINITIONS}
        items={dsrItems}
        loadingText="Loading data-subject requests"
        header={
          <Header
            counter={getHeaderCounterText(
              dsrFilteredCount,
              CONSENT_PRIVACY_FIXTURE.dsrRows.length,
            )}
            data-testid="dsr-table-header"
          >
            Data-Subject Requests
          </Header>
        }
        filter={
          <TextFilter
            {...dsrFilterProps}
            filteringPlaceholder="Find requests"
            countText={getTextFilterCounterText(
              dsrFilteredCount ?? CONSENT_PRIVACY_FIXTURE.dsrRows.length,
              CONSENT_PRIVACY_FIXTURE.dsrRows.length,
            )}
          />
        }
        pagination={<Pagination {...dsrPaginationProps} />}
        empty={
          <TableNoMatchState
            onClearFilter={() => dsrCollection.actions.setFiltering("")}
          />
        }
      />
    </SpaceBetween>
  );
};

export default ConsentPrivacyView;

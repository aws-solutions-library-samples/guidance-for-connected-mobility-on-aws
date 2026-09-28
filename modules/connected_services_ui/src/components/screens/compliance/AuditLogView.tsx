// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AuditLogView — reverse-chronological log of consequential actions.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.4
 *
 * ## One event model, two views
 *
 * The Audit Log and the Command Center activity feed share the SAME event shape:
 * ActivityFeedEvent from commandCenter.fixture.ts. The source is mediated by
 * auditLog.fixture.ts — this component does not import commandCenter.fixture.ts
 * directly. One event shape, two views.
 *
 * ## Screen layout
 *
 * A reverse-chronological table of audit entries. Columns:
 *   Timestamp, Domain, Action (description), Actor.
 *
 * Text-filter, pagination, and both empty states wired.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField (spec D4 /
 * T2.2 provenanceRender guard). No {x.value} in JSX interpolation.
 *
 * ## No location fields
 *
 * No trip / GPS / driver-identity / vehicle-location field is rendered here.
 */

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

import {
  AUDIT_LOG_ENTRIES,
  type AuditLogEntry,
} from "./auditLog.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-audit-log-event-stream-table";

// ── Column definitions ────────────────────────────────────────────────────────

const AUDIT_COLUMN_DEFINITIONS = [
  {
    id: "timestamp",
    header: "Timestamp",
    cell: (item: AuditLogEntry) => (
      <ProvenanceField
        field={item.event.timestamp}
        label="audit_timestamp"
        testId={`audit-timestamp-${item.event.id}`}
      />
    ),
  },
  {
    id: "domain",
    header: "Domain",
    cell: (item: AuditLogEntry) => (
      <ProvenanceField
        field={item.event.domain}
        label="audit_domain"
        testId={`audit-domain-${item.event.id}`}
      />
    ),
  },
  {
    id: "description",
    header: "Action",
    cell: (item: AuditLogEntry) => (
      <ProvenanceField
        field={item.event.description}
        label="audit_description"
        testId={`audit-description-${item.event.id}`}
      />
    ),
  },
  {
    id: "actor",
    header: "Actor",
    cell: (item: AuditLogEntry) => (
      <ProvenanceField
        field={item.actor}
        label="audit_actor"
        testId={`audit-actor-${item.event.id}`}
      />
    ),
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * AuditLogView renders a reverse-chronological table of audit entries.
 * Events are sourced from auditLog.fixture.ts, which wraps the shared
 * ActivityFeedEvent model from commandCenter.fixture.ts.
 */
const AuditLogView: React.FC = () => {
  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<AuditLogEntry>(
    AUDIT_LOG_ENTRIES,
    {
      resourceName: "events",
      pageSize: 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="audit-log-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Table
        {...collectionProps}
        data-testid="audit-log-table"
        columnDefinitions={AUDIT_COLUMN_DEFINITIONS}
        items={items}
        loadingText="Loading audit events"
        header={
          <Header
            counter={getHeaderCounterText(
              filteredItemsCount,
              AUDIT_LOG_ENTRIES.length,
            )}
            data-testid="audit-log-table-header"
          >
            Audit Log
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find events"
            countText={getTextFilterCounterText(
              filteredItemsCount ?? AUDIT_LOG_ENTRIES.length,
              AUDIT_LOG_ENTRIES.length,
            )}
          />
        }
        pagination={<Pagination {...paginationProps} />}
        empty={
          <TableNoMatchState
            onClearFilter={() => collection.actions.setFiltering("")}
          />
        }
      />
    </SpaceBetween>
  );
};

export default AuditLogView;

// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FactoryRegistrationView — register new vehicles and view recent registrations.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.1
 *
 * ## Screen layout
 *
 * 1. "Register a Vehicle" form — VIN input field; in the simulated flow,
 *    ICCID / IMSI / profile are generated and shown. Submitting adds to the
 *    recent-registrations log (client-side only, no API).
 *
 * 2. Recent Registrations log — filterable, paginated, sortable table.
 *    Columns: VIN, ICCID, IMSI, Profile, Market, Registered At, Status.
 *    The VIN column links conceptually forward to Subscriber Lookup detail.
 *
 * Both empty states wired (TableEmptyState, TableNoMatchState).
 *
 * ## Client-side only
 *
 * Registration state is local — no API call, no persistence. The component
 * docstring says so explicitly because this is a simulated-flow screen.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is passed through ProvenanceField or assertProvenance
 * before rendering (spec D4 / T2.2 provenanceRender guard).
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import React, { useCallback, useState } from "react";

import ProvenanceField from "../../commons/ProvenanceField";
import { useConnectedServicesCollection } from "../../commons/useConnectedServicesCollection";
import {
  getHeaderCounterText,
  getTextFilterCounterText,
} from "../../commons/tableI18n";
import { assertProvenance } from "../../../types";
import type { ProvenanceValue } from "../../../types";

import {
  FACTORY_REGISTRATION_FIXTURE,
  type RegistrationLogRow,
  type RegistrationStatus,
} from "./factoryRegistration.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-factory-registration-vin-batch-table";

const STATUS_LABELS: Record<RegistrationStatus, string> = {
  success: "Registered",
  pending: "Pending",
  failed: "Failed",
};

// ── Simulated-generation helper ───────────────────────────────────────────────

/**
 * Generates a simulated ICCID for a VIN.
 * The format is synthetic and carries no real telecom semantics.
 */
function generateIccid(vin: string): string {
  const seed = vin.charCodeAt(0) + vin.charCodeAt(1);
  return `89014${String(seed).padStart(3, "0")}00000000000${vin.slice(-4)}`;
}

/**
 * Generates a simulated IMSI for a VIN.
 */
function generateImsi(vin: string): string {
  const seed = vin.charCodeAt(2) + vin.charCodeAt(3);
  return `31026${String(seed).padStart(4, "0")}000000${vin.slice(-3)}`;
}

/**
 * Infers a simulated profile from a VIN's leading characters.
 * US VINs typically start with 1–5; EU with W, X, Y, Z; IN with MA.
 */
function inferProfile(vin: string): string {
  const first = vin.charAt(0).toUpperCase();
  if (first === "M") return "IN-4G-Standard";
  if (["W", "X", "Y", "Z"].includes(first)) return "EU-LTE-Standard";
  return "US-LTE-Standard";
}

// ── Generated registration state ─────────────────────────────────────────────

interface GeneratedFields {
  iccid: ProvenanceValue<string>;
  imsi: ProvenanceValue<string>;
  profile: ProvenanceValue<string>;
}

// ── Column definitions ────────────────────────────────────────────────────────

const COLUMN_DEFINITIONS: Array<{
  id: string;
  header: string;
  cell: (item: RegistrationLogRow) => React.ReactNode;
}> = [
  {
    id: "vin",
    header: "VIN",
    cell: (item) => (
      <ProvenanceField field={item.vin} label="reg_vin" />
    ),
  },
  {
    id: "iccid",
    header: "ICCID",
    cell: (item) => (
      <ProvenanceField field={item.iccid} label="reg_iccid" />
    ),
  },
  {
    id: "imsi",
    header: "IMSI",
    cell: (item) => (
      <ProvenanceField field={item.imsi} label="reg_imsi" />
    ),
  },
  {
    id: "profile",
    header: "Profile",
    cell: (item) => (
      <ProvenanceField field={item.profile} label="reg_profile" />
    ),
  },
  {
    id: "market",
    header: "Market",
    cell: (item) => (
      <ProvenanceField field={item.market} label="reg_market" />
    ),
  },
  {
    id: "registeredAt",
    header: "Registered At",
    cell: (item) => (
      <ProvenanceField field={item.registeredAt} label="reg_registered_at" />
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (item) => {
      assertProvenance(item.status, "reg_status");
      if (item.status.provenance === "absent") {
        return (
          <Box color="text-status-inactive">
            <em>—</em>
          </Box>
        );
      }
      const status = item.status.value;
      if (status === null) return null;
      const statusMap: Record<RegistrationStatus, "success" | "pending" | "error"> = {
        success: "success",
        pending: "pending",
        failed: "error",
      };
      return (
        <SpaceBetween size="xs" direction="horizontal">
          <StatusIndicator type={statusMap[status]}>
            {STATUS_LABELS[status]}
          </StatusIndicator>
        </SpaceBetween>
      );
    },
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * FactoryRegistrationView renders a VIN registration form and a recent-
 * registrations log. Registration state is client-side only — no API, no
 * persistence. This is a simulated-flow screen.
 */
const FactoryRegistrationView: React.FC = () => {
  // Form state
  const [vinInput, setVinInput] = useState("");
  const [vinError, setVinError] = useState<string | undefined>(undefined);
  const [generatedFields, setGeneratedFields] = useState<GeneratedFields | null>(
    null,
  );

  // Registration log — starts from fixture, new entries prepended
  const [logRows, setLogRows] = useState<RegistrationLogRow[]>(
    FACTORY_REGISTRATION_FIXTURE.recentRegistrations,
  );

  // ── Form handlers ─────────────────────────────────────────────────────────

  const handleVinChange = useCallback(
    ({ detail }: { detail: { value: string } }) => {
      setVinInput(detail.value);
      setVinError(undefined);
      setGeneratedFields(null);
    },
    [],
  );

  /**
   * Simulate generating ICCID/IMSI/profile for the entered VIN.
   * This is a preview step; the user then clicks "Confirm Registration".
   * Values are wrapped in sim() at generation so the preview is labelled too.
   */
  const handleGenerate = useCallback(() => {
    const trimmed = vinInput.trim().toUpperCase();
    if (trimmed.length < 17) {
      setVinError("VIN must be 17 characters.");
      return;
    }
    const sim = <T,>(value: T): ProvenanceValue<T> => ({
      value,
      provenance: "simulated",
    });
    setGeneratedFields({
      iccid: sim(generateIccid(trimmed)),
      imsi: sim(generateImsi(trimmed)),
      profile: sim(inferProfile(trimmed)),
    });
    setVinError(undefined);
  }, [vinInput]);

  /**
   * Confirm and add the registration to the log (client-side only).
   */
  const handleConfirm = useCallback(() => {
    if (!generatedFields) return;
    const trimmed = vinInput.trim().toUpperCase();
    const now = new Date().toISOString();
    const sim = <T,>(value: T): ProvenanceValue<T> => ({
      value,
      provenance: "simulated",
    });
    const newRow: RegistrationLogRow = {
      id: `reg-new-${Date.now()}`,
      vin: sim(trimmed),
      iccid: sim(generatedFields.iccid.value ?? ""),
      imsi: sim(generatedFields.imsi.value ?? ""),
      profile: sim(generatedFields.profile.value ?? ""),
      market: sim(inferProfile(trimmed).split("-")[0]),
      registeredAt: sim(now),
      status: sim("success" as const),
    };
    setLogRows((prev) => [newRow, ...prev]);
    setVinInput("");
    setGeneratedFields(null);
  }, [generatedFields, vinInput]);

  // ── Table collection ──────────────────────────────────────────────────────

  const handleClearFilter = useCallback(() => {
    collection.actions.setFiltering("");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const collection = useConnectedServicesCollection<RegistrationLogRow>(
    logRows,
    {
      resourceName: "registrations",
      pageSize: 25,
      onClearFilter: handleClearFilter,
    },
  );

  const { items, filteredItemsCount, collectionProps, filterProps, paginationProps } =
    collection;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="factory-registration-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* Registration form */}
      <Container
        header={<Header variant="h2">Register a Vehicle</Header>}
        data-testid="factory-registration-form"
      >
        <SpaceBetween size="m">
          <FormField
            label="VIN"
            errorText={vinError}
            description="Enter the 17-character Vehicle Identification Number stamped at end-of-line."
          >
            <Input
              value={vinInput}
              onChange={handleVinChange}
              placeholder="e.g. 1HGCM82633A004352"
              inputMode="text"
              data-testid="vin-input"
            />
          </FormField>

          {generatedFields === null ? (
            <Button
              onClick={handleGenerate}
              disabled={vinInput.trim().length === 0}
              data-testid="generate-credentials-btn"
            >
              Generate connectivity credentials
            </Button>
          ) : (
            <SpaceBetween size="m">
              <SpaceBetween size="s">
                <Box variant="awsui-key-label">ICCID</Box>
                <ProvenanceField
                  field={generatedFields.iccid}
                  label="preview_iccid"
                  testId="preview-iccid"
                />
                <Box variant="awsui-key-label">IMSI</Box>
                <ProvenanceField
                  field={generatedFields.imsi}
                  label="preview_imsi"
                  testId="preview-imsi"
                />
                <Box variant="awsui-key-label">Profile</Box>
                <ProvenanceField
                  field={generatedFields.profile}
                  label="preview_profile"
                  testId="preview-profile"
                />
              </SpaceBetween>
              <SpaceBetween size="xs" direction="horizontal">
                <Button
                  variant="primary"
                  onClick={handleConfirm}
                  data-testid="confirm-registration-btn"
                >
                  Confirm registration
                </Button>
                <Button
                  onClick={() => {
                    setGeneratedFields(null);
                    setVinInput("");
                  }}
                  data-testid="cancel-registration-btn"
                >
                  Cancel
                </Button>
              </SpaceBetween>
            </SpaceBetween>
          )}
        </SpaceBetween>
      </Container>

      {/* Recent registrations table */}
      <Table
        {...collectionProps}
        data-testid="factory-registration-table"
        columnDefinitions={COLUMN_DEFINITIONS}
        items={items}
        loadingText="Loading registrations"
        header={
          <Header
            counter={getHeaderCounterText(filteredItemsCount, logRows.length)}
            data-testid="factory-registration-table-header"
          >
            Recent Registrations
          </Header>
        }
        filter={
          <TextFilter
            {...filterProps}
            filteringPlaceholder="Find registrations"
            countText={getTextFilterCounterText(
              filteredItemsCount ?? logRows.length,
              logRows.length,
            )}
          />
        }
        pagination={<Pagination {...paginationProps} />}
      />
    </SpaceBetween>
  );
};

export default FactoryRegistrationView;

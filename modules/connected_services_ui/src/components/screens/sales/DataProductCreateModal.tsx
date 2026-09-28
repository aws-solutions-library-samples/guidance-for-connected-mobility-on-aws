// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataProductCreateModal — stub Cloudscape Modal for authoring a new outbound
 * telemetry data product.
 *
 * ## Scope (stub pass 2026-09-14)
 *
 * UI shape only. No submission target, no persistence, no validation beyond
 * "submit is disabled if required fields are empty."
 *
 * ## Why a Modal and not a full-page Wizard
 *
 * The first cut of this was a 6-step Cloudscape Wizard on a dedicated route
 * (`/sales/data-products/create`). User feedback: "shouldn't this pop up in
 * a modal?" — yes. For a stub whose purpose is to show the mental model, a
 * modal is faster to review (no route navigation, no page-load) and the
 * six sections fit as scrolling groups inside one form. Full-page Wizard
 * pattern was rejected 2026-09-14 for this reason.
 *
 * ## Sections
 *
 *   1. Basics       — name, description, category
 *   2. Signals      — which VSS signals this product streams
 *   3. Events       — which discrete events this product emits
 *   4. Delivery     — cadence + wire format
 *   5. Visibility   — which downstream CMS Cognito subscriber groups can
 *                     see this product in their catalog. This is the
 *                     direct answer to "made visible to the CMS group
 *                     who's getting this data."
 *
 * ## Wired-spec deferrals
 *
 * - Real Signal Catalog wiring (uses a hard-coded VSS list here)
 * - Real Events catalog (hard-coded stub list)
 * - Real subscriber-group targeting (5 hard-coded CMS Cognito groups)
 * - Persistence — Submit is a no-op that closes the modal
 * - Validation — the wired spec will pin signal-count minimums, event
 *   trigger compatibility with the chosen cadence, and consent-status
 *   pre-flight
 */

import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Multiselect from "@cloudscape-design/components/multiselect";
import type { MultiselectProps } from "@cloudscape-design/components/multiselect";
import RadioGroup from "@cloudscape-design/components/radio-group";
import Select from "@cloudscape-design/components/select";
import type { SelectProps } from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";
import React, { useCallback, useState } from "react";

// ── Stub option catalogs (inline — wired spec replaces with real lookups) ────

const CATEGORY_OPTIONS: SelectProps.Option[] = [
  { value: "vehicle_status", label: "Vehicle Status" },
  { value: "diagnostic", label: "Diagnostic" },
  { value: "location", label: "Location" },
  { value: "battery_energy", label: "Battery & Energy" },
  { value: "adas", label: "ADAS / Driver Assist" },
  { value: "custom", label: "Custom" },
];

const SIGNAL_OPTIONS: MultiselectProps.Options = [
  { value: "Vehicle.Speed", label: "Vehicle.Speed" },
  { value: "Vehicle.CurrentLocation.Latitude", label: "Vehicle.CurrentLocation.Latitude" },
  { value: "Vehicle.CurrentLocation.Longitude", label: "Vehicle.CurrentLocation.Longitude" },
  { value: "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current", label: "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current" },
  { value: "Vehicle.Powertrain.CombustionEngine.EngineCoolant.Temperature", label: "Vehicle.Powertrain.CombustionEngine.EngineCoolant.Temperature" },
  { value: "Vehicle.OBD.Speed", label: "Vehicle.OBD.Speed" },
  { value: "Vehicle.OBD.EngineLoad", label: "Vehicle.OBD.EngineLoad" },
  { value: "Vehicle.OBD.EngineRPM", label: "Vehicle.OBD.EngineRPM" },
  { value: "Vehicle.OBD.FuelLevel", label: "Vehicle.OBD.FuelLevel" },
  { value: "Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure", label: "Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure" },
];

const EVENT_OPTIONS: MultiselectProps.Options = [
  { value: "harsh_braking", label: "Harsh braking" },
  { value: "harsh_acceleration", label: "Harsh acceleration" },
  { value: "harsh_cornering", label: "Harsh cornering" },
  { value: "ignition_on", label: "Ignition on" },
  { value: "ignition_off", label: "Ignition off" },
  { value: "geofence_entry", label: "Geofence entry" },
  { value: "geofence_exit", label: "Geofence exit" },
  { value: "low_battery_soc", label: "Low battery SoC" },
  { value: "dtc_stored", label: "DTC stored" },
  { value: "crash_detected", label: "Crash detected" },
];

const FORMAT_OPTIONS: SelectProps.Option[] = [
  { value: "json", label: "JSON (line-delimited)" },
  { value: "protobuf", label: "Protobuf" },
  { value: "parquet", label: "Parquet" },
  { value: "avro", label: "Avro" },
];

// Downstream CMS Cognito subscriber groups this product will be visible to.
const SUBSCRIBER_GROUP_OPTIONS: MultiselectProps.Options = [
  { value: "fleet-operators", label: "fleet-operators", description: "Fleet operators managing scoped fleets" },
  { value: "fleet-viewers", label: "fleet-viewers", description: "Read-only fleet dashboard users" },
  { value: "platform-admin", label: "platform-admin", description: "Cross-fleet administrators" },
  { value: "warranty-analysts", label: "warranty-analysts", description: "Warranty and reliability analysts" },
  { value: "data-scientists", label: "data-scientists", description: "Analytics and ML users" },
];

// ── State ─────────────────────────────────────────────────────────────────────

interface FormState {
  name: string;
  description: string;
  category: SelectProps.Option | null;
  signals: MultiselectProps.Options;
  events: MultiselectProps.Options;
  cadence: "realtime" | "hourly" | "daily";
  format: SelectProps.Option | null;
  visibleTo: MultiselectProps.Options;
}

const INITIAL_STATE: FormState = {
  name: "",
  description: "",
  category: null,
  signals: [],
  events: [],
  cadence: "realtime",
  format: null,
  visibleTo: [],
};

// ── Props ────────────────────────────────────────────────────────────────────

export interface DataProductCreateModalProps {
  visible: boolean;
  onDismiss: () => void;
  /** Called on Submit — receives the form state. Stub: caller may ignore. */
  onSubmit?: (state: FormState) => void;
}

// ── Component ────────────────────────────────────────────────────────────────

const DataProductCreateModal: React.FC<DataProductCreateModalProps> = ({
  visible,
  onDismiss,
  onSubmit,
}) => {
  const [state, setState] = useState<FormState>(INITIAL_STATE);

  const patch = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) => {
      setState((s) => ({ ...s, [key]: value }));
    },
    [],
  );

  const handleSubmit = useCallback(() => {
    if (onSubmit) onSubmit(state);
    // Reset for next open.
    setState(INITIAL_STATE);
    onDismiss();
  }, [state, onSubmit, onDismiss]);

  const handleCancel = useCallback(() => {
    setState(INITIAL_STATE);
    onDismiss();
  }, [onDismiss]);

  // Minimum viable: name + category + at least one signal.
  const canSubmit =
    state.name.trim().length > 0 &&
    state.category !== null &&
    state.signals.length > 0;

  return (
    <Modal
      visible={visible}
      onDismiss={handleCancel}
      size="large"
      header="Create data product"
      data-testid="data-product-create-modal"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={handleCancel}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!canSubmit}
              onClick={handleSubmit}
              data-testid="data-product-create-submit"
            >
              Create data product
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        {/* Basics ────────────────────────────────────────────────────── */}
        <Box>
          <Header
            variant="h3"
            description="Name the product and pick a data category."
          >
            Basics
          </Header>
          <SpaceBetween size="s">
            <FormField label="Product name">
              <Input
                value={state.name}
                onChange={(e) => patch("name", e.detail.value)}
                placeholder="e.g. Powertrain telemetry v2"
              />
            </FormField>
            <FormField label="Description">
              <Textarea
                value={state.description}
                onChange={(e) => patch("description", e.detail.value)}
                rows={2}
                placeholder="What is in the feed and why a subscriber would want it."
              />
            </FormField>
            <FormField label="Data category">
              <Select
                selectedOption={state.category}
                onChange={(e) =>
                  patch("category", e.detail.selectedOption as FormState["category"])
                }
                options={CATEGORY_OPTIONS}
                placeholder="Select a category"
              />
            </FormField>
          </SpaceBetween>
        </Box>

        {/* Signals ───────────────────────────────────────────────────── */}
        <Box>
          <Header
            variant="h3"
            description={`Continuous streams from the Signal Catalog. Selected ${state.signals.length} of ${SIGNAL_OPTIONS.length}.`}
          >
            Signals
          </Header>
          <FormField>
            <Multiselect
              selectedOptions={state.signals}
              onChange={(e) => patch("signals", e.detail.selectedOptions)}
              options={SIGNAL_OPTIONS}
              placeholder="Select signals to include"
              filteringType="auto"
              tokenLimit={5}
            />
          </FormField>
        </Box>

        {/* Events ────────────────────────────────────────────────────── */}
        <Box>
          <Header
            variant="h3"
            description={`Point-in-time events emitted when their trigger fires. Selected ${state.events.length} of ${EVENT_OPTIONS.length}.`}
          >
            Events
          </Header>
          <FormField>
            <Multiselect
              selectedOptions={state.events}
              onChange={(e) => patch("events", e.detail.selectedOptions)}
              options={EVENT_OPTIONS}
              placeholder="Select events to include"
              filteringType="auto"
              tokenLimit={5}
            />
          </FormField>
        </Box>

        {/* Delivery ──────────────────────────────────────────────────── */}
        <Box>
          <Header variant="h3" description="How often and in what wire format the feed publishes.">
            Delivery
          </Header>
          <ColumnLayout columns={2}>
            <FormField label="Cadence">
              <RadioGroup
                value={state.cadence}
                onChange={(e) =>
                  patch("cadence", e.detail.value as FormState["cadence"])
                }
                items={[
                  { value: "realtime", label: "Real-time (streaming)" },
                  { value: "hourly", label: "Hourly batch" },
                  { value: "daily", label: "Daily batch" },
                ]}
              />
            </FormField>
            <FormField label="Wire format">
              <Select
                selectedOption={state.format}
                onChange={(e) =>
                  patch("format", e.detail.selectedOption as FormState["format"])
                }
                options={FORMAT_OPTIONS}
                placeholder="Select a format"
              />
            </FormField>
          </ColumnLayout>
        </Box>

        {/* Visibility ────────────────────────────────────────────────── */}
        <Box>
          <Header
            variant="h3"
            description="Which CMS Cognito subscriber groups can see this product in their catalog. Users outside these groups will not see the product at all."
          >
            Visibility
          </Header>
          <FormField>
            <Multiselect
              selectedOptions={state.visibleTo}
              onChange={(e) => patch("visibleTo", e.detail.selectedOptions)}
              options={SUBSCRIBER_GROUP_OPTIONS}
              placeholder="Select subscriber groups"
              filteringType="auto"
            />
          </FormField>
          <Box variant="small" color="text-body-secondary" padding={{ top: "xs" }}>
            Wired spec will resolve this against the live CMS Cognito group inventory.
          </Box>
        </Box>
      </SpaceBetween>
    </Modal>
  );
};

export default DataProductCreateModal;

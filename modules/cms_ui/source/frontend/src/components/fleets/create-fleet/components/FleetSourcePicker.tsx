// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from 'react';
import {
  Alert,
  Container,
  FormField,
  Header,
  RadioGroup,
  Select,
  SpaceBetween,
  Spinner,
} from '@cloudscape-design/components';
import { listManifests, ManifestRegistryError, type ManifestEntry } from '@/api/oem1ManifestRegistry';
import { listModelManifests, ModelManifestRegistryError, type ModelManifestEntry } from '@/api/vehicleModelRegistry';

export type TelemetrySource = 'on-board' | 'off-board';

export interface FleetSourceSelection {
  telemetrySource: TelemetrySource;
  transformManifestId?: string;
  defaultVehicleModelId?: string;
}

export interface FleetSourcePickerProps {
  selection: FleetSourceSelection;
  onSelectionChange: (s: FleetSourceSelection) => void;
}

/** Derive the OEM display prefix from a manifest filename.
 * oem1-*.json → "OEM1"; oem2-*.json → "OEM2"; otherwise "OEM?".
 */
function deriveOemDisplayPrefix(manifestName: string): string {
  const match = manifestName.match(/^(oem\d+)-/i);
  return match ? match[1].toUpperCase() : 'OEM?';
}

function formatTransformOption(name: string): string {
  return `${deriveOemDisplayPrefix(name)} · ${name}`;
}

export function FleetSourcePicker({ selection, onSelectionChange }: FleetSourcePickerProps) {
  const [transformManifests, setTransformManifests] = useState<ManifestEntry[]>([]);
  const [transformLoading, setTransformLoading] = useState(false);
  const [transformError, setTransformError] = useState<string | null>(null);

  const [modelManifests, setModelManifests] = useState<ModelManifestEntry[]>([]);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);

  const update = (patch: Partial<FleetSourceSelection>) =>
    onSelectionChange({ ...selection, ...patch });

  // Layer 2 — fetch transform manifests when off-board
  useEffect(() => {
    if (selection.telemetrySource !== 'off-board') return;
    let cancelled = false;
    setTransformLoading(true);
    setTransformError(null);
    listManifests()
      .then(({ manifests: list }) => {
        if (cancelled) return;
        setTransformManifests(list);
        if (list.length === 1) {
          update({ transformManifestId: list[0].name });
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setTransformError(
          e instanceof ManifestRegistryError
            ? `Failed to load manifests (${e.statusCode}): ${e.message}`
            : 'Failed to load manifest registry',
        );
      })
      .finally(() => {
        if (!cancelled) setTransformLoading(false);
      });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.telemetrySource]);

  // Layer 3 — always fetch model manifests on mount (mode-independent)
  useEffect(() => {
    let cancelled = false;
    setModelLoading(true);
    setModelError(null);
    listModelManifests()
      .then(({ modelManifests: list }) => {
        if (!cancelled) setModelManifests(list);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setModelError(
          e instanceof ModelManifestRegistryError
            ? `Failed to load model manifests (${e.statusCode}): ${e.message}`
            : 'Failed to load model manifest registry',
        );
      })
      .finally(() => {
        if (!cancelled) setModelLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const showOffBoard = selection.telemetrySource === 'off-board';

  const transformOptions = transformManifests.map((m) => ({
    label: formatTransformOption(m.name),
    value: m.name,
  }));
  const selectedTransform = transformOptions.find((o) => o.value === selection.transformManifestId) ?? null;

  const modelOptions = modelManifests.map((m) => ({
    label: [
      m.modelManifestName,
      m.vehicleCount !== undefined ? `${m.vehicleCount} vehicles` : null,
      m.status ?? null,
    ].filter(Boolean).join(' · '),
    value: m.modelManifestName,
  }));
  const selectedModel = modelOptions.find((o) => o.value === selection.defaultVehicleModelId) ?? null;

  return (
    <Container
      header={
        <Header variant="h2" description="Choose how vehicle data will be sourced for this fleet.">
          Data Source
        </Header>
      }
    >
      <SpaceBetween size="m">
        {/* Layer 1 — telemetry architecture */}
        <FormField label="Telemetry source">
          <RadioGroup
            data-testid="telemetry-source-radio"
            value={selection.telemetrySource}
            onChange={({ detail }) => {
              const next = detail.value as TelemetrySource;
              if (next === 'on-board') {
                update({ telemetrySource: 'on-board', transformManifestId: undefined });
              } else {
                update({ telemetrySource: 'off-board', defaultVehicleModelId: undefined });
              }
            }}
            items={[
              {
                value: 'on-board',
                label: 'On-board',
                description: 'Vehicle data captured at the edge. Today: FleetWise Edge (FWE) or MQTT Direct.',
              },
              {
                value: 'off-board',
                label: 'Off-board',
                description: 'Vehicle data sourced from an OEM cloud API. Requires a transform manifest.',
              },
            ]}
          />
        </FormField>

        {/* Layer 2 — transform manifest (off-board only) */}
        {showOffBoard && (
          transformLoading ? (
            <FormField label="Transform manifest">
              <Spinner data-testid="transform-spinner" />
            </FormField>
          ) : transformError ? (
            <Alert data-testid="manifest-error" type="error">{transformError}</Alert>
          ) : transformManifests.length === 0 ? (
            <FormField label="Transform manifest">
              <Alert data-testid="transform-empty" type="info">
                No transform manifests found. Upload one in Data Processing → Transform Manifests.
              </Alert>
            </FormField>
          ) : (
            <FormField label="Transform manifest" description="Select the OEM transform manifest for signal mapping.">
              <Select
                data-testid="manifest-select"
                selectedOption={selectedTransform}
                options={transformOptions}
                onChange={({ detail }) => update({ transformManifestId: detail.selectedOption.value ?? undefined })}
                placeholder="Select a manifest"
              />
            </FormField>
          )
        )}

        {/* Layer 3 — default vehicle model (on-board only, optional) */}
        {!showOffBoard && modelLoading ? (
          <FormField label="Default vehicle model" description="Optional. Pre-fills the Vehicle Model on Create-Vehicle for vehicles added to this fleet.">
            <Spinner data-testid="model-spinner" />
          </FormField>
        ) : !showOffBoard && modelError ? (
          <FormField label="Default vehicle model" description="Optional. Pre-fills the Vehicle Model on Create-Vehicle for vehicles added to this fleet.">
            <Alert data-testid="model-error" type="error">{modelError}</Alert>
          </FormField>
        ) : !showOffBoard ? (
          <FormField
            label="Default vehicle model"
            description="Optional. Pre-fills the Vehicle Model on Create-Vehicle for vehicles added to this fleet. Per-vehicle override always allowed."
          >
            <Select
              data-testid="model-manifest-select"
              selectedOption={selectedModel}
              options={modelOptions}
              onChange={({ detail }) =>
                update({ defaultVehicleModelId: detail.selectedOption.value ?? undefined })
              }
              placeholder="Optional — choose a default for vehicles in this fleet"
              empty="No vehicle models available"
            />
          </FormField>
        ) : null}
      </SpaceBetween>
    </Container>
  );
}

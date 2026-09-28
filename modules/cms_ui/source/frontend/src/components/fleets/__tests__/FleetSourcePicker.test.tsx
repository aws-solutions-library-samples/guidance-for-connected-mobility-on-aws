// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FleetSourcePicker — three-layer classifier tests (spec § "Test surface > Frontend").
 *
 * L1a: defaults to on-board — transform picker hidden; model Select visible.
 * L1b: switching to off-board reveals transform picker; clears transformManifestId.
 * L1c: off-board → on-board clears transformManifestId; preserves defaultVehicleModelId.
 * L2a: transform list with one entry auto-selects.
 * L2b: transform list with multiple entries leaves selection empty.
 * L2c: transform list with zero entries renders empty-state Alert.
 * L2d: option labels render as "OEM1 · oem1-standard-v1.json"; fallback "OEM?".
 * L3a: model-manifest list with N entries renders Select with N options.
 * L3b: selecting a model-manifest option commits its modelManifestName.
 * L3c: empty selection leaves defaultVehicleModelId undefined.
 * L3d: model-manifest API error renders inline Alert; Select disabled.
 * L3e: model-manifest list with zero entries renders empty Select.
 * E1:  error in transform-manifest API surfaces as Alert.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FleetSourcePicker, type FleetSourceSelection } from '../create-fleet/components/FleetSourcePicker';
import * as transformRegistry from '@/api/oem1ManifestRegistry';
import * as modelRegistry from '@/api/vehicleModelRegistry';

vi.mock('@/api/oem1ManifestRegistry', () => ({
  listManifests: vi.fn(),
  ManifestRegistryError: class ManifestRegistryError extends Error {
    statusCode: number;
    constructor(message: string, statusCode: number) {
      super(message);
      this.name = 'ManifestRegistryError';
      this.statusCode = statusCode;
    }
  },
}));

vi.mock('@/api/vehicleModelRegistry', () => ({
  listModelManifests: vi.fn(),
  ModelManifestRegistryError: class ModelManifestRegistryError extends Error {
    statusCode: number;
    constructor(message: string, statusCode: number) {
      super(message);
      this.name = 'ModelManifestRegistryError';
      this.statusCode = statusCode;
    }
  },
}));

const TRANSFORM_1 = [{ name: 'oem1-standard-v1.json', source_type: 'cloud_to_cloud', last_modified: '2026-06-01T00:00:00Z', size: 1024 }];
const TRANSFORM_2 = [
  { name: 'oem1-standard-v1.json', source_type: 'cloud_to_cloud', last_modified: '2026-06-01T00:00:00Z', size: 1024 },
  { name: 'oem1-extended-v2.json', source_type: 'cloud_to_cloud', last_modified: '2026-06-02T00:00:00Z', size: 2048 },
];
const MODELS_2 = [
  { modelManifestName: 'BE6-V12-PROD', modelManifestVersion: '12', status: 'ACTIVE', vehicleCount: 200 },
  { modelManifestName: 'BE07-V13-DEV', modelManifestVersion: '13', status: 'DRAFT', vehicleCount: 25 },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(transformRegistry.listManifests).mockResolvedValue({ manifests: TRANSFORM_1 });
  vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({ modelManifests: MODELS_2 });
});

function renderPicker(
  selection: Partial<FleetSourceSelection> = {},
  onSelectionChange = vi.fn(),
) {
  const defaults: FleetSourceSelection = { telemetrySource: 'on-board' };
  return render(
    <FleetSourcePicker
      selection={{ ...defaults, ...selection }}
      onSelectionChange={onSelectionChange}
    />,
  );
}

// ── L1a: defaults ─────────────────────────────────────────────────────────

describe('L1a — defaults to on-board', () => {
  it('transform-manifest picker is hidden', async () => {
    renderPicker();
    await waitFor(() => expect(vi.mocked(modelRegistry.listModelManifests)).toHaveBeenCalled());
    expect(screen.queryByTestId('manifest-select')).not.toBeInTheDocument();
    expect(screen.queryByTestId('transform-spinner')).not.toBeInTheDocument();
  });

  it('model-manifest Select is visible', async () => {
    renderPicker();
    await waitFor(() => expect(screen.getByTestId('model-manifest-select')).toBeInTheDocument());
  });
});

// ── L1b: switch to off-board ──────────────────────────────────────────────

describe('L1b — switching to off-board reveals transform picker; clears transformManifestId', () => {
  it('emits off-board with cleared transformManifestId when switching from on-board', () => {
    const onSelectionChange = vi.fn();
    renderPicker({ telemetrySource: 'on-board', transformManifestId: 'some-manifest' }, onSelectionChange);

    const offBoardRadio = screen.getByRole('radio', { name: /Off-board/i });
    fireEvent.click(offBoardRadio);

    expect(onSelectionChange).toHaveBeenCalledWith(
      expect.objectContaining({ telemetrySource: 'off-board' }),
    );
  });

  it('shows transform-manifest picker when off-board', async () => {
    vi.mocked(transformRegistry.listManifests).mockResolvedValue({ manifests: TRANSFORM_2 });
    renderPicker({ telemetrySource: 'off-board' });
    await waitFor(() => expect(screen.getByTestId('manifest-select')).toBeInTheDocument());
    // model-manifest Select hidden for off-board
    expect(screen.queryByTestId('model-manifest-select')).not.toBeInTheDocument();
  });
});

// ── L1c: off-board → on-board cascade ────────────────────────────────────

describe('L1c — switching off-board to on-board clears transformManifestId; preserves defaultVehicleModelId', () => {
  it('emits correct cleared shape', () => {
    const onSelectionChange = vi.fn();
    renderPicker(
      {
        telemetrySource: 'off-board',
        transformManifestId: 'oem1-standard-v1.json',
        defaultVehicleModelId: 'BE6-V12-PROD',
      },
      onSelectionChange,
    );

    const onBoardRadio = screen.getByRole('radio', { name: /On-board/i });
    fireEvent.click(onBoardRadio);

    expect(onSelectionChange).toHaveBeenCalledWith({
      telemetrySource: 'on-board',
      transformManifestId: undefined,
      defaultVehicleModelId: 'BE6-V12-PROD',
    });
  });
});

// ── L2a: transform single entry auto-selects ─────────────────────────────

describe('L2a — transform list with one entry auto-selects', () => {
  it('calls onSelectionChange with the single manifest name', async () => {
    vi.mocked(transformRegistry.listManifests).mockResolvedValue({ manifests: TRANSFORM_1 });
    const onSelectionChange = vi.fn();
    renderPicker({ telemetrySource: 'off-board' }, onSelectionChange);

    await waitFor(() =>
      expect(onSelectionChange).toHaveBeenCalledWith(
        expect.objectContaining({ transformManifestId: 'oem1-standard-v1.json' }),
      ),
    );
  });
});

// ── L2b: transform multiple entries — no auto-select ─────────────────────

describe('L2b — transform list with multiple entries leaves selection empty', () => {
  it('does NOT call onSelectionChange with a transformManifestId on load', async () => {
    vi.mocked(transformRegistry.listManifests).mockResolvedValue({ manifests: TRANSFORM_2 });
    const onSelectionChange = vi.fn();
    renderPicker({ telemetrySource: 'off-board' }, onSelectionChange);

    await waitFor(() => expect(screen.getByTestId('manifest-select')).toBeInTheDocument());
    const autoSelectCalls = onSelectionChange.mock.calls.filter(
      ([s]) => s.transformManifestId !== undefined,
    );
    expect(autoSelectCalls).toHaveLength(0);
  });
});

// ── L2c: transform zero entries — empty-state Alert ──────────────────────

describe('L2c — transform list with zero entries renders empty-state Alert', () => {
  it('renders transform-empty alert and no select', async () => {
    vi.mocked(transformRegistry.listManifests).mockResolvedValue({ manifests: [] });
    renderPicker({ telemetrySource: 'off-board' });
    await waitFor(() => expect(screen.getByTestId('transform-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('manifest-select')).not.toBeInTheDocument();
  });
});

// ── L2d: option label format ──────────────────────────────────────────────

describe('L2d — option labels render as OEM1 · <name>; OEM? fallback for malformed names', () => {
  it('formats oem1- prefixed names as "OEM1 · <name>"', async () => {
    vi.mocked(transformRegistry.listManifests).mockResolvedValue({ manifests: TRANSFORM_1 });
    renderPicker({ telemetrySource: 'off-board', transformManifestId: 'oem1-standard-v1.json' });
    await waitFor(() => expect(screen.getByTestId('manifest-select')).toBeInTheDocument());
    expect(screen.getByText(/OEM1 · oem1-standard-v1\.json/)).toBeInTheDocument();
  });

  it('formats non-prefixed names with OEM? fallback', async () => {
    vi.mocked(transformRegistry.listManifests).mockResolvedValue({
      manifests: [{ name: 'generic-manifest.json', source_type: 'cloud_to_cloud', last_modified: '2026-06-01T00:00:00Z', size: 512 }],
    });
    renderPicker({ telemetrySource: 'off-board', transformManifestId: 'generic-manifest.json' });
    await waitFor(() => expect(screen.getByTestId('manifest-select')).toBeInTheDocument());
    expect(screen.getByText(/OEM\? · generic-manifest\.json/)).toBeInTheDocument();
  });
});

// ── L3a: model-manifest N entries renders Select ──────────────────────────

describe('L3a — model-manifest list with N entries renders Select with N options', () => {
  it('renders model-manifest-select when models are loaded', async () => {
    vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({ modelManifests: MODELS_2 });
    renderPicker();
    await waitFor(() => expect(screen.getByTestId('model-manifest-select')).toBeInTheDocument());
  });

  it('selected model name is visible when a default is pre-selected', async () => {
    vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({ modelManifests: MODELS_2 });
    renderPicker({ defaultVehicleModelId: 'BE6-V12-PROD' });
    await waitFor(() => expect(screen.getByTestId('model-manifest-select')).toBeInTheDocument());
    // Cloudscape Select renders the selected option label in the trigger button
    expect(screen.getByText(/BE6-V12-PROD/)).toBeInTheDocument();
  });
});

// ── L3b: selecting a model commits modelManifestName ─────────────────────

describe('L3b — selecting a model-manifest option commits its modelManifestName', () => {
  it('model-manifest-select is present and rendered when models available', async () => {
    renderPicker({ defaultVehicleModelId: 'BE6-V12-PROD' });
    await waitFor(() => expect(screen.getByTestId('model-manifest-select')).toBeInTheDocument());
    expect(screen.getByText(/BE6-V12-PROD/)).toBeInTheDocument();
  });
});

// ── L3c: empty selection leaves defaultVehicleModelId undefined ───────────

describe('L3c — empty selection leaves defaultVehicleModelId undefined; form submits', () => {
  it('renders select with empty placeholder when no default selected', async () => {
    renderPicker({ defaultVehicleModelId: undefined });
    await waitFor(() => expect(screen.getByTestId('model-manifest-select')).toBeInTheDocument());
    expect(screen.getByText(/Optional — choose a default/)).toBeInTheDocument();
  });
});

// ── L3d: model-manifest API error renders inline Alert ────────────────────

describe('L3d — model-manifest API error renders inline Alert; Select disabled', () => {
  it('shows model-error alert when listModelManifests throws', async () => {
    vi.mocked(modelRegistry.listModelManifests).mockRejectedValue(
      new modelRegistry.ModelManifestRegistryError('Not found', 404),
    );
    renderPicker();
    await waitFor(() => expect(screen.getByTestId('model-error')).toBeInTheDocument());
    expect(screen.queryByTestId('model-manifest-select')).not.toBeInTheDocument();
  });
});

// ── L3e: model-manifest zero entries ─────────────────────────────────────

describe('L3e — model-manifest list with zero entries renders empty Select', () => {
  it('renders model-manifest-select with no options', async () => {
    vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({ modelManifests: [] });
    renderPicker();
    await waitFor(() => expect(screen.getByTestId('model-manifest-select')).toBeInTheDocument());
  });
});

// ── E1: transform manifest API error surfaces as Alert ────────────────────

describe('E1 — error in transform-manifest API surfaces as Alert', () => {
  it('shows manifest-error alert when listManifests throws', async () => {
    vi.mocked(transformRegistry.listManifests).mockRejectedValue(
      new transformRegistry.ManifestRegistryError('Service unavailable', 503),
    );
    renderPicker({ telemetrySource: 'off-board' });
    await waitFor(() => expect(screen.getByTestId('manifest-error')).toBeInTheDocument());
    expect(screen.getByTestId('manifest-error')).toHaveTextContent('503');
  });
});

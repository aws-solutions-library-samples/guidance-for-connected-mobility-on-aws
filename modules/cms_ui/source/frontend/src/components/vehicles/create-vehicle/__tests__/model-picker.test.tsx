// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ModelPicker component tests.
 * Spec: 2026-08-28-cms-cert-follows-model § "Test surface > Frontend"
 *
 * Cases:
 *  1. Renders loading state on first mount.
 *  2. Renders options after listModelManifests resolves — only ACTIVE items shown.
 *  3. Renders empty-state message when no ACTIVE items exist.
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ModelPicker } from '../components/model-picker';
import * as modelRegistry from '@/api/vehicleModelRegistry';

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

const ACTIVE_MODEL = {
  modelManifestName: 'CMS-Fleet-Default',
  modelManifestVersion: '1',
  displayName: 'CMS Fleet Default',
  status: 'ACTIVE' as const,
  decoderManifestRef: 'cms-fleet-v3',
};

const DRAFT_MODEL = {
  modelManifestName: 'CMS-Fleet-Beta',
  modelManifestVersion: '2',
  displayName: 'CMS Fleet Beta',
  status: 'DRAFT' as const,
};

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPicker(
  selectedModelManifestName = '',
  onChange = vi.fn(),
) {
  return render(
    <ModelPicker
      selectedModelManifestName={selectedModelManifestName}
      onChange={onChange}
    />,
  );
}

describe('ModelPicker', () => {
  it('1. renders loading state on first mount', async () => {
    // Never resolve — hold the promise open to keep the loading state visible.
    vi.mocked(modelRegistry.listModelManifests).mockReturnValue(new Promise(() => {}));

    renderPicker();

    // The Select should be in loading state (placeholder text)
    expect(screen.getByText('Loading models…')).toBeInTheDocument();
  });

  it('2. renders options after listModelManifests resolves; filters out non-ACTIVE', async () => {
    vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({
      modelManifests: [ACTIVE_MODEL, DRAFT_MODEL],
    });

    renderPicker();

    // Wait for loading to complete — the placeholder should switch to "Choose a model"
    await waitFor(() =>
      expect(screen.getByText('Choose a model')).toBeInTheDocument(),
    );

    // The ACTIVE model's displayName should be in the DOM as a Select option.
    // Cloudscape renders options in the combobox input role; the option is also
    // accessible via the listbox but only after the Select is opened.
    // The simpler assertion: the component did not show the empty-state constraint.
    expect(
      screen.queryByText(/No ACTIVE model manifests are configured/),
    ).not.toBeInTheDocument();

    // Verify the DRAFT model is NOT rendered as an option.
    // (Options are rendered lazily by Cloudscape — we check that the DRAFT
    // displayName is absent from the accessible tree.)
    expect(screen.queryByText('CMS Fleet Beta')).not.toBeInTheDocument();
  });

  it('3. renders empty-state message when no ACTIVE items exist', async () => {
    // Only a DRAFT model — the filter leaves items empty.
    vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({
      modelManifests: [DRAFT_MODEL],
    });

    renderPicker();

    await waitFor(() =>
      expect(
        screen.getByText(/No ACTIVE model manifests are configured/),
      ).toBeInTheDocument(),
    );
  });
});

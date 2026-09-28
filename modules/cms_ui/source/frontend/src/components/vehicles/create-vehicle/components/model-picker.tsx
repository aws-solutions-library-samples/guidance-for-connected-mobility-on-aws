// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import { Alert, FormField, Select, SelectProps } from '@cloudscape-design/components';
import {
  listModelManifests,
  ModelManifestRegistryError,
  type ModelManifestEntry,
} from '@/api/vehicleModelRegistry';

export interface ModelPickerProps {
  selectedModelManifestName: string;
  onChange: (modelManifestName: string) => void;
  disabled?: boolean;
}

export function ModelPicker({ selectedModelManifestName, onChange, disabled }: ModelPickerProps) {
  const [items, setItems] = useState<ModelManifestEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listModelManifests()
      .then(res => {
        if (cancelled) return;
        const active = (res.modelManifests || []).filter(m => m.status === 'ACTIVE');
        setItems(active);
        setLoading(false);
      })
      .catch(err => {
        if (cancelled) return;
        const msg = err instanceof ModelManifestRegistryError
          ? `Failed to load models (HTTP ${err.statusCode}). Retry, or contact an operator if this persists.`
          : 'Failed to load models. Retry, or contact an operator if this persists.';
        setError(msg);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const options: SelectProps.Option[] = items.map(m => ({
    label: m.displayName ?? m.modelManifestName,
    value: m.modelManifestName,
    // Cloudscape SelectProps.Option supports description as an optional string.
    description: m.decoderManifestRef ? `decoder: ${m.decoderManifestRef}` : undefined,
  }));

  const selectedOption = options.find(o => o.value === selectedModelManifestName) ?? null;

  return (
    <>
      <FormField
        label="Vehicle model"
        description="Select the model manifest for this vehicle. Only ACTIVE models with a decoder manifest are shown."
        errorText={error ?? undefined}
        constraintText={
          !loading && !error && items.length === 0
            ? 'No ACTIVE model manifests are configured. Run deployment/scripts/seed_model_manifests.py first.'
            : undefined
        }
      >
        <Select
          selectedOption={selectedOption}
          onChange={({ detail }) => onChange(String(detail.selectedOption?.value ?? ''))}
          options={options}
          placeholder={loading ? 'Loading models…' : 'Choose a model'}
          statusType={loading ? 'loading' : error ? 'error' : 'finished'}
          loadingText="Loading models…"
          empty={items.length === 0 ? 'No ACTIVE model manifests available' : undefined}
          disabled={disabled || loading || !!error}
        />
      </FormField>

      {!loading && !error && (
        <Alert type="info">
          A certificate will be issued automatically when this vehicle is created.
        </Alert>
      )}
    </>
  );
}

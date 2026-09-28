// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// spec 2026-09-01-cms-campaign-follows-enrollment § D4
// Campaign template picker for the Create Vehicle wizard.
// Only shown when dataSource === 'vehicle-telemetry'.
// Selection is optional — empty is a valid state.

import React, { useState, useEffect } from 'react';
import { FormField, Select, SelectProps } from '@cloudscape-design/components';
import {
  listCampaignTemplates,
  CampaignTemplateRegistryError,
  type CampaignTemplate,
} from '@/api/campaignTemplateRegistry';

export interface CampaignPickerProps {
  selectedCampaignTemplateRef: string | null;
  onChange: (ref: string | null) => void;
  disabled?: boolean;
}

export function CampaignPicker({ selectedCampaignTemplateRef, onChange, disabled }: CampaignPickerProps) {
  const [items, setItems] = useState<CampaignTemplate[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listCampaignTemplates()
      .then(res => {
        if (cancelled) return;
        setItems(res.campaignTemplates ?? []);
        setLoading(false);
      })
      .catch(err => {
        if (cancelled) return;
        const msg = err instanceof CampaignTemplateRegistryError
          ? `Failed to load campaign templates (HTTP ${err.statusCode}). Retry, or contact an operator if this persists.`
          : 'Failed to load campaign templates. Retry, or contact an operator if this persists.';
        setError(msg);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Include a "no selection" option at the top so the user can clear a choice.
  const noSelectionOption: SelectProps.Option = {
    label: 'None — attach later',
    value: '',
  };

  const options: SelectProps.Option[] = [
    noSelectionOption,
    ...items.map(c => ({
      label: c.campaignName ?? c.campaignId,
      value: c.campaignId,
      description: c.decoderManifestId ? `decoder: ${c.decoderManifestId}` : undefined,
    })),
  ];

  const selectedOption =
    selectedCampaignTemplateRef
      ? (options.find(o => o.value === selectedCampaignTemplateRef) ?? null)
      : noSelectionOption;

  return (
    <>
      <FormField
        label="Data collection campaign (optional)"
        description="Attach a campaign to start collecting telemetry as soon as the vehicle connects. You can also attach one later from the vehicle's Campaigns tab."
        errorText={error ?? undefined}
        constraintText={
          !loading && !error && items.length === 0
            ? 'No campaign templates are available yet. Ask your administrator to add one.'
            : undefined
        }
      >
        <Select
          selectedOption={selectedOption}
          onChange={({ detail }) => {
            const val = detail.selectedOption?.value ?? '';
            onChange(val === '' ? null : val);
          }}
          options={options}
          placeholder={loading ? 'Loading campaign templates…' : 'Choose a campaign template (optional)'}
          statusType={loading ? 'loading' : error ? 'error' : 'finished'}
          loadingText="Loading campaign templates…"
          disabled={disabled || loading || !!error}
          data-testid="campaign-template-select"
        />
      </FormField>
    </>
  );
}

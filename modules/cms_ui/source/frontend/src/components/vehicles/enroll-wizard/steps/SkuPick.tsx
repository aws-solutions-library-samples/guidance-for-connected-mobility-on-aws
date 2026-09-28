// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  SpaceBetween,
  FormField,
  Select,
  Input,
  Alert,
  Box,
} from '@cloudscape-design/components';
import type { WizardAction } from '../state/reducer';

interface CatalogEntry {
  sku: string;
  displayName: string;
  description?: string;
}

/** Reads oem1ProductCatalog from the CDK-injected window.runtimeConfig (OQ12). */
function getCatalog(): CatalogEntry[] {
  try {
    const rc = (window as any).runtimeConfig;
    const catalog = rc?.oem1ProductCatalog;
    if (Array.isArray(catalog) && catalog.length > 0) return catalog as CatalogEntry[];
  } catch {
    // ignore
  }
  return [];
}

interface StepSkuPickProps {
  sku: string;
  dispatch: React.Dispatch<WizardAction>;
}

const StepSkuPick: React.FC<StepSkuPickProps> = ({ sku, dispatch }) => {
  const catalog = getCatalog();
  const hasCatalog = catalog.length > 0;

  const selectOptions = catalog.map((c) => ({
    value: c.sku,
    label: `${c.displayName} (${c.sku})`,
    description: c.description,
  }));

  const selectedOption = selectOptions.find((o) => o.value === sku) ?? null;

  return (
    <SpaceBetween size="m">
      {!hasCatalog && (
        <Alert type="info">
          No product catalog configured in <code>cdk.json:context.oem1ProductCatalog</code>.
          Enter a SKU manually.
        </Alert>
      )}

      {hasCatalog ? (
        <FormField label="Select SKU" description="Choose a product SKU from the configured catalog.">
          <Select
            selectedOption={selectedOption}
            onChange={({ detail }) => dispatch({ type: 'SET_SKU', sku: detail.selectedOption?.value ?? '' })}
            options={selectOptions}
            placeholder="Choose a SKU"
            empty="No SKUs in catalog"
          />
        </FormField>
      ) : (
        <FormField label="SKU" description="Enter the OEM1 product SKU (e.g. SKU-00000069).">
          <Input
            value={sku}
            onChange={({ detail }) => dispatch({ type: 'SET_SKU', sku: detail.value })}
            placeholder="SKU-00000069"
          />
        </FormField>
      )}

      {sku && (
        <Box>
          Selected: <strong>{sku}</strong>
        </Box>
      )}
    </SpaceBetween>
  );
};

export default StepSkuPick;

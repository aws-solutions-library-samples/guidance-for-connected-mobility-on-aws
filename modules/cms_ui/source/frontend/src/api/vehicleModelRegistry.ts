// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';
import { getDataProcessingApiEndpoint } from '@/config/api';

export interface ModelManifestEntry {
  modelManifestName: string;
  modelManifestVersion: string;
  displayName?: string;
  status?: 'ACTIVE' | 'DRAFT' | 'DEPRECATED' | string;
  productionPhase?: 'production' | 'validation' | string;
  vehicleCount?: number;
  decoderManifestRef?: string;
}

export interface ModelManifestListResponse {
  modelManifests: ModelManifestEntry[];
}

export class ModelManifestRegistryError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'ModelManifestRegistryError';
  }
}

export async function listModelManifests(): Promise<ModelManifestListResponse> {
  const url = `${getDataProcessingApiEndpoint()}model-manifests`;

  let response: Response;
  try {
    response = await authFetch(url);
  } catch {
    throw new ModelManifestRegistryError('Network error fetching model-manifest registry', 0);
  }

  if (!response.ok) {
    throw new ModelManifestRegistryError(
      `Model-manifest registry request failed: ${response.status} ${response.statusText}`,
      response.status,
    );
  }

  const data = await response.json();
  return { modelManifests: data.modelManifests ?? [] };
}

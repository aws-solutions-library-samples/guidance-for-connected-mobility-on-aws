// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';
import { getDataProcessingApiEndpoint } from '@/config/api';

export interface ManifestEntry {
  name: string;
  source_type: string;
  last_modified: string;
  size: number;
}

export interface ManifestListResponse {
  manifests: ManifestEntry[];
}

export class ManifestRegistryError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'ManifestRegistryError';
  }
}

/**
 * List transform manifests from the Phase 1 data-processing API.
 * Mirrors the fetch pattern used in TransformManifestsViewer.tsx.
 */
export async function listManifests(): Promise<ManifestListResponse> {
  const url = `${getDataProcessingApiEndpoint()}manifests`;

  let response: Response;
  try {
    response = await authFetch(url);
  } catch {
    throw new ManifestRegistryError('Network error fetching manifest registry', 0);
  }

  if (!response.ok) {
    throw new ManifestRegistryError(
      `Manifest registry request failed: ${response.status} ${response.statusText}`,
      response.status,
    );
  }

  const data = await response.json();
  return { manifests: data.manifests ?? [] };
}

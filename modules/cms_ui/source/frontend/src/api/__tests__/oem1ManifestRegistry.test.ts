// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { listManifests, ManifestRegistryError } from '../oem1ManifestRegistry';
import * as authFetchModule from '@/utils/authFetch';
import * as apiConfig from '@/config/api';

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));
vi.mock('@/config/api', () => ({
  getDataProcessingApiEndpoint: vi.fn(() => 'https://api.example.com/data-processing/'),
}));

const MANIFESTS = [
  { name: 'oem1-transform.json', source_type: 'cloud_to_cloud', last_modified: '2026-06-01T00:00:00Z', size: 1024 },
];

beforeEach(() => vi.clearAllMocks());

describe('listManifests', () => {
  it('returns manifests array on 200', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify({ manifests: MANIFESTS }), { status: 200 }),
    );
    const result = await listManifests();
    expect(result.manifests).toHaveLength(1);
    expect(result.manifests[0].name).toBe('oem1-transform.json');
  });

  it('returns empty manifests array when response body has no manifests field', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify({}), { status: 200 }),
    );
    const result = await listManifests();
    expect(result.manifests).toEqual([]);
  });

  it('throws ManifestRegistryError on non-2xx', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response('Forbidden', { status: 403, statusText: 'Forbidden' }),
    );
    await expect(listManifests()).rejects.toSatisfy(
      (e: unknown) => e instanceof ManifestRegistryError && (e as ManifestRegistryError).statusCode === 403,
    );
  });

  it('throws ManifestRegistryError with statusCode 0 on network error', async () => {
    vi.mocked(authFetchModule.authFetch).mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(listManifests()).rejects.toSatisfy(
      (e: unknown) => e instanceof ManifestRegistryError && (e as ManifestRegistryError).statusCode === 0,
    );
  });
});

// silence unused import warning — apiConfig is used only for vi.mock hoisting
void apiConfig;

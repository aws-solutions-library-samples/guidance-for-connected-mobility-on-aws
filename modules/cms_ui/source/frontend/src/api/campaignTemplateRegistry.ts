// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// spec 2026-09-01-cms-campaign-follows-enrollment § D4
// Fetches campaign templates from the existing campaigns endpoint and filters
// client-side to rows whose targetArn === 'template'.

import { authFetch } from '@/utils/authFetch';
import { getDataProcessingApiEndpoint } from '@/config/api';

export interface CampaignTemplate {
  campaignId: string;
  campaignName?: string;
  decoderManifestId?: string;
  category?: string;
  description?: string;
  targetArn: 'template';
}

export interface CampaignTemplateListResponse {
  campaignTemplates: CampaignTemplate[];
}

export class CampaignTemplateRegistryError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'CampaignTemplateRegistryError';
  }
}

export async function listCampaignTemplates(): Promise<CampaignTemplateListResponse> {
  const url = `${getDataProcessingApiEndpoint()}campaigns`;

  let response: Response;
  try {
    response = await authFetch(url);
  } catch {
    throw new CampaignTemplateRegistryError('Network error fetching campaign template registry', 0);
  }

  if (!response.ok) {
    throw new CampaignTemplateRegistryError(
      `Campaign template registry request failed: ${response.status} ${response.statusText}`,
      response.status,
    );
  }

  const data = await response.json();
  const allCampaigns: Array<CampaignTemplate & { targetArn: string }> = data.campaigns ?? [];

  // Filter client-side: return only rows that are templates.
  // Existing VehicleCampaignsTable.tsx:88 already uses this pattern.
  const campaignTemplates = allCampaigns.filter(
    (c): c is CampaignTemplate => c.targetArn === 'template',
  );

  return { campaignTemplates };
}

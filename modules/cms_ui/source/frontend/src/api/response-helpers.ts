// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { PaginatedResponse, ListRequestParams } from './types';

/**
 * Helper to get total count using multiple strategies
 */
export async function getTotalCount(
  makeRequest: (endpoint: string) => Promise<any>,
  resourceType: string,
  input: ListRequestParams = {}
): Promise<number> {
  try {
    console.log(`🔢 Getting total count for ${resourceType}...`);
    
    // Strategy 1: Try count endpoint
    try {
      const countEndpoint = `/api/v1/${resourceType}/count`;
      console.log(`📡 Trying count endpoint: ${countEndpoint}`);
      
      const countResponse = await makeRequest(countEndpoint);
      console.log(`✅ Count response for ${resourceType}:`, countResponse);
      
      if (countResponse.total || countResponse.count) {
        const total = countResponse.total || countResponse.count;
        console.log(`🎯 Using total from count endpoint for ${resourceType}:`, total);
        return total;
      }
    } catch (countError) {
      console.log(`⚠️ Count endpoint not available for ${resourceType}:`, countError.message);
    }
    
    // Strategy 2: Try high limit request
    try {
      const queryParams = new URLSearchParams();
      queryParams.append('limit', '10000'); // High limit to get all items
      
      // Add filters but exclude pagination
      Object.entries(input).forEach(([key, value]) => {
        if (key !== 'limit' && key !== 'lastKey' && value !== undefined) {
          queryParams.append(key, value.toString());
        }
      });
      
      const highLimitEndpoint = `/api/v1/${resourceType}?${queryParams.toString()}`;
      console.log(`📡 Trying high limit request for ${resourceType}:`, highLimitEndpoint);
      
      const highLimitResponse = await makeRequest(highLimitEndpoint);
      console.log(`✅ High limit response for ${resourceType}:`, {
        itemCount: highLimitResponse[resourceType]?.length,
        total: highLimitResponse.total,
        hasMore: highLimitResponse.hasMore
      });
      
      if (!highLimitResponse.hasMore && highLimitResponse[resourceType]?.length) {
        const total = highLimitResponse[resourceType].length;
        console.log(`🎯 Using item count from high limit request for ${resourceType}:`, total);
        return total;
      } else if (highLimitResponse.total) {
        console.log(`🎯 Using total from high limit request for ${resourceType}:`, highLimitResponse.total);
        return highLimitResponse.total;
      }
    } catch (highLimitError) {
      console.log(`⚠️ High limit request failed for ${resourceType}:`, highLimitError.message);
    }
    
    // Strategy 3: Fallback to 0 (will be updated when we get the actual response)
    console.log(`📊 No total count available for ${resourceType}, will use response total`);
    return 0;
    
  } catch (error) {
    console.log(`⚠️ Error getting total count for ${resourceType}:`, error.message);
    return 0;
  }
}

/**
 * Standardize API response format
 */
export function standardizeResponse<T>(
  response: any,
  resourceType: string,
  totalCount?: number
): PaginatedResponse<T> & { [key: string]: T[] } {
  const items = response[resourceType] || [];
  const actualTotal = totalCount || response.total || response.count || items.length;
  
  const standardized = {
    items,
    total: actualTotal,
    count: items.length,
    hasMore: response.hasMore || false,
    lastKey: response.lastKey || response.last_key,
    message: response.message || `Found ${items.length} ${resourceType}`,
    // Include the original resource array for backward compatibility
    [resourceType]: items
  };
  
  console.log(`📊 Standardized response for ${resourceType}:`, {
    itemCount: items.length,
    total: actualTotal,
    hasMore: standardized.hasMore
  });
  
  return standardized as PaginatedResponse<T> & { [key: string]: T[] };
}

/**
 * Build query parameters for list requests
 */
export function buildQueryParams(input: ListRequestParams): URLSearchParams {
  const queryParams = new URLSearchParams();
  
  Object.entries(input).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      // Convert camelCase to snake_case for API compatibility
      const apiKey = key.replace(/([A-Z])/g, '_$1').toLowerCase();
      queryParams.append(apiKey, value.toString());
    }
  });
  
  return queryParams;
}

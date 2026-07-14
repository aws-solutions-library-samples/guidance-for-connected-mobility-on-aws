// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Standardized paginated API response structure
 * All list/fetch operations should follow this pattern
 */
export interface PaginatedResponse<T> {
  /** Array of items for current page */
  items: T[];
  
  /** Total count of all items across all pages (from cache/counter) */
  total: number;
  
  /** Count of items in current page */
  count: number;
  
  /** Whether there are more pages available */
  hasMore: boolean;
  
  /** Pagination key for next page */
  lastKey?: string;
  
  /** Human-readable message */
  message?: string;
}

/**
 * Standardized list request parameters
 */
export interface ListRequestParams {
  /** Number of items per page (default: 100) */
  limit?: number;
  
  /** Pagination key from previous response */
  lastKey?: string;
  
  /** Filter by status */
  status?: string;
  
  /** Additional filters */
  [key: string]: any;
}

/**
 * Vehicle-specific response
 */
export interface VehicleListResponse extends PaginatedResponse<any> {
  vehicles: any[];
}

/**
 * Fleet-specific response
 */
export interface FleetListResponse extends PaginatedResponse<any> {
  fleets: any[];
}

/**
 * Trip-specific response
 */
export interface TripListResponse extends PaginatedResponse<any> {
  trips: any[];
}

/**
 * Safety event-specific response
 */
export interface SafetyEventListResponse extends PaginatedResponse<any> {
  safetyEvents: any[];
}

/**
 * Maintenance event-specific response
 */
export interface MaintenanceEventListResponse extends PaginatedResponse<any> {
  maintenanceEvents: any[];
}

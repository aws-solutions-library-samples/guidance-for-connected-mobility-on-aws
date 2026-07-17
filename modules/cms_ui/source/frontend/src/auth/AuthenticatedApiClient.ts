// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useAuth } from './useAuth';

export interface ApiClientConfig {
  baseUrl: string;
  timeout?: number;
  retryAttempts?: number;
  isDemoMode?: boolean;
}

export interface ApiResponse<T = any> {
  data: T;
  status: number;
  statusText: string;
  headers: Record<string, string>;
}

export interface ApiError {
  message: string;
  status?: number;
  code?: string;
  details?: any;
}

export class AuthenticatedApiClient {
  private config: ApiClientConfig;
  private getAuthHeaders: () => Record<string, string>;
  private isTokenValid: () => boolean;
  private login: () => void;

  constructor(
    config: ApiClientConfig,
    getAuthHeaders: () => Record<string, string>,
    isTokenValid: () => boolean,
    login: () => void
  ) {
    this.config = config;
    this.getAuthHeaders = getAuthHeaders;
    this.isTokenValid = isTokenValid;
    this.login = login;
  }

  private async makeRequest<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<ApiResponse<T>> {
    const url = `${this.config.baseUrl}${endpoint}`;
    
    // Check token validity before making request (unless in demo mode)
    if (!this.config.isDemoMode && !this.isTokenValid()) {
      console.warn('Token is invalid, redirecting to login');
      this.login();
      throw new Error('Authentication required');
    }

    // Prepare headers
    const headers = {
      'Content-Type': 'application/json',
      ...this.getAuthHeaders(),
      ...options.headers,
    };

    // Prepare request options
    const requestOptions: RequestInit = {
      ...options,
      headers,
      timeout: this.config.timeout || 30000,
    };

    try {
      const response = await fetch(url, requestOptions);
      
      // Handle authentication errors
      if (response.status === 401) {
        console.warn('Received 401, token may be expired');
        if (!this.config.isDemoMode) {
          this.login();
        }
        throw new ApiError({
          message: 'Authentication failed',
          status: 401,
          code: 'UNAUTHORIZED',
        });
      }

      // Handle other HTTP errors
      if (!response.ok) {
        const errorText = await response.text();
        let errorData;
        
        try {
          errorData = JSON.parse(errorText);
        } catch {
          errorData = { message: errorText };
        }

        throw new ApiError({
          message: errorData.message || `HTTP ${response.status}: ${response.statusText}`,
          status: response.status,
          code: errorData.code || 'HTTP_ERROR',
          details: errorData,
        });
      }

      // Parse response
      const contentType = response.headers.get('content-type');
      let data: T;

      if (contentType && contentType.includes('application/json')) {
        data = await response.json();
      } else {
        data = await response.text() as unknown as T;
      }

      // Convert headers to object
      const responseHeaders: Record<string, string> = {};
      response.headers.forEach((value, key) => {
        responseHeaders[key] = value;
      });

      return {
        data,
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      };

    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }

      // Handle network errors
      if (error instanceof TypeError && error.message.includes('fetch')) {
        throw new ApiError({
          message: 'Network error: Unable to connect to the server',
          code: 'NETWORK_ERROR',
          details: error,
        });
      }

      // Handle timeout errors
      if (error instanceof Error && error.name === 'AbortError') {
        throw new ApiError({
          message: 'Request timeout',
          code: 'TIMEOUT',
          details: error,
        });
      }

      // Handle other errors
      throw new ApiError({
        message: error instanceof Error ? error.message : 'Unknown error occurred',
        code: 'UNKNOWN_ERROR',
        details: error,
      });
    }
  }

  // HTTP methods
  async get<T>(endpoint: string, params?: Record<string, any>): Promise<ApiResponse<T>> {
    let url = endpoint;
    
    if (params) {
      const searchParams = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          searchParams.append(key, String(value));
        }
      });
      
      if (searchParams.toString()) {
        url += `?${searchParams.toString()}`;
      }
    }

    return this.makeRequest<T>(url, { method: 'GET' });
  }

  async post<T>(endpoint: string, data?: any): Promise<ApiResponse<T>> {
    return this.makeRequest<T>(endpoint, {
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    });
  }

  async put<T>(endpoint: string, data?: any): Promise<ApiResponse<T>> {
    return this.makeRequest<T>(endpoint, {
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    });
  }

  async patch<T>(endpoint: string, data?: any): Promise<ApiResponse<T>> {
    return this.makeRequest<T>(endpoint, {
      method: 'PATCH',
      body: data ? JSON.stringify(data) : undefined,
    });
  }

  async delete<T>(endpoint: string): Promise<ApiResponse<T>> {
    return this.makeRequest<T>(endpoint, { method: 'DELETE' });
  }
}

// Custom error class
class ApiError extends Error {
  public status?: number;
  public code?: string;
  public details?: any;

  constructor({ message, status, code, details }: {
    message: string;
    status?: number;
    code?: string;
    details?: any;
  }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// Hook to create an authenticated API client
export const useAuthenticatedApiClient = (config: ApiClientConfig): AuthenticatedApiClient => {
  const auth = useAuth();

  return new AuthenticatedApiClient(
    config,
    auth.getAuthHeaders,
    auth.isTokenValid,
    auth.login
  );
};

export { ApiError };

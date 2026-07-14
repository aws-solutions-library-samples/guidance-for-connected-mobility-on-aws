// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * API Configuration Service
 * Handles authentication, base URLs, and common API configuration
 * for AIOT Management Console integration
 */

export interface ApiConfig {
  baseUrl: string;
  authHeaders: Record<string, string>;
  timeout: number;
  retryAttempts: number;
}

class ApiConfigService {
  private config: ApiConfig;

  constructor() {
    this.config = {
      baseUrl: this.getBaseUrl(),
      authHeaders: {},
      timeout: 5000, // 5 seconds - reduced from 30 seconds
      retryAttempts: 3,
    };
  }

  /**
   * Get the base URL for API calls
   * This should be configured based on environment
   */
  private getBaseUrl(): string {
    // Check for environment variables or runtime config
    if (typeof window !== 'undefined') {
      // Browser environment - check for runtime config
      const runtimeConfig = (window as any).runtimeConfig;
      if (runtimeConfig?.apiEndpoint) {
        return runtimeConfig.apiEndpoint;
      }
    }

    // Check for Vite environment variables
    const envApiUrl = import.meta.env.VITE_API_BASE_URL;
    if (envApiUrl) {
      return envApiUrl;
    }

    // Default to relative path for development
    return '/api';
  }

  /**
   * Set authentication headers
   * This will be called when user authentication is available
   */
  setAuthHeaders(headers: Record<string, string>) {
    this.config.authHeaders = { ...headers };
  }

  /**
   * Set authorization token (common case)
   */
  setAuthToken(token: string, tokenType: string = 'Bearer') {
    this.config.authHeaders = {
      ...this.config.authHeaders,
      'Authorization': `${tokenType} ${token}`,
    };
  }

  /**
   * Set AWS Cognito authentication headers
   */
  setCognitoAuth(idToken: string, accessToken?: string) {
    this.config.authHeaders = {
      ...this.config.authHeaders,
      'Authorization': `Bearer ${idToken}`,
    };
    
    if (accessToken) {
      this.config.authHeaders['X-Access-Token'] = accessToken;
    }
  }

  /**
   * Set AWS IAM authentication headers (for service-to-service calls)
   */
  setIamAuth(accessKeyId: string, secretAccessKey: string, sessionToken?: string) {
    this.config.authHeaders = {
      ...this.config.authHeaders,
      'X-Amz-Access-Key-Id': accessKeyId,
      'X-Amz-Secret-Access-Key': secretAccessKey,
    };
    
    if (sessionToken) {
      this.config.authHeaders['X-Amz-Security-Token'] = sessionToken;
    }
  }

  /**
   * Clear authentication headers
   */
  clearAuth() {
    this.config.authHeaders = {};
  }

  /**
   * Get current configuration
   */
  getConfig(): ApiConfig {
    return { ...this.config };
  }

  /**
   * Update base URL
   */
  setBaseUrl(baseUrl: string) {
    this.config.baseUrl = baseUrl;
  }

  /**
   * Make authenticated HTTP request with retry logic
   */
  async makeRequest<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.config.baseUrl}${endpoint}`;
    
    const requestOptions: RequestInit = {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...this.config.authHeaders,
        ...options.headers,
      },
    };

    let lastError: Error;
    
    // Retry logic
    for (let attempt = 1; attempt <= this.config.retryAttempts; attempt++) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.config.timeout);
        
        const response = await fetch(url, {
          ...requestOptions,
          signal: controller.signal,
        });
        
        clearTimeout(timeoutId);

        if (!response.ok) {
          // Handle specific HTTP errors
          if (response.status === 401) {
            throw new Error('Unauthorized - Please check your authentication');
          }
          if (response.status === 403) {
            throw new Error('Forbidden - Insufficient permissions');
          }
          if (response.status === 404) {
            throw new Error('Not Found - The requested resource does not exist');
          }
          if (response.status >= 500) {
            throw new Error(`Server Error (${response.status}) - Please try again later`);
          }
          
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        // Handle empty responses
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
          return await response.json();
        } else {
          return await response.text() as unknown as T;
        }
        
      } catch (error) {
        lastError = error as Error;
        
        // Don't retry on authentication errors or client errors
        if (error instanceof Error) {
          if (error.message.includes('Unauthorized') || 
              error.message.includes('Forbidden') ||
              error.message.includes('404')) {
            throw error;
          }
        }
        
        // Wait before retrying (exponential backoff)
        if (attempt < this.config.retryAttempts) {
          const delay = Math.pow(2, attempt - 1) * 1000; // 1s, 2s, 4s...
          await new Promise(resolve => setTimeout(resolve, delay));
        }
      }
    }
    
    throw lastError!;
  }

  /**
   * Health check endpoint
   */
  async healthCheck(): Promise<{ status: string; timestamp: string }> {
    try {
      return await this.makeRequest<{ status: string; timestamp: string }>('/health');
    } catch (error) {
      console.warn('Health check failed:', error);
      return {
        status: 'unknown',
        timestamp: new Date().toISOString(),
      };
    }
  }

  /**
   * Initialize API configuration from runtime config or environment
   */
  async initialize(): Promise<void> {
    try {
      // Perform health check to validate configuration
      const health = await this.healthCheck();
      console.log('API Configuration initialized successfully:', health);
    } catch (error) {
      console.warn('API Configuration initialization failed:', error);
      // Continue with fallback configuration
    }
  }
}

// Export singleton instance
export const apiConfigService = new ApiConfigService();
export default apiConfigService;

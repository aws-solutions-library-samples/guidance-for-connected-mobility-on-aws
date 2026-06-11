// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Note: This file previously used Smithy packages for SigV4 signing
// If SigV4 signing is needed, consider using AWS SDK v3 directly
// or implement a custom signing solution

export interface SigV4MiddlewareConfig {
  region: string;
  service: string;
  credentials: any; // Replace with appropriate credential type
}

/**
 * Placeholder for SigV4 middleware - implement as needed
 */
export const createSigV4Middleware = (config: SigV4MiddlewareConfig) => {
  return {
    // Implement SigV4 signing logic here if needed
    sign: (request: any) => {
      console.log('SigV4 signing not implemented');
      return request;
    }
  };
};
 */
export interface SigV4MiddlewareConfig {
  /**
   * The AWS region to use for signing
   */
  region: string;
  /**
   * The service name to use for signing (e.g., 'execute-api')
   */
  service: string;
  /**
   * Function that returns AWS credentials
   */
  getCredentials: () => Promise<AwsCredentialIdentity>;
}

/**
 * Creates a middleware that signs requests with AWS SigV4
 * @param config The SigV4 middleware configuration
 * @returns A middleware function that signs requests
 */
export const createSigV4Middleware = (config: SigV4MiddlewareConfig) => {
  const { region, service, getCredentials } = config;

  /**
   * Signs a request with AWS SigV4
   * @param url The request URL
   * @param method The request method
   * @param headers The request headers
   * @param body The request body
   * @returns The signed request headers
   */
  const signRequest = async (
    url: string,
    method: string,
    headers: Record<string, string>,
    body?: string
  ): Promise<Record<string, string>> => {
    try {
      const credentials = await getCredentials();
      
      const parsedUrl = new URL(url);
      
      const request = new HttpRequest({
        method,
        protocol: parsedUrl.protocol.replace(":", ""),
        hostname: parsedUrl.hostname,
        port: parsedUrl.port ? parseInt(parsedUrl.port) : undefined,
        path: parsedUrl.pathname,
        query: Object.fromEntries(parsedUrl.searchParams.entries()),
        headers: {
          ...headers,
          host: parsedUrl.host,
        },
        body: body ? toUtf8(body) : undefined,
      });

      const signer = new SignatureV4({
        credentials,
        region,
        service,
        sha256: async (data: Uint8Array) => {
          const hashBuffer = await crypto.subtle.digest("SHA-256", data);
          return new Uint8Array(hashBuffer);
        },
      });

      const signedRequest = await signer.sign(request);
      
      return signedRequest.headers;
    } catch (error) {
      console.error("Error signing request with SigV4:", error);
      throw error;
    }
  };

  return {
    signRequest,
  };
};
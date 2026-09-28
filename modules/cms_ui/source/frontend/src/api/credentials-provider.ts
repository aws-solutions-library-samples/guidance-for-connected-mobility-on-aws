// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { CognitoIdentityClient } from "@aws-sdk/client-cognito-identity";
import { fromCognitoIdentityPool } from "@aws-sdk/credential-provider-cognito-identity";

// Use AWS SDK types directly instead of Smithy
export interface AwsCredentialIdentity {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
}

export interface AwsCredentialsConfig {
  identityPoolId: string;
  userPoolId: string;
  region: string;
}

export class CredentialsProvider {
  private config: AwsCredentialsConfig;
  private token: string;
  private credentials: AwsCredentialIdentity | null = null;
  private expirationTime: number = 0;
  private refreshPromise: Promise<AwsCredentialIdentity> | null = null;

  constructor(config: AwsCredentialsConfig, token: string) {
    this.config = config;
    this.token = token;
  }

  /**
   * Updates the token used for authentication
   * @param token The new token to use
   */
  public updateToken(token: string): void {
    this.token = token;
    // Reset credentials to force refresh on next getCredentials call
    this.credentials = null;
    this.expirationTime = 0;
  }

  /**
   * Gets AWS credentials from Cognito Identity Pool
   * @returns A promise that resolves to AWS credentials
   */
  public async getCredentials(): Promise<AwsCredentialIdentity> {
    // If we have valid credentials that aren't about to expire, return them
    const now = Date.now();
    if (this.credentials && this.expirationTime > now + 60000) {
      return this.credentials;
    }

    // If we're already refreshing credentials, return that promise
    if (this.refreshPromise) {
      return this.refreshPromise;
    }

    // Otherwise, refresh credentials
    this.refreshPromise = this.refreshCredentials();
    
    try {
      this.credentials = await this.refreshPromise;
      // Set expiration time to 55 minutes from now (default is 1 hour)
      this.expirationTime = now + 55 * 60 * 1000;
      return this.credentials;
    } finally {
      this.refreshPromise = null;
    }
  }

  /**
   * Refreshes AWS credentials from Cognito Identity Pool
   * @returns A promise that resolves to AWS credentials
   */
  private async refreshCredentials(): Promise<AwsCredentialIdentity> {
    if (!this.token) {
      throw new Error("No authentication token available");
    }

    try {
      console.log("Refreshing AWS credentials from Cognito Identity Pool");
      
      const identityProvider = `cognito-idp.${this.config.region}.amazonaws.com/${this.config.userPoolId}`;
      
      const credentialsProvider = fromCognitoIdentityPool({
        client: new CognitoIdentityClient({ region: this.config.region }),
        identityPoolId: this.config.identityPoolId,
        logins: {
          [identityProvider]: this.token
        }
      });

      return await credentialsProvider();
    } catch (error) {
      console.error("Error refreshing AWS credentials:", error);
      throw error;
    }
  }
}
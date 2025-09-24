// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// S3 bucket interface
export interface S3Bucket {
  name: string;
  creationDate: Date;
}

// S3 object interface
export interface S3Object {
  key: string;
  lastModified: Date;
  size: number;
  etag: string;
}

/**
 * AWS Service client for interacting with AWS services via API Gateway
 */
export class AwsService {
  private apiUrl: string;

  constructor() {
    // For now, use mock data
    this.apiUrl = '';
  }

  /**
   * List S3 buckets in the AWS account
   * @returns Promise with array of S3 buckets
   */
  async listS3Buckets(): Promise<S3Bucket[]> {
    try {
      // Return mock data for now
      console.log('Using mock S3 bucket data');
      return [
        { name: 'example-bucket-1', creationDate: new Date() },
        { name: 'example-bucket-2', creationDate: new Date() }
      ];
    } catch (error) {
      console.error('Error listing S3 buckets:', error);
      throw error;
    }
  }

  /**
   * List objects in an S3 bucket
   * @param bucket The bucket name
   * @param prefix Optional prefix to filter objects
   * @returns Promise with array of S3 objects
   */
  async listS3Objects(bucket: string, prefix: string = ''): Promise<S3Object[]> {
    try {
      // Return mock data for now
      console.log(`Using mock S3 object data for bucket: ${bucket}, prefix: ${prefix}`);
      return [
        { key: 'example-file-1.json', lastModified: new Date(), size: 1024, etag: '"abc123"' },
        { key: 'example-file-2.json', lastModified: new Date(), size: 2048, etag: '"def456"' }
      ];
    } catch (error) {
      console.error(`Error listing objects in bucket ${bucket}:`, error);
      throw error;
    }
  }
}

// Create a singleton instance
const awsService = new AwsService();
export default awsService;
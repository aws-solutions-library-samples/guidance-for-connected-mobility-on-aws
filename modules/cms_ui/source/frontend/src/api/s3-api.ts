// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import awsService, { S3Bucket } from './aws-service';

/**
 * API for S3 operations
 */
export const s3Api = {
  /**
   * List all S3 buckets
   * @returns Promise with array of S3 buckets
   */
  listBuckets: async (): Promise<S3Bucket[]> => {
    return awsService.listS3Buckets();
  },
  
  /**
   * List objects in an S3 bucket with optional prefix
   * @param bucketName The name of the bucket
   * @param prefix Optional prefix to filter objects
   * @returns Promise with array of S3 objects
   */
  listObjects: async (bucketName: string, prefix?: string): Promise<any[]> => {
    // This would be implemented when needed
    return [];
  }
};

export default s3Api;
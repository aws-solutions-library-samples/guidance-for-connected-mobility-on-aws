#!/usr/bin/env python3
"""
Fix CloudFront access to S3 bucket
"""

import boto3
import json
import time

def invalidate_cloudfront(distribution_id: str):
    """Invalidate CloudFront cache"""
    cloudfront = boto3.client('cloudfront')
    
    try:
        response = cloudfront.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                'Paths': {
                    'Quantity': 1,
                    'Items': ['/*']
                },
                'CallerReference': str(int(time.time()))
            }
        )
        print(f"✅ CloudFront invalidation created: {response['Invalidation']['Id']}")
        return True
    except Exception as e:
        print(f"❌ Error invalidating CloudFront: {e}")
        return False

def main():
    # Get CloudFront distribution ID from URL
    cloudfront_url = "https://d13d2q5vq7u93s.cloudfront.net"
    distribution_id = cloudfront_url.split("//")[1].split(".")[0]
    
    print(f"🔄 Invalidating CloudFront distribution: {distribution_id}")
    
    success = invalidate_cloudfront(distribution_id)
    
    if success:
        print("🎉 CloudFront cache invalidated!")
        print("⏳ Wait 5-10 minutes for propagation, then try the URL again")
        return True
    else:
        print("❌ Failed to invalidate cache")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)

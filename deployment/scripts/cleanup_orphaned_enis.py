#!/usr/bin/env python3
"""
Clean up orphaned IoT VPC destination ENIs
"""

import boto3
import time

def cleanup_orphaned_iot_enis():
    """Clean up orphaned IoT VPC destination ENIs"""
    ec2 = boto3.client('ec2', region_name='us-east-1')
    iot = boto3.client('iot', region_name='us-east-1')
    
    # Get current VPC destinations
    destinations = iot.list_topic_rule_destinations()
    current_destination_arns = [dest['arn'] for dest in destinations.get('destinationSummaries', [])]
    
    print(f"📊 Found {len(current_destination_arns)} current VPC destinations")
    for arn in current_destination_arns:
        print(f"   - {arn}")
    
    # Get all IoT VPC destination ENIs
    response = ec2.describe_network_interfaces(
        Filters=[
            {'Name': 'description', 'Values': ['DO NOT DELETE - AWS IoT Rules Engine managed ENI for VPCDestination*']},
            {'Name': 'status', 'Values': ['available']}
        ]
    )
    
    enis_to_delete = []
    
    for eni in response['NetworkInterfaces']:
        description = eni['Description']
        # Extract VPC destination ARN from description
        if 'VPCDestination arn:aws:iot:' in description:
            dest_arn = description.split('VPCDestination ')[1]
            
            # Check if this ENI belongs to a current destination
            if dest_arn not in current_destination_arns:
                enis_to_delete.append({
                    'eni_id': eni['NetworkInterfaceId'],
                    'destination_arn': dest_arn,
                    'vpc_id': eni['VpcId'],
                    'subnet_id': eni['SubnetId']
                })
    
    print(f"\n🗑️  Found {len(enis_to_delete)} orphaned ENIs to delete")
    
    if not enis_to_delete:
        print("✅ No orphaned ENIs found")
        return True
    
    # Delete orphaned ENIs
    deleted_count = 0
    for eni_info in enis_to_delete:
        try:
            print(f"Deleting ENI {eni_info['eni_id']} from orphaned destination...")
            ec2.delete_network_interface(NetworkInterfaceId=eni_info['eni_id'])
            deleted_count += 1
            time.sleep(0.5)  # Rate limiting
        except Exception as e:
            print(f"❌ Failed to delete {eni_info['eni_id']}: {e}")
    
    print(f"\n✅ Deleted {deleted_count}/{len(enis_to_delete)} orphaned ENIs")
    return True

def main():
    print("🧹 Cleaning up orphaned IoT VPC destination ENIs...")
    
    success = cleanup_orphaned_iot_enis()
    
    if success:
        print("\n🎉 ENI cleanup complete!")
        print("💡 The current VPC destination should now be able to complete")
        return True
    else:
        print("\n❌ ENI cleanup failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)

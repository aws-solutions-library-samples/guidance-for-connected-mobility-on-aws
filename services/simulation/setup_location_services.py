#!/usr/bin/env python3
"""
Setup Amazon Location Services resources for route calculation
"""

import boto3
import json

def setup_location_services(profile_name="target-account", region="us-east-1"):
    """Setup required Location Services resources"""
    
    session = boto3.Session(profile_name=profile_name)
    location_client = session.client('location', region_name=region)
    
    try:
        # Create route calculator
        calculator_name = 'cms-route-calculator'
        
        print(f"🗺️ Creating route calculator: {calculator_name}")
        
        location_client.create_route_calculator(
            CalculatorName=calculator_name,
            DataSource='Esri',  # or 'Here'
            Description='Route calculator for CMS telemetry simulation',
            Tags={
                'Project': 'ConnectedMobility',
                'Environment': 'Simulation'
            }
        )
        
        print(f"✅ Route calculator created: {calculator_name}")
        
        # Verify calculator is active
        response = location_client.describe_route_calculator(
            CalculatorName=calculator_name
        )
        
        print(f"📊 Calculator created successfully")
        print(f"📊 Data Source: {response.get('DataSource', 'Unknown')}")
        
        return True
        
    except location_client.exceptions.ConflictException:
        print(f"✅ Route calculator already exists: {calculator_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error setting up Location Services: {e}")
        return False

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Setup Amazon Location Services')
    parser.add_argument('--profile', default='target-account', help='AWS profile name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    
    args = parser.parse_args()
    
    success = setup_location_services(args.profile, args.region)
    if success:
        print("🎉 Location Services setup completed!")
    else:
        print("❌ Location Services setup failed!")

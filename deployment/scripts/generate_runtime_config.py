#!/usr/bin/env python3
"""
Generate runtimeConfig.json from CDK stack outputs
"""

import boto3
import json
import os
import sys

def get_stack_outputs(stack_name: str, profile: str):
    """Get stack outputs"""
    session = boto3.Session(profile_name=profile)
    cf = session.client('cloudformation')
    
    try:
        response = cf.describe_stacks(StackName=stack_name)
        outputs = {}
        if 'Outputs' in response['Stacks'][0]:
            for output in response['Stacks'][0]['Outputs']:
                outputs[output['OutputKey']] = output['OutputValue']
        return outputs
    except Exception as e:
        print(f"❌ Error getting stack outputs for {stack_name}: {e}")
        return {}

def generate_runtime_config(deployment_stage: str, profile: str):
    """Generate runtime config from stack outputs"""
    
    # Get UI stack outputs
    ui_outputs = get_stack_outputs(f"cms-{deployment_stage}-ui", profile)
    
    if not ui_outputs:
        print(f"❌ No outputs found for cms-{deployment_stage}-ui stack")
        return False
    
    # Resolve map name. Prefer the stack output (MapName / LocationMapName) if
    # it exists; fall back to the actually-deployed map for prod
    # (cvs_location_map_test2 — HERE-sourced via v1 Maps API). Hardcoding
    # "cms-map" here used to break maps in prod when this script ran during
    # CDK redeploys, because that map doesn't exist in the prod account.
    map_name = (
        ui_outputs.get('MapName')
        or ui_outputs.get('LocationMapName')
        or os.environ.get('MAP_NAME')
        or 'cvs_location_map_test2'
    )

    # Generate runtime config
    runtime_config = {
        "awsRegion": "us-east-1",
        "mapAuth": {
            "identityPoolClient": f"cognito-idp.us-east-1.amazonaws.com/{ui_outputs.get('UserPoolId', '')}",
            "mapName": map_name,
            "identityPoolId": ui_outputs.get('IdentityPoolId', '')
        },
        "locationServices": {
            "mapName": map_name,
            "placeIndexName": ui_outputs.get('PlaceIndexName', f'cms-{deployment_stage}-ui-place-index'),
            "routeCalculatorName": ui_outputs.get('RouteCalculatorName', f'cms-{deployment_stage}-ui-route-calculator'),
            "region": "us-east-1",
            "enabled": True
        },
        "isDemoMode": "false",
        "apiEndpoint": ui_outputs.get('APIEndpoint', ''),
        "wsEndpoint": ui_outputs.get('WebSocketEndpoint', ''),
        "userPreferencesApiEndpoint": ui_outputs.get('APIEndpoint', ''),
        "cognitoDomain": os.environ.get('COGNITO_DOMAIN', ''),
        "awsCredentials": {
            "region": "us-east-1",
            "identityPoolId": ui_outputs.get('IdentityPoolId', ''),
            "userPoolId": ui_outputs.get('UserPoolId', ''),
            "userPoolWebClientId": ui_outputs.get('UserPoolClientId', '')
        }
    }
    
    # Write to frontend public directory
    frontend_dir = "../../modules/cms_ui/source/frontend/public"
    config_path = os.path.join(frontend_dir, "runtimeConfig.json")
    
    try:
        with open(config_path, 'w') as f:
            json.dump(runtime_config, f, indent=2)
        
        print(f"✅ Generated runtime config: {config_path}")
        print(f"   API Endpoint: {runtime_config['apiEndpoint']}")
        print(f"   User Pool: {runtime_config['awsCredentials']['userPoolId']}")
        print(f"   Identity Pool: {runtime_config['awsCredentials']['identityPoolId']}")
        return True
        
    except Exception as e:
        print(f"❌ Error writing runtime config: {e}")
        return False

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    profile = os.environ.get('AWS_PROFILE', 'default')
    
    print(f"🔧 Generating runtime config for stage: {deployment_stage}, profile: {profile}")
    
    success = generate_runtime_config(deployment_stage, profile)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

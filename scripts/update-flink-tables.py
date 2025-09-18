#!/usr/bin/env python3
import sys
import json
import subprocess
from pathlib import Path

# Add config to path
sys.path.append(str(Path(__file__).parent.parent / ".config"))
from table_config import TableConfig

def update_flink_deployment(environment="prod"):
    """Update Flink deployment script with current table names"""
    config = TableConfig()
    flink_props = config.get_flink_properties(environment)
    
    deploy_script = Path(__file__).parent.parent / "modules/flink/deploy_to_kda.sh"
    
    # Read current script
    with open(deploy_script) as f:
        content = f.read()
    
    # Update table names in the PropertyMap section
    property_map = {
        **flink_props,
        "bootstrap.servers": "b-1.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9096,b-2.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9096",
        "group.id": "flink-telemetry-consumer",
        "sasl.jaas.config": f"org.apache.kafka.common.security.scram.ScramLoginModule required username=\\\"{os.environ.get('IOT_USERNAME', 'iot-user')}\\\" password=\\\"{os.environ.get('IOT_PASSWORD', 'CHANGE_ME')}\\\";",
        "sasl.mechanism": "SCRAM-SHA-512",
        "security.protocol": "SASL_SSL"
    }
    
    print(f"Updating Flink deployment for {environment} environment:")
    for key, value in flink_props.items():
        print(f"  {key}: {value}")
    
    # Generate new PropertyMap JSON
    property_map_json = json.dumps(property_map, indent=24).replace('\n', '\n                        ')
    
    # Replace the PropertyMap section
    start_marker = '"PropertyMap": {'
    end_marker = '                    }'
    
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker, start_idx) + len(end_marker)
    
    if start_idx != -1 and end_idx != -1:
        new_content = (
            content[:start_idx] + 
            f'"PropertyMap": {property_map_json}' +
            content[end_idx:]
        )
        
        # Write updated script
        with open(deploy_script, 'w') as f:
            f.write(new_content)
        
        print(f"✅ Updated {deploy_script}")
    else:
        print("❌ Could not find PropertyMap section in deployment script")

if __name__ == "__main__":
    environment = sys.argv[1] if len(sys.argv) > 1 else "prod"
    update_flink_deployment(environment)

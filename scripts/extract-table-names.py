#!/usr/bin/env python3
import re
import json
from pathlib import Path

def extract_table_names():
    """Extract table names from Lambda handler code"""
    workspace = Path(__file__).parent.parent
    
    # Search for TABLE_NAME environment variable usage
    table_names = {}
    
    # Check main API handler
    main_api = workspace / "modules/cms_ui/source/handlers/main_api/index.py"
    if main_api.exists():
        with open(main_api) as f:
            content = f.read()
            
        # Extract default table names from os.environ.get() calls
        patterns = [
            (r"os\.environ\.get\('VEHICLES_TABLE_NAME'[^)]*'([^']+)'", "vehicles"),
            (r"os\.environ\.get\('FLEETS_TABLE_NAME'[^)]*'([^']+)'", "fleets"),
            (r"os\.environ\.get\('TRIPS_TABLE_NAME'[^)]*'([^']+)'", "trips"),
            (r"os\.environ\.get\('SAFETY_EVENTS_TABLE_NAME'[^)]*'([^']+)'", "safety_events"),
            (r"os\.environ\.get\('MAINTENANCE_ALERTS_TABLE_NAME'[^)]*'([^']+)'", "maintenance_alerts"),
            (r"os\.environ\.get\('VEHICLE_CERTIFICATES_TABLE_NAME'[^)]*'([^']+)'", "vehicle_certificates"),
            (r"os\.environ\.get\('USER_PREFERENCES_TABLE_NAME'[^)]*'([^']+)'", "user_preferences"),
            (r"os\.environ\.get\('CACHE_TABLE_NAME'[^)]*'([^']+)'", "dashboard_metrics_cache")
        ]
        
        for pattern, key in patterns:
            match = re.search(pattern, content)
            if match:
                table_names[key] = match.group(1)
    
    # Check Flink deployment for telemetry table
    flink_deploy = workspace / "modules/flink/deploy_to_kda.sh"
    if flink_deploy.exists():
        with open(flink_deploy) as f:
            content = f.read()
            
        match = re.search(r'"TELEMETRY_TABLE_NAME":\s*"([^"]+)"', content)
        if match:
            table_names["telemetry"] = match.group(1)
    
    return table_names

def update_config():
    """Update table-config.json with extracted names"""
    config_file = Path(__file__).parent.parent / ".config/table-config.json"
    
    # Load current config
    with open(config_file) as f:
        config = json.load(f)
    
    # Extract table names from code
    extracted = extract_table_names()
    
    print("Extracted table names from code:")
    for key, name in extracted.items():
        print(f"  {key}: {name}")
        config["tables"][key] = name
    
    # Write updated config
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\n✅ Updated {config_file}")

if __name__ == "__main__":
    update_config()

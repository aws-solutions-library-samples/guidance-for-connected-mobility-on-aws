#!/usr/bin/env python3
import json
import os
from pathlib import Path

class TableConfig:
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = Path(__file__).parent / "table-config.json"
        
        with open(config_path) as f:
            self.config = json.load(f)
    
    def get_table_name(self, table_key, environment="prod"):
        """Get table name for a specific environment"""
        base_name = self.config["tables"][table_key]
        if environment != "prod":
            suffix = self.config["environments"][environment]["suffix"]
            return f"{base_name}-{suffix}"
        return base_name
    
    def get_all_tables(self, environment="prod"):
        """Get all table names for environment"""
        return {
            key: self.get_table_name(key, environment) 
            for key in self.config["tables"]
        }
    
    def get_env_vars(self, environment="prod"):
        """Get environment variables dict for Lambda/CDK"""
        tables = self.get_all_tables(environment)
        return {
            "VEHICLES_TABLE_NAME": tables["vehicles"],
            "FLEETS_TABLE_NAME": tables["fleets"],
            "TRIPS_TABLE_NAME": tables["trips"],
            "TELEMETRY_TABLE_NAME": tables["telemetry"],
            "SAFETY_EVENTS_TABLE_NAME": tables["safety_events"],
            "MAINTENANCE_ALERTS_TABLE_NAME": tables["maintenance_alerts"],
            "VEHICLE_CERTIFICATES_TABLE_NAME": tables["vehicle_certificates"],
            "USER_PREFERENCES_TABLE_NAME": tables["user_preferences"],
            "CACHE_TABLE_NAME": tables["dashboard_metrics_cache"]
        }
    
    def get_flink_properties(self, environment="prod"):
        """Get Flink application properties"""
        env_vars = self.get_env_vars(environment)
        return {
            "TELEMETRY_TABLE_NAME": env_vars["TELEMETRY_TABLE_NAME"],
            "TRIPS_TABLE_NAME": env_vars["TRIPS_TABLE_NAME"],
            "MAINTENANCE_ALERTS_TABLE_NAME": env_vars["MAINTENANCE_ALERTS_TABLE_NAME"],
            "SAFETY_EVENTS_TABLE_NAME": env_vars["SAFETY_EVENTS_TABLE_NAME"]
        }

# Convenience functions
def get_table_config():
    return TableConfig()

def get_table_name(table_key, environment="prod"):
    return get_table_config().get_table_name(table_key, environment)

if __name__ == "__main__":
    config = TableConfig()
    print("Current table configuration:")
    for env in ["dev", "prod"]:
        print(f"\n{env.upper()} Environment:")
        for key, name in config.get_all_tables(env).items():
            print(f"  {key}: {name}")

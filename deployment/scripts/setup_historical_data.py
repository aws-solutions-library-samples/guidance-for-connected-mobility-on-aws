#!/usr/bin/env python3
"""
Interactive Historical Data Setup for Phase 2: Fleet Management Interface
"""

import os
import sys
import subprocess
import argparse

def detect_deployment_profile():
    """Detect which AWS profile has the deployed CMS resources"""
    import subprocess
    
    # Get list of available profiles
    try:
        result = subprocess.run(['aws', 'configure', 'list-profiles'], 
                              capture_output=True, text=True, check=True)
        profiles = result.stdout.strip().split('\n')
    except:
        profiles = ['default']
    
    # Check each profile for storage stack
    stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    for profile in profiles:
        try:
            result = subprocess.run([
                'aws', 'cloudformation', 'describe-stacks', 
                '--stack-name', f'cms-{stage}-storage',
                '--profile', profile,
                '--region', region
            ], capture_output=True, text=True, check=True)
            
            if result.returncode == 0:
                print(f"✅ Found CMS deployment in profile: {profile}")
                return profile
        except:
            continue
    
    # Fallback to default
    print("⚠️  Could not detect deployment profile, using default")
    return 'default'

def get_user_input(prompt, default_value, input_type=str):
    """Get user input with default value"""
    if input_type == int:
        prompt_text = f"{prompt} (default: {default_value}): "
    else:
        prompt_text = f"{prompt} (default: {default_value}): "
    
    user_input = input(prompt_text).strip()
    
    if not user_input:
        return default_value
    
    if input_type == int:
        try:
            return int(user_input)
        except ValueError:
            print(f"Invalid input. Using default: {default_value}")
            return default_value
    
    return user_input
    """Get user input with default value"""
    if input_type == int:
        prompt_text = f"{prompt} (default: {default_value}): "
    else:
        prompt_text = f"{prompt} (default: {default_value}): "
    
    user_input = input(prompt_text).strip()
    
    if not user_input:
        return default_value
    
    if input_type == int:
        try:
            return int(user_input)
        except ValueError:
            print(f"Invalid input. Using default: {default_value}")
            return default_value
    
    return user_input

def calculate_time_estimate(days, num_fleets, vehicles_per_fleet, use_location_services):
    """Calculate estimated execution time"""
    total_vehicles = num_fleets * vehicles_per_fleet
    
    # Base time estimates (in seconds)
    base_setup_time = 10  # AWS setup, table detection
    fleet_time = num_fleets * 0.5  # 0.5 sec per fleet
    vehicle_time = total_vehicles * 0.3  # 0.3 sec per vehicle
    
    # Trip generation (most time-consuming)
    trips_per_vehicle_per_day = 2  # average trips per vehicle per day
    total_trips = total_vehicles * days * trips_per_vehicle_per_day
    
    if use_location_services:
        trip_time = total_trips * 0.8  # 0.8 sec per trip with Location Services
        location_setup_time = 15  # Location Services setup
    else:
        trip_time = total_trips * 0.1  # 0.1 sec per trip without Location Services
        location_setup_time = 0
    
    # Safety and maintenance events
    safety_time = total_trips * 0.05  # 0.05 sec per trip for safety events
    maintenance_time = total_vehicles * 0.2  # 0.2 sec per vehicle for maintenance
    
    # DynamoDB write time
    total_items = num_fleets + total_vehicles + total_trips + (total_trips * 0.1) + (total_vehicles * 2)
    db_write_time = total_items * 0.02  # 0.02 sec per item
    
    total_seconds = (base_setup_time + location_setup_time + fleet_time + 
                    vehicle_time + trip_time + safety_time + maintenance_time + db_write_time)
    
    return int(total_seconds)

def format_time_estimate(seconds):
    """Format time estimate in human-readable format"""
    if seconds < 60:
        return f"{seconds} seconds"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds > 0:
            return f"{minutes}m {remaining_seconds}s"
        return f"{minutes} minutes"
    else:
        hours = seconds // 3600
        remaining_minutes = (seconds % 3600) // 60
        if remaining_minutes > 0:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours} hours"

def main():
    print("🚀 Phase 2: Fleet Management Interface - Historical Data Setup")
    print("=" * 60)
    print()
    
    # Get environment variables with defaults
    # Dynamically detect which profile has the CMS deployment
    detected_profile = detect_deployment_profile()
    default_profile = os.environ.get('AWS_PROFILE', detected_profile)
    default_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    default_region = os.environ.get('AWS_REGION', 'us-east-1')
    batch_mode = os.environ.get('BATCH_MODE', 'false').lower() == 'true' or not sys.stdin.isatty()
    
    if batch_mode:
        print("🤖 Running in batch mode (non-interactive)")
    
    print("📋 Configuration Parameters:")
    print()
    
    # Auto-detect current AWS profile (no prompting)
    aws_profile = default_profile
    print(f"✅ Using AWS Profile: {aws_profile}")
    
    # Auto-detect deployment stage (no prompting)
    deployment_stage = default_stage
    print(f"✅ Using Deployment Stage: {deployment_stage}")
    
    if batch_mode:
        region = default_region
        days = 30
        num_fleets = 5
        vehicles_per_fleet = 10
        use_location_services = True
        selected_cities = list(range(1, 6))
        safety_event_probability = 0.05
        maintenance_frequency = 30
    else:
        region = get_user_input("AWS Region", default_region)

        print()
        print("📊 Data Generation Parameters:")
        print()

        days = get_user_input("Days of historical data", 30, int)
        num_fleets = get_user_input("Number of fleets", 5, int)
        vehicles_per_fleet = get_user_input("Vehicles per fleet", 10, int)

        print()
        print("🌍 Location Services Configuration:")
        print()

        use_location_services = get_user_input("Use Amazon Location Services for realistic routes? (y/n)", "y")
        use_location_services = use_location_services.lower().startswith('y')

        cities = [
            "new_york (NYC area)",
            "los_angeles (LA area)", 
            "chicago (Chicago area)",
            "houston (Houston area)",
            "phoenix (Phoenix area)"
        ]

        print("Available cities for route generation:")
        for i, city in enumerate(cities, 1):
            print(f"  {i}. {city}")

        city_selection = get_user_input("Select cities (comma-separated numbers, or 'all')", "all")

        if city_selection.lower() == 'all':
            selected_cities = list(range(1, len(cities) + 1))
        else:
            try:
                selected_cities = [int(x.strip()) for x in city_selection.split(',')]
                selected_cities = [x for x in selected_cities if 1 <= x <= len(cities)]
            except ValueError:
                print("Invalid city selection. Using all cities.")
                selected_cities = list(range(1, len(cities) + 1))

        print()
        print("⚙️  Advanced Options:")
        print()

        safety_event_probability = get_user_input("Safety event probability (0.0-1.0)", "0.05")
        try:
            safety_event_probability = float(safety_event_probability)
            if not 0.0 <= safety_event_probability <= 1.0:
                safety_event_probability = 0.05
        except ValueError:
            safety_event_probability = 0.05

        maintenance_frequency = get_user_input("Maintenance alert frequency (days)", "30", int)

        # Confirmation
        confirm = get_user_input("Proceed with data generation? (y/n)", "y")
        if not confirm.lower().startswith('y'):
            print("❌ Operation cancelled.")
            return
    
    print()
    print("🚀 Starting historical data generation...")
    print()
    
    # Build command
    script_path = "../../services/simulation/enhanced_historical_data_injector.py"
    
    cmd = [
        "python3", script_path,
        "--profile", aws_profile,
        "--days", str(days),
        "--region", region
    ]
    
    # Set environment variables for the script
    env = os.environ.copy()
    env.update({
        'AWS_PROFILE': aws_profile,
        'DEPLOYMENT_STAGE': deployment_stage,
        'AWS_REGION': region,
        'NUM_FLEETS': str(num_fleets),
        'VEHICLES_PER_FLEET': str(vehicles_per_fleet),
        'USE_LOCATION_SERVICES': str(use_location_services).lower(),
        'SELECTED_CITIES': ','.join(map(str, selected_cities)),
        'SAFETY_EVENT_PROBABILITY': str(safety_event_probability),
        'MAINTENANCE_FREQUENCY': str(maintenance_frequency)
    })
    
    try:
        print(f"📡 Executing: {' '.join(cmd)}")
        print()
        
        # Run the enhanced historical data injector
        result = subprocess.run(cmd, env=env, check=True, text=True)
        
        print()
        print("✅ Historical data generation completed successfully!")
        print()
        print("📊 Next Steps:")
        print("1. Verify data in DynamoDB tables")
        print("2. Check Amazon Location Services resources")
        print("3. Test UI with generated data")
        print("4. Proceed to Phase 3 (Telemetry Pipeline)")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running historical data injector: {e}")
        print("💡 Troubleshooting:")
        print("- Check AWS credentials and permissions")
        print("- Verify DynamoDB tables exist (run Phase 1 first)")
        print("- Check Amazon Location Services permissions")
        sys.exit(1)
    except FileNotFoundError:
        print(f"❌ Script not found: {script_path}")
        print("💡 Make sure you're running from the deployment directory")
        sys.exit(1)

if __name__ == "__main__":
    main()

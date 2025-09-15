#!/usr/bin/env python3
"""
Test script for enhanced historical data injector with trip correlation
"""

from enhanced_historical_data_injector import EnhancedHistoricalDataInjector

def test_enhanced_injector():
    """Test the enhanced historical data injector"""
    
    print("🧪 Testing Enhanced Historical Data Injector...")
    
    injector = EnhancedHistoricalDataInjector()
    
    # Generate sample data
    print("📊 Generating sample fleet data...")
    fleets = injector.generate_fleet_data(num_fleets=2)
    
    print("🚗 Generating sample vehicle data...")
    vehicles = injector.generate_vehicle_data(fleets, vehicles_per_fleet=2)
    
    print("🗺️ Generating sample trip data with Location Services...")
    trips = injector.generate_enhanced_trip_data(vehicles, days=1)
    
    print(f"\n✅ Generated test data:")
    print(f"   • {len(fleets)} fleets")
    print(f"   • {len(vehicles)} vehicles") 
    print(f"   • {len(trips)} trips")
    
    # Show sample trip with correlations
    if trips:
        sample_trip = trips[0]
        print(f"\n📋 Sample Trip Structure:")
        print(f"   Trip ID: {sample_trip['tripId']}")
        print(f"   Vehicle ID: {sample_trip['vehicleId']}")
        print(f"   Start Time: {sample_trip['startTime']}")
        print(f"   End Time: {sample_trip['endTime']}")
        print(f"   Duration: {sample_trip['duration']} minutes")
        print(f"   Distance: {sample_trip['distance']:.2f} km")
        
        if 'safetyAlerts' in sample_trip:
            print(f"   Safety Alerts: {len(sample_trip['safetyAlerts'])}")
            for alert in sample_trip['safetyAlerts'][:2]:  # Show first 2
                print(f"      - {alert['alertType']} ({alert['severity']}) at {alert['timestamp']}")
        
        if 'maintenanceAlerts' in sample_trip:
            print(f"   Maintenance Alerts: {len(sample_trip['maintenanceAlerts'])}")
            for alert in sample_trip['maintenanceAlerts'][:2]:  # Show first 2
                print(f"      - {alert['alertType']} (DTC: {alert['dtc']}) - {alert['severity']}")
        
        if 'telemetryCount' in sample_trip:
            print(f"   Telemetry Records: {sample_trip['telemetryCount']}")
    
    print("\n🎉 Enhanced historical data injector test completed!")

if __name__ == "__main__":
    test_enhanced_injector()

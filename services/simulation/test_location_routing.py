#!/usr/bin/env python3
"""
Test Amazon Location Services routing integration
"""

from realtime_telemetry_simulator import RealtimeTelemetrySimulator

def test_location_routing():
    """Test Location Services route generation"""
    
    simulator = RealtimeTelemetrySimulator()
    
    # Test locations (NYC area)
    start_lat = 40.7128
    start_lon = -74.0060
    
    print("🗺️ Testing Amazon Location Services routing...")
    print(f"📍 Start: ({start_lat}, {start_lon})")
    
    # Generate route using Location Services
    route_points = simulator.generate_route_points(start_lat, start_lon, 10)
    
    print(f"🛣️ Generated {len(route_points)} route points:")
    
    for i, point in enumerate(route_points):
        print(f"   {i+1}. ({point['lat']:.6f}, {point['lng']:.6f})")
    
    # Calculate total distance
    total_distance = 0
    for i in range(1, len(route_points)):
        prev = route_points[i-1]
        curr = route_points[i]
        
        # Simple distance calculation (approximate)
        lat_diff = curr['lat'] - prev['lat']
        lon_diff = curr['lng'] - prev['lng']
        distance = ((lat_diff ** 2) + (lon_diff ** 2)) ** 0.5 * 111000  # meters
        total_distance += distance
    
    print(f"📏 Approximate route distance: {total_distance:.0f} meters")
    
    if len(route_points) > 0:
        print("✅ Location Services routing working!")
    else:
        print("❌ No route points generated")

if __name__ == "__main__":
    test_location_routing()

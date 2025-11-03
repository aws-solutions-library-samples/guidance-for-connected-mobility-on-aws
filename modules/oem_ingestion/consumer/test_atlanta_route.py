#!/usr/bin/env python3
"""Test Atlanta route generation and output for UI visualization"""
import json
from datetime import datetime, timedelta
from atlanta_route_generator import AtlantaRouteGenerator

def generate_sample_trip():
    """Generate a complete trip with GPS coordinates"""
    route_gen = AtlantaRouteGenerator("downtown_to_airport")
    
    trip_data = {
        "vehicleId": "VEH-ATLANTA-TEST-001",
        "tripId": f"TRIP-{int(datetime.utcnow().timestamp())}",
        "startTime": datetime.utcnow().isoformat() + "Z",
        "route": "Downtown Atlanta to Airport",
        "telemetry": []
    }
    
    # Generate telemetry points every 30 seconds
    current_time = datetime.utcnow()
    speeds = [0, 15, 25, 35, 45, 55, 45, 35, 25, 15, 0]  # mph
    
    for i, speed in enumerate(speeds):
        position = route_gen.get_next_position(
            speed_mph=speed,
            timestamp=(current_time + timedelta(seconds=i*30)).isoformat() + "Z"
        )
        
        telemetry_point = {
            "timestamp": position["timestamp"],
            "latitude": position["latitude"],
            "longitude": position["longitude"],
            "speed": speed,
            "heading": position["heading"],
            "odometer": 12345 + (i * 0.25),  # Increment odometer
            "location": position["location_name"]
        }
        
        trip_data["telemetry"].append(telemetry_point)
    
    trip_data["endTime"] = trip_data["telemetry"][-1]["timestamp"]
    trip_data["distance_miles"] = len(speeds) * 0.25
    trip_data["duration_minutes"] = len(speeds) * 0.5
    
    return trip_data

def generate_geojson_route():
    """Generate GeoJSON for map visualization"""
    route_gen = AtlantaRouteGenerator("downtown_to_airport")
    
    coordinates = []
    for _ in range(len(route_gen.route)):
        pos = route_gen.get_next_position()
        coordinates.append([pos["longitude"], pos["latitude"]])
    
    geojson = {
        "type": "Feature",
        "properties": {
            "name": "Downtown Atlanta to Airport",
            "distance_miles": 10,
            "city": "Atlanta, GA"
        },
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates
        }
    }
    
    return geojson

if __name__ == "__main__":
    print("=" * 60)
    print("Atlanta Route Test")
    print("=" * 60)
    
    # Generate sample trip
    print("\n1. Sample Trip Data:")
    trip = generate_sample_trip()
    print(json.dumps(trip, indent=2))
    
    # Generate GeoJSON
    print("\n2. GeoJSON Route (for map visualization):")
    geojson = generate_geojson_route()
    print(json.dumps(geojson, indent=2))
    
    # Save to files
    with open('atlanta_sample_trip.json', 'w') as f:
        json.dump(trip, f, indent=2)
    
    with open('atlanta_route.geojson', 'w') as f:
        json.dump(geojson, f, indent=2)
    
    print("\n✅ Files created:")
    print("   - atlanta_sample_trip.json")
    print("   - atlanta_route.geojson")
    print("\nYou can visualize the GeoJSON at: https://geojson.io")

#!/usr/bin/env python3
"""
Trip Aggregator - Groups Ford telemetry into trips
"""
import json
import sys
from datetime import datetime
from collections import defaultdict

def aggregate_trips(log_file):
    """Aggregate telemetry messages into trips"""
    
    # Load all messages
    messages = []
    with open(log_file, 'r') as f:
        for line in f:
            messages.append(json.loads(line))
    
    # Group by vehicle and sort by timestamp
    vehicles = defaultdict(list)
    for msg in messages:
        vin = msg['vin']
        vehicles[vin].append(msg)
    
    print(f"📊 Processing {len(vehicles)} vehicles, {len(messages)} messages\n")
    
    all_trips = []
    
    for vin, msgs in vehicles.items():
        # Sort by timestamp
        msgs = sorted(msgs, key=lambda x: x['message']['timestamp'])
        
        # Extract telemetry by type
        telemetry = defaultdict(list)
        for msg in msgs:
            signal = msg['message']['typedData'].get('signal', {}).get('wksSignal')
            timestamp = msg['message']['timestamp']
            
            if signal == 'IGNITION_STATUS':
                status = msg['message']['typedData'].get('enumValue', {}).get('ignitionStatus')
                telemetry['ignition'].append({'time': timestamp, 'status': status})
            elif signal == 'SPEED':
                speed = msg['message']['typedData'].get('speedValue', {}).get('speed', 0)
                telemetry['speed'].append({'time': timestamp, 'speed': speed})
            elif signal == 'ODOMETER':
                odo = msg['message']['typedData'].get('doubleValue', 0)
                telemetry['odometer'].append({'time': timestamp, 'odometer': odo})
        
        # Find trip boundaries (ignition ON to OFF)
        trips = []
        trip_start = None
        
        for event in telemetry['ignition']:
            if event['status'] == 'ON' and trip_start is None:
                trip_start = event['time']
            elif event['status'] in ['OFF', 'ACCESSORY'] and trip_start:
                trips.append({
                    'start_time': trip_start,
                    'end_time': event['time'],
                    'vin': vin
                })
                trip_start = None
        
        # Aggregate telemetry for each trip
        for trip in trips:
            start = trip['start_time']
            end = trip['end_time']
            
            # Get speed data during trip
            trip_speeds = [s for s in telemetry['speed'] if start <= s['time'] <= end]
            trip_odo = [o for o in telemetry['odometer'] if start <= o['time'] <= end]
            
            if trip_speeds and trip_odo:
                trip['avg_speed'] = sum(s['speed'] for s in trip_speeds) / len(trip_speeds)
                trip['max_speed'] = max(s['speed'] for s in trip_speeds)
                trip['distance_km'] = trip_odo[-1]['odometer'] - trip_odo[0]['odometer']
                trip['duration_sec'] = (datetime.fromisoformat(end.replace('Z', '+00:00')) - 
                                       datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds()
                
                all_trips.append(trip)
    
    # Print summary
    print(f"🚗 Found {len(all_trips)} trips\n")
    
    for i, trip in enumerate(all_trips[:10], 1):
        print(f"Trip {i}:")
        print(f"  VIN: {trip['vin']}")
        print(f"  Start: {trip['start_time']}")
        print(f"  End: {trip['end_time']}")
        print(f"  Duration: {trip['duration_sec']/60:.1f} minutes")
        print(f"  Distance: {trip['distance_km']:.1f} km")
        print(f"  Avg Speed: {trip['avg_speed']:.1f} km/h")
        print(f"  Max Speed: {trip['max_speed']:.1f} km/h")
        print()
    
    # Save trips to JSON
    output_file = log_file.replace('.jsonl', '_trips.json')
    with open(output_file, 'w') as f:
        json.dump(all_trips, f, indent=2)
    
    print(f"✓ Saved {len(all_trips)} trips to {output_file}")
    
    return all_trips

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 aggregate_trips.py <log_file.jsonl>")
        sys.exit(1)
    
    aggregate_trips(sys.argv[1])

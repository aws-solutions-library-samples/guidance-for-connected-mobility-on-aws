#!/usr/bin/env python3
"""
GPS companion for FWE CAN simulation.
Publishes GPS coordinates via IoT Core alongside CAN telemetry.

Usage:
    python3 gps_publisher.py --vehicle-id 5YJ3E1EA1PF721240 --profile givenand-CMS
"""
import argparse, json, time, random, math
import boto3

def generate_route(start_lat, start_lng, num_points):
    points = []
    lat, lng = start_lat, start_lng
    bearing = random.uniform(0, 360)
    for i in range(num_points):
        bearing += random.uniform(-15, 15)
        rad = math.radians(bearing)
        lat += 0.0004 * math.cos(rad)
        lng += 0.0004 * math.sin(rad)
        points.append((lat, lng, bearing % 360))
    return points

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vehicle-id', required=True)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--start-lat', type=float, default=47.6062)
    parser.add_argument('--start-lng', type=float, default=-122.3321)
    parser.add_argument('--points', type=int, default=10)
    parser.add_argument('--interval', type=float, default=5.0)
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    iot = session.client('iot-data', region_name='us-east-1')
    topic = f"$aws/rules/cms_dev_iot_msk_rule/{args.vehicle_id}"

    route = generate_route(args.start_lat, args.start_lng, args.points)
    print(f"🗺️  Publishing {len(route)} GPS points for {args.vehicle_id}")

    for i, (lat, lng, heading) in enumerate(route):
        payload = json.dumps({
            'vehicleId': args.vehicle_id,
            'timestamp': int(time.time() * 1000),
            'lat': round(lat, 6),
            'lng': round(lng, 6),
            'heading': round(heading, 1),
            'speed': round(random.uniform(25, 65), 1) if 0 < i < len(route) - 1 else 0,
            'ignitionOn': True,
            'dataSource': 'gps',
        })
        iot.publish(topic=topic, payload=payload, qos=0)
        print(f"  {i+1}/{len(route)}: ({lat:.6f}, {lng:.6f})")
        time.sleep(args.interval)

    print("✅ GPS complete")

if __name__ == '__main__':
    main()

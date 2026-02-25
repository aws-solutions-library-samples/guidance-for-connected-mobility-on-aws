#!/usr/bin/env python3
"""
GPS companion for FWE CAN simulation.
Uses Amazon Location Services for road-snapped routes.

Usage:
    python3 gps_publisher.py --vehicle-id 5YJ3E1EA1PF721240 --profile givenand-CMS
"""
import argparse, json, time, random, math
import boto3

def generate_route(session, start_lat, start_lng, num_points):
    """Generate road-following route via Amazon Location Services."""
    location = session.client('location', region_name='us-east-1')
    dest_lat = start_lat + random.uniform(-0.02, 0.02)
    dest_lng = start_lng + random.uniform(-0.02, 0.02)

    try:
        resp = location.calculate_route(
            CalculatorName='cms-route-calculator',
            DeparturePosition=[start_lng, start_lat],
            DestinationPosition=[dest_lng, dest_lat],
            TravelMode='Car',
            IncludeLegGeometry=True
        )
        coords = resp['Legs'][0]['Geometry']['LineString']
        step = max(1, len(coords) // num_points)
        points = [(coords[i][1], coords[i][0]) for i in range(0, len(coords), step)][:num_points]
        print(f"🗺️  Route: {len(coords)} road points, sampled {len(points)}")
        return points
    except Exception as e:
        print(f"⚠️ Location Services failed: {e}, using fallback")
        points = []
        lat, lng = start_lat, start_lng
        bearing = random.uniform(0, 360)
        for _ in range(num_points):
            bearing += random.uniform(-15, 15)
            lat += 0.0004 * math.cos(math.radians(bearing))
            lng += 0.0004 * math.sin(math.radians(bearing))
            points.append((lat, lng))
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

    route = generate_route(session, args.start_lat, args.start_lng, args.points)
    print(f"📡 Publishing {len(route)} GPS points for {args.vehicle_id}")

    for i, (lat, lng) in enumerate(route):
        prev = route[max(0, i-1)]
        heading = math.degrees(math.atan2(lng - prev[1], lat - prev[0])) % 360
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

#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Seed trip history for all vehicles from purchase date to now.
Cumulative trip mileage matches current odometer - purchase odometer.
"""

import boto3
import os
import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

REGION = os.environ.get("AWS_REGION", "us-east-1")
VEHICLES_TABLE = "cms-prod-storage-vehicles"
TRIPS_TABLE = "cms-prod-storage-trips"

ddb = boto3.resource("dynamodb", region_name=REGION)

# City coordinates for route generation
CITY_COORDS = {
    "Dallas": (32.78, -96.80), "Denver": (39.74, -104.99), "Phoenix": (33.45, -112.07),
    "Portland": (45.52, -122.68), "Chicago": (41.88, -87.63), "Atlanta": (33.75, -84.39),
    "Seattle": (47.61, -122.33), "Miami": (25.76, -80.19), "Nashville": (36.16, -86.78),
    "Las Vegas": (36.17, -115.14), "Boston": (42.36, -71.06), "Houston": (29.76, -95.37),
}

DRIVERS = [
    "Mike Johnson", "Sarah Chen", "Carlos Rodriguez", "Emily Davis", "James Wilson",
    "Maria Garcia", "David Kim", "Lisa Thompson", "Robert Brown", "Jennifer Martinez",
    "Thomas Anderson", "Amanda White", "Kevin Lee", "Rachel Green", "Daniel Harris",
]


def generate_route(base_lat, base_lng, distance_miles):
    """Generate a simple route with 4-8 waypoints."""
    points = []
    num_points = random.randint(4, 8)
    spread = distance_miles * 0.01  # Rough degree spread for distance
    for i in range(num_points):
        lat = base_lat + random.uniform(-spread, spread)
        lng = base_lng + random.uniform(-spread, spread)
        points.append({"lat": str(round(lat, 6)), "lng": str(round(lng, 6))})
    return points


def main():
    print("=" * 60)
    print("Fleet Trip History Seeder")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)

    vehicles_table = ddb.Table(VEHICLES_TABLE)
    trips_table = ddb.Table(TRIPS_TABLE)

    resp = vehicles_table.scan()
    vehicles = resp["Items"]
    print(f"\nFound {len(vehicles)} vehicles\n")

    now = datetime.now()
    total_trips = 0

    for v in vehicles:
        vid = v["vehicleId"]
        make = v.get("make", "Unknown")
        model = v.get("model", "Unknown")

        # Get lifecycle data
        purchase_date_str = v.get("purchaseDate", "")
        if not purchase_date_str:
            continue

        purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d")
        purchase_odometer = float(v.get("purchaseOdometer", 0))
        current_mileage = float(v.get("currentMileage", 0) or v.get("mileage", 0) or v.get("odometer", 0))
        total_miles_driven = current_mileage - purchase_odometer

        if total_miles_driven <= 0:
            continue

        # Get base location
        reg_city = v.get("registrationCity", "Dallas")
        base_coords = CITY_COORDS.get(reg_city, (32.78, -96.80))

        # Calculate trip parameters
        days_owned = (now - purchase_date).days
        if days_owned <= 0:
            continue

        # Generate trips: ~5 trips per week (weekdays)
        trips_per_week = random.uniform(4, 6)
        total_trip_count = int(trips_per_week * (days_owned / 7))
        total_trip_count = min(total_trip_count, 800)  # Cap at 800 trips

        # Distribute mileage across trips
        avg_trip_miles = total_miles_driven / total_trip_count
        driver = random.choice(DRIVERS)

        vehicle_trips = 0
        cumulative_miles = purchase_odometer

        for i in range(total_trip_count):
            # Random date between purchase and now
            trip_days_ago = random.randint(0, days_owned)
            trip_date = purchase_date + timedelta(days=trip_days_ago)

            # Skip weekends sometimes
            if trip_date.weekday() >= 5 and random.random() > 0.2:
                continue

            # Trip distance with variation
            trip_miles = max(1, avg_trip_miles * random.uniform(0.3, 2.5))
            trip_miles = round(trip_miles, 2)
            cumulative_miles += trip_miles

            # Trip timing
            start_hour = random.choice([6, 7, 8, 9, 10, 11, 13, 14, 15, 16])
            trip_start = trip_date.replace(hour=start_hour, minute=random.randint(0, 59))
            duration_minutes = int(trip_miles * random.uniform(1.5, 3.0))  # 1.5-3 min per mile
            trip_end = trip_start + timedelta(minutes=duration_minutes)

            start_ts = int(trip_start.timestamp() * 1000)
            end_ts = int(trip_end.timestamp() * 1000)
            trip_id = f"{vid}-{start_ts}-{uuid.uuid4().hex[:6]}"

            avg_speed = round(trip_miles / (duration_minutes / 60), 1) if duration_minutes > 0 else 30
            max_speed = round(avg_speed * random.uniform(1.2, 1.6), 1)

            route = generate_route(base_coords[0], base_coords[1], trip_miles)

            item = {
                "vehicleId": vid,
                "tripId": trip_id,
                "timestamp": start_ts,
                "startTime": start_ts,
                "endTime": end_ts,
                "completedAt": end_ts,
                "status": "COMPLETED",
                "distance": Decimal(str(trip_miles)),
                "totalDistance": Decimal(str(trip_miles)),
                "durationMs": (end_ts - start_ts),
                "averageSpeed": Decimal(str(avg_speed)),
                "avgSpeed": Decimal(str(avg_speed)),
                "maxSpeed": Decimal(str(max_speed)),
                "driverScore": random.randint(70, 100),
                "driverName": driver,
                "assignedDriver": driver,
                "lat": Decimal(str(round(base_coords[0] + random.uniform(-0.05, 0.05), 6))),
                "lng": Decimal(str(round(base_coords[1] + random.uniform(-0.05, 0.05), 6))),
                "route": route,
                "telemetryCount": len(route),
                "createdBy": "HistoricalSeeder",
                "currentFuelLevel": random.randint(20, 95),
                "currentSpeed": Decimal("0"),
                "currentEngineTemp": Decimal(str(random.randint(170, 210))),
                "odometerAtStart": Decimal(str(round(cumulative_miles - trip_miles, 1))),
                "odometerAtEnd": Decimal(str(round(cumulative_miles, 1))),
            }

            trips_table.put_item(Item=item)
            vehicle_trips += 1

        total_trips += vehicle_trips
        print(f"  {vid:10s} {make:12s} {model:12s} | {purchase_date_str} → now | {int(total_miles_driven):>7,} mi | {vehicle_trips:>4} trips | {reg_city}")

    print(f"\n✅ Created {total_trips} trips across {len(vehicles)} vehicles")


if __name__ == "__main__":
    main()

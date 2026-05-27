#!/usr/bin/env python3
"""Seed realistic driver records for fleet operations."""
import boto3, os, random, uuid
from datetime import datetime, timedelta
from decimal import Decimal

REGION = os.environ.get("AWS_REGION", "us-west-2")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
TABLE = f"cms-{STAGE}-storage-drivers"
NUM_DRIVERS = int(os.environ.get("NUM_DRIVERS", "75"))

ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(TABLE)

FIRST_NAMES = ["James","Maria","Robert","Linda","Michael","Sarah","David","Jennifer","Carlos","Emily",
               "William","Jessica","Daniel","Ashley","Jose","Amanda","Kevin","Stephanie","Brian","Nicole",
               "Marcus","Rachel","Anthony","Michelle","Thomas","Laura","Christopher","Angela","Jason","Megan",
               "Ryan","Brittany","Eric","Samantha","Tyler","Rebecca","Brandon","Katherine","Aaron","Heather",
               "Derek","Christina","Travis","Amber","Cody","Tiffany","Shane","Crystal","Dustin","Vanessa"]
LAST_NAMES = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
              "Anderson","Taylor","Thomas","Moore","Jackson","Martin","Lee","Thompson","White","Harris",
              "Clark","Lewis","Robinson","Walker","Young","Allen","King","Wright","Scott","Torres",
              "Hill","Green","Adams","Baker","Nelson","Carter","Mitchell","Perez","Roberts","Turner"]
LICENSE_CLASSES = [("CDL-A", 0.3), ("CDL-B", 0.25), ("Class C", 0.35), ("Class D", 0.1)]
CERTIFICATIONS = ["HAZMAT","Tanker","Doubles/Triples","Passenger","School Bus","Air Brake"]

now = datetime.utcnow()
drivers = []

# Pool of vehicle IDs for one-time 1:1 assignment below. The seed
# enforces the "one driver per vehicle at a time" invariant (VSA_DATA_SEED.md
# §driver-vehicle-invariant) by drawing without replacement. Before
# 2026-05-04 each driver picked a vehicle via `random.randint(1,50)`
# independently, which gave ~21/35 vehicles multiple drivers on a 75-driver
# seed — bad enough that we ran a one-time cleanup script (see
# /tmp/driver_unassign_2026-05-04.json in the ops runbook). We size the
# pool to VEHICLE_POOL_SIZE; any drivers beyond that remain unassigned,
# which matches real fleets where some drivers are bench/spare.
VEHICLE_POOL_SIZE = int(os.environ.get("VEHICLE_POOL_SIZE", "50"))
_unassigned_share = float(os.environ.get("DRIVER_UNASSIGNED_SHARE", "0.2"))
_vehicle_pool = [f"VEH-{i:04d}" for i in range(1, VEHICLE_POOL_SIZE + 1)]
random.shuffle(_vehicle_pool)
# Some drivers are intentionally unassigned (bench/spare). The share is
# tunable; default 20% matches the pre-2026-05-04 behavior.
_num_unassigned = int(NUM_DRIVERS * _unassigned_share)
# First NUM_DRIVERS - _num_unassigned drivers get vehicles; the rest get None.
_assignable = NUM_DRIVERS - _num_unassigned

for i in range(NUM_DRIVERS):
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    hire_date = now - timedelta(days=random.randint(180, 2500))
    license_class = random.choices([c[0] for c in LICENSE_CLASSES], [c[1] for c in LICENSE_CLASSES])[0]
    certs = random.sample(CERTIFICATIONS, k=random.randint(0, 3)) if "CDL" in license_class else []
    years_exp = random.randint(1, 25)
    base_score = random.gauss(82, 10)
    
    driver = {
        "driverId": f"DRV-{i+1:04d}",
        "firstName": first,
        "lastName": last,
        "email": f"{first.lower()}.{last.lower()}@example.com",
        "phone": f"555-{random.randint(1000,9999)}",
        "status": random.choices(["active","active","active","on_leave","terminated"], [50,30,10,7,3])[0],
        "hireDate": hire_date.strftime("%Y-%m-%d"),
        "yearsExperience": years_exp,
        "licenseNumber": f"DL-{license_class.replace(' ','')}-{random.randint(100000,999999)}",
        "licenseClass": license_class,
        "licenseState": random.choice(["TX","CA","FL","NY","IL","OH","GA","NC","MI","AZ","NV","WA","OR","CO"]),
        "licenseExpiry": (now + timedelta(days=random.randint(30, 1200))).strftime("%Y-%m-%d"),
        "certifications": certs,
        "safetyScore": Decimal(str(round(max(40, min(100, base_score)), 1))),
        "totalTrips": random.randint(50, 5000),
        "totalMiles": random.randint(5000, 500000),
        "incidentCount": random.randint(0, 8),
        "lastTripDate": (now - timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d"),
        "homeBase": random.choice(["Dallas","Houston","Phoenix","Atlanta","Chicago","Miami","Denver","Seattle","Portland","Las Vegas"]),
        # Draw the next vehicle from the shuffled pool so we never
        # double-assign. When the pool runs out, or when this driver is
        # past the assignable cutoff, leave it None (bench/spare driver).
        "assignedVehicleId": (
            _vehicle_pool.pop() if i < _assignable and _vehicle_pool else None
        ),
        "createdAt": hire_date.isoformat(),
        "updatedAt": now.isoformat(),
    }
    # Remove None values
    driver = {k: v for k, v in driver.items() if v is not None}
    drivers.append(driver)

print(f"Seeding {len(drivers)} drivers to {TABLE} in {REGION}...")
with table.batch_writer() as batch:
    for d in drivers:
        batch.put_item(Item=d)
print(f"✅ {len(drivers)} drivers seeded")

# Stats
active = sum(1 for d in drivers if d["status"] == "active")
cdl = sum(1 for d in drivers if "CDL" in d.get("licenseClass",""))
print(f"   Active: {active}, CDL holders: {cdl}, Avg safety score: {sum(float(d['safetyScore']) for d in drivers)/len(drivers):.1f}")

#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Enrich fleet vehicles with realistic lifecycle data:
purchase info, acquisition cost, depreciation, insurance, lease terms, odometer history.
"""

import boto3
import os
import random
from datetime import datetime, timedelta
from decimal import Decimal

REGION = os.environ.get("AWS_REGION", "us-east-1")
DEPLOYMENT_STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
VEHICLES_TABLE = f"cms-{DEPLOYMENT_STAGE}-storage-vehicles"

ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(VEHICLES_TABLE)

# Realistic pricing by make/model
VEHICLE_PRICING = {
    ("Ford", "F-150"): {"msrp": (35000, 55000), "type": "Pickup", "mpg": (18, 24), "class": "Class 3"},
    ("Ford", "Transit"): {"msrp": (38000, 52000), "type": "Van", "mpg": (14, 18), "class": "Class 3"},
    ("Ford", "Escape"): {"msrp": (28000, 38000), "type": "SUV", "mpg": (26, 33), "class": "Class 1"},
    ("Ford", "Explorer"): {"msrp": (35000, 55000), "type": "SUV", "mpg": (20, 27), "class": "Class 2"},
    ("Ram", "1500"): {"msrp": (36000, 58000), "type": "Pickup", "mpg": (17, 23), "class": "Class 3"},
    ("Ram", "2500"): {"msrp": (40000, 65000), "type": "Pickup", "mpg": (12, 18), "class": "Class 4"},
    ("Ram", "ProMaster"): {"msrp": (35000, 48000), "type": "Van", "mpg": (14, 21), "class": "Class 3"},
    ("Chevrolet", "Express"): {"msrp": (34000, 45000), "type": "Van", "mpg": (11, 16), "class": "Class 3"},
    ("Chevrolet", "Equinox"): {"msrp": (27000, 35000), "type": "SUV", "mpg": (26, 31), "class": "Class 1"},
    ("Chevrolet", "Silverado"): {"msrp": (35000, 58000), "type": "Pickup", "mpg": (16, 23), "class": "Class 3"},
    ("Chevrolet", "Tahoe"): {"msrp": (52000, 72000), "type": "SUV", "mpg": (15, 20), "class": "Class 2"},
    ("Dodge", "Charger"): {"msrp": (32000, 45000), "type": "Sedan", "mpg": (19, 30), "class": "Class 1"},
    ("Dodge", "Durango"): {"msrp": (38000, 55000), "type": "SUV", "mpg": (18, 24), "class": "Class 2"},
    ("Toyota", "Corolla"): {"msrp": (22000, 28000), "type": "Sedan", "mpg": (30, 40), "class": "Class 1"},
    ("Toyota", "RAV4"): {"msrp": (28000, 38000), "type": "SUV", "mpg": (27, 35), "class": "Class 1"},
    ("Toyota", "Highlander"): {"msrp": (36000, 50000), "type": "SUV", "mpg": (21, 29), "class": "Class 2"},
    ("Honda", "Accord"): {"msrp": (27000, 38000), "type": "Sedan", "mpg": (26, 33), "class": "Class 1"},
    ("Honda", "Civic"): {"msrp": (23000, 30000), "type": "Sedan", "mpg": (30, 40), "class": "Class 1"},
    ("Mercedes", "Sprinter"): {"msrp": (42000, 65000), "type": "Van", "mpg": (14, 20), "class": "Class 3"},
    ("Mercedes", "Metris"): {"msrp": (34000, 42000), "type": "Van", "mpg": (19, 24), "class": "Class 2"},
}

ACQUISITION_TYPES = ["Purchase", "Lease", "Fleet Program"]
INSURANCE_PROVIDERS = ["Progressive Commercial", "Nationwide Fleet", "Liberty Mutual", "Zurich Fleet", "Great West Casualty"]
LEASE_PROVIDERS = ["Element Fleet", "ARI Fleet", "Donlen", "Enterprise Fleet", "Penske Leasing"]

# US cities for registration
CITIES = [
    ("Dallas", "TX"), ("Denver", "CO"), ("Phoenix", "AZ"), ("Portland", "OR"),
    ("Chicago", "IL"), ("Atlanta", "GA"), ("Seattle", "WA"), ("Miami", "FL"),
    ("Nashville", "TN"), ("Las Vegas", "NV"), ("Boston", "MA"), ("Houston", "TX"),
]


def main():
    print("=" * 60)
    print("Fleet Vehicle Lifecycle Enrichment")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)

    resp = table.scan()
    vehicles = resp["Items"]
    print(f"\nFound {len(vehicles)} vehicles\n")

    now = datetime.now()
    updated = 0

    for v in vehicles:
        vid = v["vehicleId"]
        make = v.get("make", "Unknown")
        model = v.get("model", "Unknown")
        year = int(v.get("year", 2022))

        pricing = VEHICLE_PRICING.get((make, model), {"msrp": (30000, 50000), "type": "Unknown", "mpg": (15, 25), "class": "Class 2"})

        # Purchase/acquisition data
        msrp = random.randint(pricing["msrp"][0], pricing["msrp"][1])
        acquisition_type = random.choice(ACQUISITION_TYPES)
        purchase_date = datetime(year, random.randint(1, 12), random.randint(1, 28))
        purchase_odometer = random.randint(0, 500) if acquisition_type != "Fleet Program" else random.randint(0, 15000)
        acquisition_cost = msrp if acquisition_type == "Purchase" else int(msrp * 0.85) if acquisition_type == "Fleet Program" else 0

        # Current age and depreciation
        age_years = (now - purchase_date).days / 365.25
        depreciation_rate = 0.15 if age_years < 1 else 0.12 if age_years < 3 else 0.10
        current_value = int(msrp * (1 - depreciation_rate) ** age_years)
        total_depreciation = msrp - current_value

        # Mileage — use existing or generate realistic
        current_mileage = int(v.get("mileage", 0) or v.get("odometer", 0))
        if current_mileage < 1000:
            annual_miles = random.randint(12000, 35000)
            current_mileage = int(purchase_odometer + (annual_miles * age_years))

        # Insurance
        insurance_provider = random.choice(INSURANCE_PROVIDERS)
        annual_premium = random.randint(1200, 4500)
        monthly_premium = round(annual_premium / 12, 2)

        # Lease terms (if leased)
        lease_provider = random.choice(LEASE_PROVIDERS) if acquisition_type == "Lease" else None
        monthly_lease = random.randint(450, 1200) if acquisition_type == "Lease" else 0
        lease_term_months = random.choice([24, 36, 48, 60]) if acquisition_type == "Lease" else 0
        lease_end_date = (purchase_date + timedelta(days=lease_term_months * 30)).strftime("%Y-%m-%d") if lease_term_months else ""

        # Registration
        city, state = random.choice(CITIES)
        registration_expiry = (now + timedelta(days=random.randint(30, 365))).strftime("%Y-%m-%d")

        # Warranty
        warranty_miles = 50000 if make in ("Ford", "Chevrolet", "Ram", "Dodge") else 60000 if make in ("Toyota", "Honda") else 50000
        warranty_months = 36 if make in ("Ford", "Chevrolet", "Ram", "Dodge") else 36
        warranty_end_date = (purchase_date + timedelta(days=warranty_months * 30)).strftime("%Y-%m-%d")
        warranty_miles_remaining = max(0, warranty_miles - current_mileage)
        warranty_days_remaining = max(0, (datetime.strptime(warranty_end_date, "%Y-%m-%d") - now).days)
        warranty_active = warranty_miles_remaining > 0 and warranty_days_remaining > 0

        # Fuel efficiency
        mpg = round(random.uniform(pricing["mpg"][0], pricing["mpg"][1]), 1)

        # Update vehicle record
        update_expr = """SET 
            purchaseDate = :purchaseDate,
            purchaseOdometer = :purchaseOdometer,
            acquisitionType = :acquisitionType,
            acquisitionCost = :acquisitionCost,
            msrp = :msrp,
            currentValue = :currentValue,
            totalDepreciation = :totalDepreciation,
            currentMileage = :currentMileage,
            vehicleClass = :vehicleClass,
            avgMpg = :avgMpg,
            insuranceProvider = :insuranceProvider,
            annualPremium = :annualPremium,
            monthlyPremium = :monthlyPremium,
            leaseProvider = :leaseProvider,
            monthlyLease = :monthlyLease,
            leaseTermMonths = :leaseTermMonths,
            leaseEndDate = :leaseEndDate,
            registrationCity = :registrationCity,
            registrationState = :registrationState,
            registrationExpiry = :registrationExpiry,
            warrantyEndDate = :warrantyEndDate,
            warrantyMilesLimit = :warrantyMilesLimit,
            warrantyMilesRemaining = :warrantyMilesRemaining,
            warrantyDaysRemaining = :warrantyDaysRemaining,
            warrantyActive = :warrantyActive,
            annualMiles = :annualMiles,
            costPerMile = :costPerMile,
            totalCostYTD = :totalCostYTD
        """

        # Calculate cost per mile and YTD costs
        annual_miles = max(1, int(current_mileage / max(1, age_years)))
        fuel_cost_per_mile = round(3.50 / mpg, 3)  # ~$3.50/gal average
        maintenance_cost_per_mile = round(random.uniform(0.05, 0.15), 3)
        total_cost_per_mile = round(fuel_cost_per_mile + maintenance_cost_per_mile + (monthly_premium / (annual_miles / 12)) + (total_depreciation / max(1, current_mileage)), 3)

        ytd_days = (now - datetime(now.year, 1, 1)).days
        ytd_miles = int(annual_miles * (ytd_days / 365))
        total_cost_ytd = int(ytd_miles * total_cost_per_mile)

        table.update_item(
            Key={"vehicleId": vid},
            UpdateExpression=update_expr,
            ExpressionAttributeValues={
                ":purchaseDate": purchase_date.strftime("%Y-%m-%d"),
                ":purchaseOdometer": Decimal(str(purchase_odometer)),
                ":acquisitionType": acquisition_type,
                ":acquisitionCost": Decimal(str(acquisition_cost)),
                ":msrp": Decimal(str(msrp)),
                ":currentValue": Decimal(str(current_value)),
                ":totalDepreciation": Decimal(str(total_depreciation)),
                ":currentMileage": Decimal(str(current_mileage)),
                ":vehicleClass": pricing["class"],
                ":avgMpg": Decimal(str(mpg)),
                ":insuranceProvider": insurance_provider,
                ":annualPremium": Decimal(str(annual_premium)),
                ":monthlyPremium": Decimal(str(monthly_premium)),
                ":leaseProvider": lease_provider or "",
                ":monthlyLease": Decimal(str(monthly_lease)),
                ":leaseTermMonths": lease_term_months,
                ":leaseEndDate": lease_end_date,
                ":registrationCity": city,
                ":registrationState": state,
                ":registrationExpiry": registration_expiry,
                ":warrantyEndDate": warranty_end_date,
                ":warrantyMilesLimit": Decimal(str(warranty_miles)),
                ":warrantyMilesRemaining": Decimal(str(warranty_miles_remaining)),
                ":warrantyDaysRemaining": warranty_days_remaining,
                ":warrantyActive": warranty_active,
                ":annualMiles": Decimal(str(annual_miles)),
                ":costPerMile": Decimal(str(total_cost_per_mile)),
                ":totalCostYTD": Decimal(str(total_cost_ytd)),
            }
        )

        updated += 1
        warranty_status = "✅ Active" if warranty_active else "❌ Expired"
        print(f"  {vid:10s} {year} {make:12s} {model:12s} | {acquisition_type:14s} ${msrp:>6,} | {current_mileage:>7,} mi | ${total_cost_per_mile:.2f}/mi | Warranty: {warranty_status}")

    print(f"\n✅ Updated {updated} vehicles with lifecycle data")

    # Sync cert table VINs to match vehicle table VINs
    cert_table = ddb.Table(f"cms-{DEPLOYMENT_STAGE}-storage-vehicle-certificates")
    synced = 0
    for v in vehicles:
        vid = v["vehicleId"]
        real_vin = v.get("vin", "")
        if not real_vin:
            continue
        try:
            cert = cert_table.get_item(Key={"vehicleId": vid}).get("Item", {})
            if cert and cert.get("vin") != real_vin:
                cert_table.update_item(
                    Key={"vehicleId": vid},
                    UpdateExpression="SET vin = :v",
                    ExpressionAttributeValues={":v": real_vin}
                )
                synced += 1
        except Exception:
            pass
    if synced:
        print(f"🔄 Synced {synced} vehicle-certificates VINs")


if __name__ == "__main__":
    main()

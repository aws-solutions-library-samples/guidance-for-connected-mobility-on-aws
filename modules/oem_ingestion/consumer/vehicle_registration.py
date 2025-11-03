"""
Vehicle Auto-Registration for OEM Ingestion
Automatically registers vehicles in DynamoDB when first seen from OEM feed
"""
import boto3
import os
from datetime import datetime
from typing import Optional

class VehicleRegistration:
    def __init__(self, table_name: str = None, region: str = 'us-east-1'):
        self.dynamodb = boto3.client('dynamodb', region_name=region)
        self.table_name = table_name or os.getenv('VEHICLES_TABLE', 'cms-dev-storage-vehicles')
        self.registered_cache = set()  # Cache to avoid repeated DynamoDB calls
    
    def register_vehicle_if_new(self, vehicle_id: str, oem_source: str, asset_info: dict = None) -> bool:
        """
        Register vehicle in DynamoDB if it doesn't exist
        Returns True if vehicle was newly registered, False if already exists
        """
        # Check cache first
        if vehicle_id in self.registered_cache:
            return False
        
        try:
            # Check if vehicle exists
            response = self.dynamodb.get_item(
                TableName=self.table_name,
                Key={'vehicleId': {'S': vehicle_id}}
            )
            
            if 'Item' in response:
                # Vehicle exists, add to cache
                self.registered_cache.add(vehicle_id)
                return False
            
            # Vehicle doesn't exist, create it
            self._create_vehicle(vehicle_id, oem_source, asset_info)
            self.registered_cache.add(vehicle_id)
            return True
            
        except Exception as e:
            print(f"⚠ Error checking/registering vehicle {vehicle_id}: {e}")
            return False
    
    def _create_vehicle(self, vehicle_id: str, oem_source: str, asset_info: dict = None):
        """Create new vehicle record in DynamoDB with all available Ford attributes"""
        timestamp = int(datetime.utcnow().timestamp() * 1000)
        
        # Extract all available info from Ford assetInfo
        vin = None
        vehicle_type = None
        region_code = None
        esn = None
        iccid = None
        serial = None
        protocol_version = None
        fuel_type = None
        country_code = None
        
        if asset_info and 'vehicleAssetInfo' in asset_info:
            vehicle_asset = asset_info['vehicleAssetInfo']
            vin = vehicle_asset.get('vin')
            vehicle_type = vehicle_asset.get('type', 'Unknown')
            region_code = vehicle_asset.get('regionCode', 'Unknown')
            esn = vehicle_asset.get('lastKnownEsn')
            iccid = vehicle_asset.get('lastKnownIccid')
            serial = vehicle_asset.get('serial')
            protocol_version = vehicle_asset.get('protocolVersion')
            fuel_type = vehicle_asset.get('fuelType')
            country_code = vehicle_asset.get('countryCode')
        
        # Parse vehicle type to extract make/model if possible
        # Ford type format: "ecg,tcu-fnv2" or similar
        make = oem_source.upper()
        model = 'Unknown'
        if vehicle_type:
            # Try to extract meaningful model info from type
            type_parts = vehicle_type.split(',')
            if len(type_parts) > 1:
                model = type_parts[1].upper()  # e.g., "TCU-FNV2"
            else:
                model = vehicle_type.upper()
        
        # Build vehicle item
        item = {
            'vehicleId': {'S': vehicle_id},
            'status': {'S': 'active'},
            'source': {'S': 'oem'},
            'oem': {'S': oem_source},
            'createdAt': {'N': str(timestamp)},
            'updatedAt': {'N': str(timestamp)},
            'registrationMethod': {'S': 'auto-oem-ingestion'},
            'enrichmentStatus': {'S': 'pending'},  # Needs manual enrichment
            'dataCompleteness': {'S': 'partial'}   # Only has OEM-provided data
        }
        
        # Add Ford-provided fields
        if vin:
            item['vin'] = {'S': vin}
        if vehicle_type:
            item['vehicleType'] = {'S': vehicle_type}
            item['oemVehicleType'] = {'S': vehicle_type}  # Store original
        if region_code:
            item['region'] = {'S': region_code}
        if esn:
            item['esn'] = {'S': esn}
        if iccid:
            item['iccid'] = {'S': iccid}
        if serial:
            item['serialNumber'] = {'S': serial}
        if protocol_version:
            item['protocolVersion'] = {'S': protocol_version}
        if fuel_type:
            item['fuelType'] = {'S': fuel_type}
        if country_code:
            item['countryCode'] = {'S': country_code}
        
        # Add required fields with defaults
        item['make'] = {'S': make}
        item['model'] = {'S': model}
        item['year'] = {'N': '2024'}  # Default - needs enrichment
        
        # Add notes about what needs enrichment
        missing_fields = []
        if not vin:
            missing_fields.append('vin')
        if model == 'Unknown':
            missing_fields.append('model')
        missing_fields.extend(['year', 'trim', 'color', 'licensePlate'])
        
        if missing_fields:
            item['enrichmentNeeded'] = {'SS': missing_fields}
        
        try:
            self.dynamodb.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression='attribute_not_exists(vehicleId)'  # Only create if doesn't exist
            )
            print(f"✓ Auto-registered vehicle: {vehicle_id} (VIN: {vin or 'N/A'})")
        except self.dynamodb.exceptions.ConditionalCheckFailedException:
            # Vehicle was created by another process, that's fine
            print(f"✓ Vehicle {vehicle_id} already exists (race condition)")
        except Exception as e:
            print(f"✗ Failed to register vehicle {vehicle_id}: {e}")
            raise

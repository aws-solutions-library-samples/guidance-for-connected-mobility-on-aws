"""
Connected Mobility Platform Connector

Integration layer for connecting the predictive maintenance agent
with the existing Connected Mobility Platform infrastructure.
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class CMSConnector:
    """
    Connector for integrating with Connected Mobility Platform
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # AWS clients
        self.dynamodb = boto3.resource('dynamodb', region_name=config.get('aws_region', 'us-east-1'))
        self.s3 = boto3.client('s3', region_name=config.get('aws_region', 'us-east-1'))
        self.eventbridge = boto3.client('events', region_name=config.get('aws_region', 'us-east-1'))
        self.elasticache = boto3.client('elasticache', region_name=config.get('aws_region', 'us-east-1'))
        
        # CMS platform configuration
        self.environment = config.get('environment', 'dev')
        self.table_prefix = f"cms-{self.environment}-storage"
        
        # Table references
        self.vehicles_table = self.dynamodb.Table(f"{self.table_prefix}-vehicles")
        self.trips_table = self.dynamodb.Table(f"{self.table_prefix}-trips")
        self.telemetry_table = self.dynamodb.Table(f"{self.table_prefix}-telemetry")
        
        # S3 configuration
        self.datalake_bucket = config.get('datalake_bucket', f'cms-{self.environment}-datalake')
        
        # EventBridge configuration
        self.event_bus_name = config.get('event_bus_name', 'default')
        self.event_source = 'predictive-maintenance-agent'
        
        # Redis configuration
        self.redis_endpoint = config.get('redis_endpoint')
        
        logger.info(f"CMS Connector initialized for environment: {self.environment}")
    
    async def get_vehicle_data(self, vehicle_id: str) -> Dict[str, Any]:
        """
        Get comprehensive vehicle data from CMS platform
        """
        
        try:
            # Get vehicle record from DynamoDB
            response = self.vehicles_table.get_item(
                Key={'vehicle_id': vehicle_id}
            )
            
            if 'Item' not in response:
                logger.warning(f"Vehicle {vehicle_id} not found in vehicles table")
                return {}
            
            vehicle_data = response['Item']
            
            # Get latest vehicle state from Redis if available
            if self.redis_endpoint:
                redis_data = await self._get_redis_vehicle_state(vehicle_id)
                if redis_data:
                    vehicle_data.update(redis_data)
            
            # Enrich with calculated fields
            vehicle_data['last_analysis_time'] = datetime.utcnow().isoformat()
            
            return vehicle_data
            
        except ClientError as e:
            logger.error(f"Error getting vehicle data for {vehicle_id}: {str(e)}")
            return {}
    
    async def get_recent_telemetry(self, vehicle_id: str, hours: int = 24) -> Dict[str, Any]:
        """
        Get recent telemetry data for a vehicle
        """
        
        try:
            # Calculate time range
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=hours)
            
            # Query telemetry table
            response = self.telemetry_table.query(
                KeyConditionExpression='vehicle_id = :vid AND #ts BETWEEN :start AND :end',
                ExpressionAttributeNames={'#ts': 'timestamp'},
                ExpressionAttributeValues={
                    ':vid': vehicle_id,
                    ':start': start_time.isoformat(),
                    ':end': end_time.isoformat()
                },
                ScanIndexForward=False,  # Most recent first
                Limit=100  # Limit to recent readings
            )
            
            telemetry_records = response.get('Items', [])
            
            if not telemetry_records:
                logger.warning(f"No recent telemetry found for {vehicle_id}")
                return {}
            
            # Process and structure telemetry data
            structured_telemetry = self._structure_telemetry_data(telemetry_records)
            
            return structured_telemetry
            
        except ClientError as e:
            logger.error(f"Error getting telemetry for {vehicle_id}: {str(e)}")
            return {}
    
    async def get_service_history(self, vehicle_id: str) -> List[Dict[str, Any]]:
        """
        Get service history for a vehicle
        """
        
        try:
            # Query service history from trips table (maintenance trips)
            response = self.trips_table.query(
                IndexName='vehicle-type-index',  # Assuming this index exists
                KeyConditionExpression='vehicle_id = :vid AND trip_type = :type',
                ExpressionAttributeValues={
                    ':vid': vehicle_id,
                    ':type': 'maintenance'
                },
                ScanIndexForward=False  # Most recent first
            )
            
            service_records = response.get('Items', [])
            
            # Also check for maintenance events in S3 data lake
            s3_service_history = await self._get_s3_service_history(vehicle_id)
            
            # Combine and deduplicate
            all_service_records = service_records + s3_service_history
            
            # Sort by date
            all_service_records.sort(
                key=lambda x: x.get('service_date', ''), 
                reverse=True
            )
            
            return all_service_records[:20]  # Return last 20 service records
            
        except ClientError as e:
            logger.error(f"Error getting service history for {vehicle_id}: {str(e)}")
            return []
    
    async def get_active_vehicles(self) -> List[str]:
        """
        Get list of active vehicles in the fleet
        """
        
        try:
            # Scan vehicles table for active vehicles
            response = self.vehicles_table.scan(
                FilterExpression='attribute_exists(vehicle_id) AND #status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'active'},
                ProjectionExpression='vehicle_id'
            )
            
            vehicle_ids = [item['vehicle_id'] for item in response.get('Items', [])]
            
            logger.info(f"Found {len(vehicle_ids)} active vehicles")
            return vehicle_ids
            
        except ClientError as e:
            logger.error(f"Error getting active vehicles: {str(e)}")
            return []
    
    async def publish_maintenance_decision(self, decision) -> bool:
        """
        Publish maintenance decision to EventBridge for CMS platform consumption
        """
        
        try:
            # Create EventBridge event
            event_detail = {
                'vehicle_id': decision.vehicle_id,
                'component_type': decision.component_type.value,
                'urgency': decision.urgency.value,
                'predicted_failure_date': decision.predicted_failure_date.isoformat() if decision.predicted_failure_date else None,
                'confidence_score': decision.confidence_score,
                'recommended_action': decision.recommended_action,
                'cost_estimate': decision.cost_estimate,
                'parts_needed': decision.parts_needed,
                'service_time_hours': decision.service_time_hours,
                'reasoning': decision.reasoning,
                'created_at': decision.created_at.isoformat(),
                'agent_version': '1.0'
            }
            
            # Send event to EventBridge
            response = self.eventbridge.put_events(
                Entries=[
                    {
                        'Source': self.event_source,
                        'DetailType': 'Maintenance Decision',
                        'Detail': json.dumps(event_detail),
                        'EventBusName': self.event_bus_name
                    }
                ]
            )
            
            if response['FailedEntryCount'] == 0:
                logger.info(f"Published maintenance decision for {decision.vehicle_id}")
                return True
            else:
                logger.error(f"Failed to publish maintenance decision: {response}")
                return False
                
        except ClientError as e:
            logger.error(f"Error publishing maintenance decision: {str(e)}")
            return False
    
    async def update_vehicle_maintenance_status(self, vehicle_id: str, status: Dict[str, Any]) -> bool:
        """
        Update vehicle maintenance status in CMS platform
        """
        
        try:
            # Update vehicle record with maintenance status
            self.vehicles_table.update_item(
                Key={'vehicle_id': vehicle_id},
                UpdateExpression='SET maintenance_status = :status, last_maintenance_check = :timestamp',
                ExpressionAttributeValues={
                    ':status': status,
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
            
            # Also update Redis cache if available
            if self.redis_endpoint:
                await self._update_redis_maintenance_status(vehicle_id, status)
            
            return True
            
        except ClientError as e:
            logger.error(f"Error updating maintenance status for {vehicle_id}: {str(e)}")
            return False
    
    def _structure_telemetry_data(self, telemetry_records: List[Dict]) -> Dict[str, Any]:
        """
        Structure raw telemetry data for model consumption
        """
        
        if not telemetry_records:
            return {}
        
        # Get most recent record as base
        latest_record = telemetry_records[0]
        
        structured_data = {
            'vehicle_id': latest_record.get('vehicle_id'),
            'timestamp': latest_record.get('timestamp'),
            'latitude': latest_record.get('latitude'),
            'longitude': latest_record.get('longitude')
        }
        
        # Extract tire data
        tire_data = {}
        tire_positions = ['FL', 'FR', 'RL', 'RR']
        
        for position in tire_positions:
            position_lower = position.lower()
            
            # Check for tire pressure data
            pressure_key = f'tpms_pressure_{position_lower}_mbar'
            temp_key = f'tpms_temperature_{position_lower}_celsius'
            tread_key = f'tread_depth_{position_lower}_mm'
            condition_key = f'tpms_condition_{position_lower}'
            
            if pressure_key in latest_record:
                tire_data[position] = {
                    'position': position,
                    'pressure_mbar': latest_record.get(pressure_key),
                    'temperature_celsius': latest_record.get(temp_key, 20),
                    'tread_depth_mm': latest_record.get(tread_key),
                    'condition': latest_record.get(condition_key, 'NORMAL'),
                    'timestamp': latest_record.get('timestamp')
                }
        
        if tire_data:
            structured_data['tire'] = {'tires': list(tire_data.values())}
        
        # Extract brake data
        brake_fields = ['brake_fluid_level', 'brake_pad_wear_front', 'brake_pad_wear_rear']
        brake_data = {}
        
        for field in brake_fields:
            if field in latest_record:
                brake_data[field] = latest_record[field]
        
        if brake_data:
            structured_data['brake'] = brake_data
        
        # Extract engine data
        engine_fields = ['engine_oil_life', 'engine_coolant_temp', 'engine_rpm', 'engine_load']
        engine_data = {}
        
        for field in engine_fields:
            if field in latest_record:
                engine_data[field] = latest_record[field]
        
        if engine_data:
            structured_data['engine'] = engine_data
        
        # Extract battery data (for EVs)
        battery_fields = ['battery_soc', 'battery_voltage', 'battery_temperature', 'battery_health']
        battery_data = {}
        
        for field in battery_fields:
            if field in latest_record:
                battery_data[field] = latest_record[field]
        
        if battery_data:
            structured_data['battery'] = battery_data
        
        return structured_data
    
    async def _get_redis_vehicle_state(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        """
        Get vehicle state from Redis cache
        """
        
        # In production, this would use a Redis client
        # For now, return None to indicate Redis data not available
        return None
    
    async def _update_redis_maintenance_status(self, vehicle_id: str, status: Dict[str, Any]):
        """
        Update maintenance status in Redis cache
        """
        
        # In production, this would update Redis
        pass
    
    async def _get_s3_service_history(self, vehicle_id: str) -> List[Dict[str, Any]]:
        """
        Get service history from S3 data lake
        """
        
        try:
            # Query S3 for service history files
            # This would typically use Athena or direct S3 queries
            # For now, return empty list
            return []
            
        except Exception as e:
            logger.error(f"Error getting S3 service history: {str(e)}")
            return []
    
    async def get_fleet_telemetry_summary(self) -> Dict[str, Any]:
        """
        Get summary of fleet telemetry for monitoring
        """
        
        try:
            # Get count of vehicles with recent telemetry
            current_time = datetime.utcnow()
            one_hour_ago = current_time - timedelta(hours=1)
            
            # This would typically be a more efficient query in production
            active_vehicles = await self.get_active_vehicles()
            
            vehicles_with_recent_data = 0
            total_telemetry_points = 0
            
            # Sample a subset for performance
            sample_vehicles = active_vehicles[:50] if len(active_vehicles) > 50 else active_vehicles
            
            for vehicle_id in sample_vehicles:
                recent_telemetry = await self.get_recent_telemetry(vehicle_id, hours=1)
                if recent_telemetry:
                    vehicles_with_recent_data += 1
                    total_telemetry_points += 1  # Simplified count
            
            return {
                'total_active_vehicles': len(active_vehicles),
                'vehicles_with_recent_data': vehicles_with_recent_data,
                'telemetry_points_last_hour': total_telemetry_points,
                'data_freshness_percentage': (vehicles_with_recent_data / len(sample_vehicles) * 100) if sample_vehicles else 0,
                'last_updated': current_time.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting fleet telemetry summary: {str(e)}")
            return {}
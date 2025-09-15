import json
import boto3
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional
import os

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')

# Table references - using actual table names
vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE', 'cms-631ca2-591631-vehicles'))
safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE', 'cms-631ca2-591631-safety-events'))
maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE', 'cms-631ca2-591631-maintenance-alerts'))
aggregated_metrics_table = dynamodb.Table(os.environ.get('AGGREGATED_METRICS_TABLE', 'cms-631ca2-591631-dashboard-metrics-cache'))

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def lambda_handler(event, context):
    """
    Lambda handler that aggregates dashboard metrics exactly like DashboardMetrics.tsx
    Supports the same filters: time range and fleet selection
    """
    try:
        # Parse query parameters (for API Gateway requests)
        query_params = event.get('queryStringParameters') or {}
        
        # Extract filters - matching DashboardMetrics.tsx
        time_range = query_params.get('timeRange', '24h')  # 1h, 6h, 24h, 3d, 7d, 30d
        fleet_id = query_params.get('fleetId', 'all')      # specific fleet ID or 'all'
        
        logger.info(f"Aggregating metrics for timeRange={time_range}, fleetId={fleet_id}")
        
        # Check cache first
        cached_metrics = get_cached_metrics(time_range, fleet_id)
        if cached_metrics and not is_cache_stale(cached_metrics):
            logger.info("Returning cached metrics")
            return create_api_response(cached_metrics)
        
        # Fetch fresh data
        metrics = aggregate_dashboard_metrics(time_range, fleet_id)
        
        # Cache the results
        cache_metrics(metrics, time_range, fleet_id)
        
        return create_api_response(metrics)
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Content-Type': 'application/json'
            },
            'body': json.dumps({'error': str(e)})
        }

def aggregate_dashboard_metrics(time_range: str, fleet_id: str) -> Dict[str, Any]:
    """
    Aggregate metrics exactly matching DashboardMetrics.tsx structure
    """
    logger.info(f"Fetching fresh data for timeRange={time_range}, fleetId={fleet_id}")
    
    # Calculate time boundaries
    end_time = datetime.utcnow()
    start_time = get_start_time(time_range, end_time)
    
    # Fetch data from all sources (matching the frontend API calls)
    vehicles_data = fetch_vehicles_data(fleet_id)
    safety_events_data = fetch_safety_events_data(start_time, end_time, fleet_id)
    maintenance_alerts_data = fetch_maintenance_alerts_data(start_time, end_time, fleet_id)
    
    # Calculate metrics exactly like the frontend
    metrics = calculate_dashboard_metrics(
        vehicles_data, 
        safety_events_data, 
        maintenance_alerts_data,
        time_range,
        fleet_id
    )
    
    return {
        'timeRange': time_range,
        'fleetId': fleet_id,
        'timestamp': end_time.isoformat(),
        'metrics': metrics,
        'rawData': {
            'vehicles': vehicles_data,
            'safetyEvents': safety_events_data,
            'maintenanceAlerts': maintenance_alerts_data
        },
        'lastUpdated': end_time.isoformat()
    }

def get_start_time(time_range: str, end_time: datetime) -> datetime:
    """Convert time range string to start datetime - matching frontend logic"""
    time_deltas = {
        '1h': timedelta(hours=1),
        '6h': timedelta(hours=6),
        '24h': timedelta(hours=24),
        '3d': timedelta(days=3),
        '7d': timedelta(days=7),
        '30d': timedelta(days=30)
    }
    
    delta = time_deltas.get(time_range, timedelta(hours=24))
    return end_time - delta

def fetch_vehicles_data(fleet_id: str) -> List[Dict[str, Any]]:
    """
    Fetch vehicles data - equivalent to /realtime/vehicles?limit=100
    """
    try:
        if fleet_id == 'all':
            # Scan all vehicles (matching frontend behavior)
            response = vehicles_table.scan(Limit=100)
        else:
            # Filter by fleet ID
            response = vehicles_table.scan(
                FilterExpression='#fid = :fleet_id OR fleet_info.fleet_id = :fleet_id',
                ExpressionAttributeNames={'#fid': 'fid'},
                ExpressionAttributeValues={':fleet_id': fleet_id},
                Limit=100
            )
        
        vehicles = response.get('Items', [])
        logger.info(f"Fetched {len(vehicles)} vehicles for fleet {fleet_id}")
        return vehicles
        
    except Exception as e:
        logger.error(f"Error fetching vehicles: {str(e)}")
        return []

def fetch_safety_events_data(start_time: datetime, end_time: datetime, fleet_id: str) -> List[Dict[str, Any]]:
    """
    Fetch safety events data - equivalent to /safety-events?limit=50
    """
    try:
        # Build filter expression for time range
        filter_expression = '#ts BETWEEN :start_time AND :end_time'
        expression_values = {
            ':start_time': start_time.isoformat(),
            ':end_time': end_time.isoformat()
        }
        expression_names = {'#ts': 'timestamp'}
        
        # Add fleet filter if not 'all'
        if fleet_id != 'all':
            filter_expression += ' AND (#fid = :fleet_id OR fleet_info.fleet_id = :fleet_id)'
            expression_values[':fleet_id'] = fleet_id
            expression_names['#fid'] = 'fid'
        
        response = safety_events_table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            Limit=50
        )
        
        events = response.get('Items', [])
        logger.info(f"Fetched {len(events)} safety events for fleet {fleet_id} in time range {start_time} to {end_time}")
        return events
        
    except Exception as e:
        logger.error(f"Error fetching safety events: {str(e)}")
        return []

def fetch_maintenance_alerts_data(start_time: datetime, end_time: datetime, fleet_id: str) -> List[Dict[str, Any]]:
    """
    Fetch maintenance alerts data - equivalent to /maintenance-alerts?limit=50
    """
    try:
        # Build filter expression for time range and open alerts
        filter_expression = '#ts BETWEEN :start_time AND :end_time'
        expression_values = {
            ':start_time': start_time.isoformat(),
            ':end_time': end_time.isoformat()
        }
        expression_names = {'#ts': 'timestamp'}
        
        # Add fleet filter if not 'all'
        if fleet_id != 'all':
            filter_expression += ' AND (#fid = :fleet_id OR fleet_info.fleet_id = :fleet_id)'
            expression_values[':fleet_id'] = fleet_id
            expression_names['#fid'] = 'fid'
        
        response = maintenance_alerts_table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            Limit=50
        )
        
        alerts = response.get('Items', [])
        logger.info(f"Fetched {len(alerts)} maintenance alerts for fleet {fleet_id} in time range {start_time} to {end_time}")
        return alerts
        
    except Exception as e:
        logger.error(f"Error fetching maintenance alerts: {str(e)}")
        return []

def calculate_dashboard_metrics(vehicles: List[Dict], safety_events: List[Dict], 
                              maintenance_alerts: List[Dict], time_range: str, fleet_id: str) -> Dict[str, Any]:
    """
    Calculate metrics exactly matching DashboardMetrics.tsx calculateMetrics() function
    """
    
    # Vehicle metrics - matching frontend logic
    total_vehicles = len(vehicles)
    active_vehicles = len([v for v in vehicles if v.get('connectivity') == 'CONNECTED'])
    
    # Driver score calculation - matching frontend logic
    vehicles_with_scores = [v for v in vehicles if v.get('drv_score') or v.get('current_metrics', {}).get('driver_score')]
    avg_driver_score = 0
    if vehicles_with_scores:
        total_score = sum(
            v.get('drv_score') or v.get('current_metrics', {}).get('driver_score', 0) 
            for v in vehicles_with_scores
        )
        avg_driver_score = round(total_score / len(vehicles_with_scores))
    
    # Safety events metrics - matching frontend logic
    critical_safety_events = len([e for e in safety_events if e.get('severity') in ['critical', 'high']])
    
    # Maintenance alerts metrics - matching frontend logic
    critical_maintenance_alerts = len([a for a in maintenance_alerts if a.get('severity') == 'critical' or a.get('priority') == 'high'])
    
    # Key metrics cards - exactly matching frontend structure
    key_metrics = [
        {
            'id': 'total-vehicles',
            'title': 'Total Vehicles',
            'value': total_vehicles,
            'subtitle': f'{active_vehicles} active',
            'status': 'success' if active_vehicles > 0 else 'warning',
            'badge': {
                'text': f'{round((active_vehicles / max(total_vehicles, 1)) * 100)}% online',
                'color': 'green' if active_vehicles > total_vehicles * 0.8 else 'blue'
            }
        },
        {
            'id': 'driver-score',
            'title': 'Avg Driver Score',
            'value': avg_driver_score if avg_driver_score > 0 else 'N/A',
            'subtitle': f'{len(vehicles_with_scores)} drivers' if vehicles_with_scores else 'No data',
            'status': 'success' if avg_driver_score >= 80 else 'warning' if avg_driver_score >= 70 else 'error',
            'badge': {
                'text': 'Excellent' if avg_driver_score >= 80 else 'Good' if avg_driver_score >= 70 else 'Needs Improvement',
                'color': 'green' if avg_driver_score >= 80 else 'blue' if avg_driver_score >= 70 else 'red'
            }
        },
        {
            'id': 'safety-events',
            'title': 'Safety Events',
            'value': len(safety_events),
            'subtitle': f'{critical_safety_events} critical',
            'status': 'error' if critical_safety_events > 0 else 'warning' if len(safety_events) > 0 else 'success',
            'badge': {
                'text': 'Action Required' if critical_safety_events > 0 else 'Monitor' if len(safety_events) > 0 else 'All Clear',
                'color': 'red' if critical_safety_events > 0 else 'blue' if len(safety_events) > 0 else 'green'
            }
        },
        {
            'id': 'maintenance-alerts',
            'title': 'Maintenance Alerts',
            'value': len(maintenance_alerts),
            'subtitle': f'{critical_maintenance_alerts} critical',
            'status': 'error' if critical_maintenance_alerts > 0 else 'warning' if len(maintenance_alerts) > 0 else 'success',
            'badge': {
                'text': 'Urgent' if critical_maintenance_alerts > 0 else 'Scheduled' if len(maintenance_alerts) > 0 else 'Up to Date',
                'color': 'red' if critical_maintenance_alerts > 0 else 'blue' if len(maintenance_alerts) > 0 else 'green'
            }
        }
    ]
    
    # Safety events summary - matching frontend getSafetyEventsSummary()
    safety_summary = get_safety_events_summary(safety_events)
    
    # Maintenance alerts summary - matching frontend getMaintenanceAlertsSummary()
    maintenance_summary = get_maintenance_alerts_summary(maintenance_alerts)
    
    return {
        'keyMetrics': key_metrics,
        'safetyEventsSummary': safety_summary,
        'maintenanceAlertsSummary': maintenance_summary,
        'timeRange': time_range,
        'fleetId': fleet_id,
        'totals': {
            'vehicles': total_vehicles,
            'activeVehicles': active_vehicles,
            'avgDriverScore': avg_driver_score,
            'safetyEvents': len(safety_events),
            'criticalSafetyEvents': critical_safety_events,
            'maintenanceAlerts': len(maintenance_alerts),
            'criticalMaintenanceAlerts': critical_maintenance_alerts
        }
    }

def get_safety_events_summary(safety_events: List[Dict]) -> List[Dict[str, Any]]:
    """
    Calculate safety events summary - matching frontend getSafetyEventsSummary()
    """
    event_types = {
        'hard_braking': len([e for e in safety_events if e.get('event_type') == 'hard_braking' or e.get('hard_braking_event')]),
        'lane_departure': len([e for e in safety_events if e.get('event_type') == 'lane_departure_violation' or e.get('lane_departure_event')]),
        'rapid_acceleration': len([e for e in safety_events if e.get('event_type') == 'rapid_acceleration' or e.get('rapid_acceleration_event')]),
        'speeding': len([e for e in safety_events if e.get('event_type') == 'speeding_violation' or e.get('speeding_event')]),
        'collision_warning': len([e for e in safety_events if e.get('event_type') == 'collision_warning' or e.get('collision_warning_event')])
    }
    
    return [
        {
            'label': 'Hard Braking',
            'value': event_types['hard_braking'],
            'status': 'active' if event_types['hard_braking'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if event_types['hard_braking'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Lane Departure',
            'value': event_types['lane_departure'],
            'status': 'active' if event_types['lane_departure'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if event_types['lane_departure'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Rapid Acceleration',
            'value': event_types['rapid_acceleration'],
            'status': 'active' if event_types['rapid_acceleration'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if event_types['rapid_acceleration'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Speeding',
            'value': event_types['speeding'],
            'status': 'active' if event_types['speeding'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if event_types['speeding'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Collision Warning',
            'value': event_types['collision_warning'],
            'status': 'active' if event_types['collision_warning'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if event_types['collision_warning'] > 0 else {'color': 'green', 'text': 'None'}
        }
    ]

def get_maintenance_alerts_summary(maintenance_alerts: List[Dict]) -> List[Dict[str, Any]]:
    """
    Calculate maintenance alerts summary - matching frontend getMaintenanceAlertsSummary()
    """
    alert_types = {
        'engine': len([a for a in maintenance_alerts if a.get('category') == 'ENGINE' or 'engine' in str(a.get('type', '')).lower()]),
        'brake': len([a for a in maintenance_alerts if a.get('category') == 'BRAKE' or 'brake' in str(a.get('type', '')).lower()]),
        'tire': len([a for a in maintenance_alerts if a.get('category') == 'TIRE' or 'tire' in str(a.get('type', '')).lower()]),
        'battery': len([a for a in maintenance_alerts if a.get('category') == 'BATTERY' or 'battery' in str(a.get('type', '')).lower()]),
        'oil': len([a for a in maintenance_alerts if a.get('category') == 'OIL' or 'oil' in str(a.get('type', '')).lower()])
    }
    
    return [
        {
            'label': 'Engine Issues',
            'value': alert_types['engine'],
            'status': 'active' if alert_types['engine'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if alert_types['engine'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Brake Maintenance',
            'value': alert_types['brake'],
            'status': 'active' if alert_types['brake'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if alert_types['brake'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Tire Issues',
            'value': alert_types['tire'],
            'status': 'active' if alert_types['tire'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if alert_types['tire'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Battery Alerts',
            'value': alert_types['battery'],
            'status': 'active' if alert_types['battery'] > 0 else 'none',
            'badge': {'color': 'red', 'text': 'Active'} if alert_types['battery'] > 0 else {'color': 'green', 'text': 'None'}
        },
        {
            'label': 'Oil Changes',
            'value': alert_types['oil'],
            'status': 'due' if alert_types['oil'] > 0 else 'current',
            'badge': {'color': 'blue', 'text': 'Due'} if alert_types['oil'] > 0 else {'color': 'green', 'text': 'Current'}
        }
    ]

def get_cached_metrics(time_range: str, fleet_id: str) -> Optional[Dict[str, Any]]:
    """Get cached metrics if available"""
    try:
        cache_key = f"METRICS#{fleet_id}#{time_range}"
        response = aggregated_metrics_table.get_item(Key={'PK': cache_key, 'SK': 'LATEST'})
        return response.get('Item')
    except Exception as e:
        logger.error(f"Error getting cached metrics: {str(e)}")
        return None

def is_cache_stale(cached_metrics: Dict[str, Any], max_age_minutes: int = 5) -> bool:
    """Check if cached metrics are stale"""
    try:
        cached_time = datetime.fromisoformat(cached_metrics['timestamp'])
        return (datetime.utcnow() - cached_time).total_seconds() > (max_age_minutes * 60)
    except:
        return True

def cache_metrics(metrics: Dict[str, Any], time_range: str, fleet_id: str):
    """Cache the calculated metrics"""
    try:
        cache_key = f"METRICS#{fleet_id}#{time_range}"
        cache_item = {
            'PK': cache_key,
            'SK': 'LATEST',
            'timestamp': metrics['timestamp'],
            'metrics': metrics,
            'ttl': int((datetime.utcnow() + timedelta(hours=1)).timestamp())  # Cache for 1 hour
        }
        aggregated_metrics_table.put_item(Item=cache_item)
        logger.info(f"Cached metrics for {cache_key}")
    except Exception as e:
        logger.error(f"Error caching metrics: {str(e)}")

def create_api_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create API Gateway response"""
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Content-Type': 'application/json',
            'Cache-Control': 'max-age=300'  # Cache for 5 minutes
        },
        'body': json.dumps(data, cls=DecimalEncoder)
    }

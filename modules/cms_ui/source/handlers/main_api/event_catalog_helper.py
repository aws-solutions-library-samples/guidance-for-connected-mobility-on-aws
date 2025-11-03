"""
Event Catalog Helper - Utilities for working with Event Catalog
"""

# Event Catalog mapping (can be loaded from DynamoDB in production)
EVENT_CATALOG = {
    "safety.harsh_braking": {"category": "safety", "severity": 1},
    "safety.harsh_acceleration": {"category": "safety", "severity": 1},
    "safety.harsh_cornering": {"category": "safety", "severity": 1},
    "safety.excessive_speed": {"category": "safety", "severity": 2},
    "safety.forward_collision_warning": {"category": "safety", "severity": 2},
    "safety.seatbelt_unfastened": {"category": "safety", "severity": 1},
    "maintenance.oil_change_due": {"category": "maintenance", "severity": 1},
    "maintenance.tire_pressure_low": {"category": "maintenance", "severity": 1},
    "maintenance.battery_low": {"category": "maintenance", "severity": 1},
    "maintenance.check_engine_light": {"category": "maintenance", "severity": 2},
    "maintenance.excessive_idle": {"category": "maintenance", "severity": 0},
    "maintenance.excessive_engine_speed": {"category": "maintenance", "severity": 1},
}

# Legacy eventType to event_id mapping
LEGACY_EVENT_MAPPING = {
    "harsh_braking": "safety.harsh_braking",
    "harsh_acceleration": "safety.harsh_acceleration",
    "harsh_cornering": "safety.harsh_cornering",
    "excessive_speed": "safety.excessive_speed",
    "forward_collision_warning": "safety.forward_collision_warning",
    "seatbelt_unfastened": "safety.seatbelt_unfastened",
    "oil_change_due": "maintenance.oil_change_due",
    "tire_pressure_low": "maintenance.tire_pressure_low",
    "battery_low": "maintenance.battery_low",
    "check_engine_light": "maintenance.check_engine_light",
    "excessive_idle": "maintenance.excessive_idle",
    "excessive_engine_speed": "maintenance.excessive_engine_speed",
}


def enrich_event_with_catalog(event):
    """
    Enrich event with Event Catalog fields if missing.
    Handles both new format (event_id) and legacy format (eventType).
    """
    # If event already has event_id, category, severity - return as is
    if all(k in event for k in ['event_id', 'category', 'severity']):
        return event
    
    # Try to get event_id from event or map from legacy eventType
    event_id = event.get('event_id')
    if not event_id and 'eventType' in event:
        event_id = LEGACY_EVENT_MAPPING.get(event['eventType'])
    
    # Enrich with catalog data if we have event_id
    if event_id and event_id in EVENT_CATALOG:
        catalog_data = EVENT_CATALOG[event_id]
        event['event_id'] = event_id
        event['category'] = catalog_data['category']
        event['severity'] = catalog_data['severity']
    
    return event


def normalize_event_response(event):
    """
    Normalize event for API response - ensure consistent field names.
    """
    # Ensure both eventType (legacy) and event_id (new) are present
    if 'event_id' in event and 'eventType' not in event:
        # Extract eventType from event_id (e.g., "safety.harsh_braking" -> "harsh_braking")
        event['eventType'] = event['event_id'].split('.')[-1]
    
    return event

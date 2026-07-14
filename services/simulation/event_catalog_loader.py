"""
Event Catalog Loader - Dynamically load event definitions from DynamoDB
"""
import boto3
import os
from typing import Dict, Optional
from functools import lru_cache

class EventCatalogLoader:
    """Load and cache Event Catalog from DynamoDB"""
    
    def __init__(self, profile_name: Optional[str] = None, region: str = "us-east-1"):
        """Initialize Event Catalog loader"""
        self.region = region
        
        # Initialize AWS session
        if profile_name:
            session = boto3.Session(profile_name=profile_name)
        else:
            session = boto3.Session()
        
        self.dynamodb = session.resource('dynamodb', region_name=region)
        
        # Detect table names
        self.event_catalog_table_name = self._detect_event_catalog_table()
        self.event_mappings_table_name = self._detect_event_mappings_table()
        
        # Cache
        self._catalog_cache = None
        self._mappings_cache = None
    
    def _detect_event_catalog_table(self) -> str:
        """Detect Event Catalog table name"""
        # Try environment variable first
        table_name = os.environ.get('EVENT_CATALOG_TABLE_NAME')
        if table_name:
            return table_name
        
        # Try common patterns
        patterns = ['cms-dev-event-catalog', 'event-catalog', 'cms-event-catalog']
        dynamodb_client = self.dynamodb.meta.client
        
        try:
            tables = dynamodb_client.list_tables()['TableNames']
            for pattern in patterns:
                for table in tables:
                    if pattern in table.lower():
                        return table
        except Exception as e:
            print(f"Warning: Could not detect Event Catalog table: {e}")
        
        return 'cms-dev-event-catalog'  # Default
    
    def _detect_event_mappings_table(self) -> str:
        """Detect Event Mappings table name"""
        table_name = os.environ.get('EVENT_MAPPINGS_TABLE_NAME')
        if table_name:
            return table_name
        
        patterns = ['cms-dev-oem-event-mappings', 'event-mappings', 'oem-event-mappings']
        dynamodb_client = self.dynamodb.meta.client
        
        try:
            tables = dynamodb_client.list_tables()['TableNames']
            for pattern in patterns:
                for table in tables:
                    if pattern in table.lower():
                        return table
        except Exception as e:
            print(f"Warning: Could not detect Event Mappings table: {e}")
        
        return 'cms-dev-oem-event-mappings'  # Default
    
    @lru_cache(maxsize=1)
    def load_event_catalog(self) -> Dict[str, Dict]:
        """Load all events from Event Catalog table (cached)"""
        if self._catalog_cache:
            return self._catalog_cache
        
        try:
            table = self.dynamodb.Table(self.event_catalog_table_name)
            response = table.scan()
            
            catalog = {}
            for item in response.get('Items', []):
                event_id = item.get('event_id')
                if event_id:
                    catalog[event_id] = {
                        'event_id': event_id,
                        'category': item.get('category', 'unknown'),
                        'severity': int(item.get('severity', 1)),
                        'description': item.get('description', ''),
                        'required_signals': item.get('required_signals', []),
                        'thresholds': item.get('thresholds', {})
                    }
            
            self._catalog_cache = catalog
            print(f"✅ Loaded {len(catalog)} events from Event Catalog")
            return catalog
            
        except Exception as e:
            print(f"⚠️  Could not load Event Catalog: {e}")
            return self._get_fallback_catalog()
    
    def _get_fallback_catalog(self) -> Dict[str, Dict]:
        """Fallback catalog if DynamoDB is unavailable"""
        return {
            'safety.harsh_braking': {'event_id': 'safety.harsh_braking', 'category': 'safety', 'severity': 1},
            'safety.harsh_acceleration': {'event_id': 'safety.harsh_acceleration', 'category': 'safety', 'severity': 1},
            'safety.harsh_cornering': {'event_id': 'safety.harsh_cornering', 'category': 'safety', 'severity': 1},
            'safety.excessive_speed': {'event_id': 'safety.excessive_speed', 'category': 'safety', 'severity': 2},
            'safety.forward_collision_warning': {'event_id': 'safety.forward_collision_warning', 'category': 'safety', 'severity': 2},
            'safety.seatbelt_unfastened': {'event_id': 'safety.seatbelt_unfastened', 'category': 'safety', 'severity': 1},
            'safety.phone_usage': {'event_id': 'safety.phone_usage', 'category': 'safety', 'severity': 2},
            'maintenance.check_engine_light': {'event_id': 'maintenance.check_engine_light', 'category': 'maintenance', 'severity': 2},
        }
    
    def get_event_by_id(self, event_id: str) -> Optional[Dict]:
        """Get event definition by event_id"""
        catalog = self.load_event_catalog()
        return catalog.get(event_id)
    
    def get_events_by_category(self, category: str) -> Dict[str, Dict]:
        """Get all events for a category"""
        catalog = self.load_event_catalog()
        return {k: v for k, v in catalog.items() if v.get('category') == category}
    
    def map_simulator_event(self, simulator_event_type: str) -> Optional[Dict]:
        """Map simulator event type to Event Catalog entry"""
        # Mapping for simulator event codes
        simulator_mapping = {
            'HB': 'safety.harsh_braking',
            'HARD_BRAKING': 'safety.harsh_braking',
            'RA': 'safety.harsh_acceleration',
            'RAPID_ACCELERATION': 'safety.harsh_acceleration',
            'SPEEDING': 'safety.excessive_speed',
            'FCW': 'safety.forward_collision_warning',
            'SV': 'safety.seatbelt_unfastened',
            'SEATBELT_VIOLATION': 'safety.seatbelt_unfastened',
            'PU': 'safety.phone_usage',
            'PHONE_USAGE': 'safety.phone_usage',
            'EC': 'maintenance.check_engine_light',
            'ENGINE_CRITICAL': 'maintenance.check_engine_light',
            'HARSH_CORNERING': 'safety.harsh_cornering',
            'LANE_DEPARTURE': 'safety.lane_departure',
            'TAILGATING': 'safety.tailgating',
            'DISTRACTED_DRIVING': 'safety.distracted_driving',
            'FATIGUE_DETECTION': 'safety.fatigue_detected',
        }
        
        event_id = simulator_mapping.get(simulator_event_type)
        if event_id:
            return self.get_event_by_id(event_id)
        
        return None


# Global instance (lazy loaded)
_catalog_loader = None

def get_catalog_loader(profile_name: Optional[str] = None, region: str = "us-east-1") -> EventCatalogLoader:
    """Get or create global Event Catalog loader"""
    global _catalog_loader
    if _catalog_loader is None:
        _catalog_loader = EventCatalogLoader(profile_name, region)
    return _catalog_loader

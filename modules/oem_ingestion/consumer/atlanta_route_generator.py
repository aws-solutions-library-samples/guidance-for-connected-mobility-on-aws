"""Generate realistic Atlanta route GPS coordinates"""
import math
from datetime import datetime, timedelta

class AtlantaRouteGenerator:
    """Generate GPS coordinates for common Atlanta routes"""
    
    # Downtown Atlanta to Hartsfield-Jackson Airport (10 miles)
    ROUTE_DOWNTOWN_TO_AIRPORT = [
        {"lat": 33.7490, "lng": -84.3880, "name": "Downtown Atlanta"},
        {"lat": 33.7470, "lng": -84.3850, "name": "Peachtree St"},
        {"lat": 33.7450, "lng": -84.3820, "name": "I-75/85 Merge"},
        {"lat": 33.7400, "lng": -84.3800, "name": "I-75 South"},
        {"lat": 33.7300, "lng": -84.3750, "name": "Exit 246"},
        {"lat": 33.7200, "lng": -84.3700, "name": "Turner Field Area"},
        {"lat": 33.7100, "lng": -84.3650, "name": "I-85 South"},
        {"lat": 33.7000, "lng": -84.3600, "name": "Exit 73"},
        {"lat": 33.6900, "lng": -84.3550, "name": "College Park"},
        {"lat": 33.6800, "lng": -84.3500, "name": "Airport Approach"},
        {"lat": 33.6700, "lng": -84.4280, "name": "Hartsfield-Jackson Airport"},
    ]
    
    # Buckhead to Midtown (5 miles)
    ROUTE_BUCKHEAD_TO_MIDTOWN = [
        {"lat": 33.8490, "lng": -84.3670, "name": "Buckhead"},
        {"lat": 33.8400, "lng": -84.3680, "name": "Peachtree Rd"},
        {"lat": 33.8300, "lng": -84.3690, "name": "Lenox Square"},
        {"lat": 33.8200, "lng": -84.3700, "name": "Piedmont Hospital"},
        {"lat": 33.8100, "lng": -84.3710, "name": "Ansley Park"},
        {"lat": 33.8000, "lng": -84.3720, "name": "Colony Square"},
        {"lat": 33.7900, "lng": -84.3730, "name": "Arts Center"},
        {"lat": 33.7800, "lng": -84.3850, "name": "Midtown"},
    ]
    
    def __init__(self, route_name="downtown_to_airport"):
        self.routes = {
            "downtown_to_airport": self.ROUTE_DOWNTOWN_TO_AIRPORT,
            "buckhead_to_midtown": self.ROUTE_BUCKHEAD_TO_MIDTOWN,
        }
        self.route = self.routes.get(route_name, self.ROUTE_DOWNTOWN_TO_AIRPORT)
        self.current_index = 0
    
    def get_next_position(self, speed_mph=35, timestamp=None):
        """
        Get next GPS position based on speed
        Returns: dict with lat, lng, heading, timestamp
        """
        if self.current_index >= len(self.route) - 1:
            self.current_index = 0  # Loop route
        
        current = self.route[self.current_index]
        next_point = self.route[self.current_index + 1]
        
        # Calculate heading (bearing) between points
        heading = self._calculate_bearing(
            current["lat"], current["lng"],
            next_point["lat"], next_point["lng"]
        )
        
        # Interpolate position based on speed
        # For simplicity, move to next waypoint
        self.current_index += 1
        
        return {
            "latitude": current["lat"],
            "longitude": current["lng"],
            "heading": heading,
            "speed_mph": speed_mph,
            "timestamp": timestamp or datetime.utcnow().isoformat() + "Z",
            "location_name": current["name"]
        }
    
    def _calculate_bearing(self, lat1, lng1, lat2, lng2):
        """Calculate bearing between two GPS points"""
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        lng_diff = math.radians(lng2 - lng1)
        
        x = math.sin(lng_diff) * math.cos(lat2_rad)
        y = math.cos(lat1_rad) * math.sin(lat2_rad) - \
            math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(lng_diff)
        
        bearing = math.atan2(x, y)
        bearing_deg = math.degrees(bearing)
        
        return (bearing_deg + 360) % 360  # Normalize to 0-360
    
    def reset(self):
        """Reset to start of route"""
        self.current_index = 0

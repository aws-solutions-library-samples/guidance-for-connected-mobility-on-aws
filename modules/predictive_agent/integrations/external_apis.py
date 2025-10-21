"""
External APIs Integration

Handles integration with external services:
- Weather APIs for environmental conditions
- Parts suppliers for inventory and pricing
- Service centers for scheduling and capacity
- Emergency services for critical alerts
"""

import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class ExternalAPIs:
    """
    Integration with external APIs and services
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # API configurations
        self.weather_api_key = config.get('weather_api_key')
        self.weather_api_url = config.get('weather_api_url', 'https://api.openweathermap.org/data/2.5')
        
        self.parts_api_config = config.get('parts_suppliers', {})
        self.service_centers_config = config.get('service_centers', {})
        self.emergency_config = config.get('emergency_services', {})
        
        # HTTP session for API calls
        self.session = None
        
        logger.info("External APIs integration initialized")
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def get_weather_conditions(self, latitude: float, longitude: float) -> Dict[str, Any]:
        """
        Get current weather conditions for a location
        """
        
        try:
            if not self.weather_api_key:
                logger.warning("Weather API key not configured, using default conditions")
                return self._get_default_weather()
            
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            # Call OpenWeatherMap API
            url = f"{self.weather_api_url}/weather"
            params = {
                'lat': latitude,
                'lon': longitude,
                'appid': self.weather_api_key,
                'units': 'metric'
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_weather_data(data)
                else:
                    logger.warning(f"Weather API returned status {response.status}")
                    return self._get_default_weather()
                    
        except Exception as e:
            logger.error(f"Error getting weather conditions: {str(e)}")
            return self._get_default_weather()
    
    def _parse_weather_data(self, weather_data: Dict) -> Dict[str, Any]:
        """Parse weather API response"""
        
        main_weather = weather_data.get('weather', [{}])[0].get('main', 'Clear').lower()
        
        # Map weather conditions to risk categories
        weather_condition = 'clear'
        if 'rain' in main_weather or 'drizzle' in main_weather:
            weather_condition = 'rain'
        elif 'snow' in main_weather:
            weather_condition = 'snow'
        elif 'fog' in main_weather or 'mist' in main_weather:
            weather_condition = 'fog'
        
        return {
            'temperature': weather_data.get('main', {}).get('temp', 20),
            'humidity': weather_data.get('main', {}).get('humidity', 50),
            'weather': weather_condition,
            'visibility': weather_data.get('visibility', 10000) / 1000,  # Convert to km
            'wind_speed': weather_data.get('wind', {}).get('speed', 0),
            'road_conditions': self._estimate_road_conditions(weather_condition, weather_data),
            'last_updated': datetime.utcnow().isoformat()
        }
    
    def _estimate_road_conditions(self, weather: str, weather_data: Dict) -> str:
        """Estimate road conditions based on weather"""
        
        temp = weather_data.get('main', {}).get('temp', 20)
        
        if weather == 'snow' or (weather == 'rain' and temp < 2):
            return 'poor'  # Ice/snow conditions
        elif weather == 'rain':
            return 'moderate'  # Wet roads
        elif weather == 'fog':
            return 'moderate'  # Reduced visibility
        else:
            return 'good'
    
    def _get_default_weather(self) -> Dict[str, Any]:
        """Return default weather conditions when API is unavailable"""
        
        return {
            'temperature': 20,
            'humidity': 50,
            'weather': 'clear',
            'visibility': 10,
            'wind_speed': 0,
            'road_conditions': 'good',
            'last_updated': datetime.utcnow().isoformat(),
            'source': 'default'
        }
    
    async def check_parts_availability(self, parts_needed: List[str]) -> Dict[str, Any]:
        """
        Check parts availability and pricing from suppliers
        """
        
        try:
            availability_results = {}
            
            for part in parts_needed:
                # In production, this would call actual parts supplier APIs
                # For now, simulate availability check
                availability_results[part] = await self._simulate_parts_check(part)
            
            return {
                'parts_availability': availability_results,
                'total_estimated_cost': sum(p['price'] for p in availability_results.values() if p['available']),
                'earliest_delivery': self._calculate_earliest_delivery(availability_results),
                'recommended_suppliers': self._get_recommended_suppliers(availability_results)
            }
            
        except Exception as e:
            logger.error(f"Error checking parts availability: {str(e)}")
            return {'error': str(e)}
    
    async def _simulate_parts_check(self, part: str) -> Dict[str, Any]:
        """Simulate parts availability check"""
        
        # Simulate API delay
        await asyncio.sleep(0.1)
        
        # Simulate availability and pricing
        base_prices = {
            'tire_fl': 200, 'tire_fr': 200, 'tire_rl': 200, 'tire_rr': 200,
            'brake_pads_front': 150, 'brake_pads_rear': 120,
            'oil_filter': 25, 'air_filter': 30,
            'battery_12v': 150, 'battery_ev': 8000
        }
        
        base_price = base_prices.get(part, 100)
        
        # Simulate 90% availability
        available = True  # In reality, this would be based on actual inventory
        
        return {
            'part_name': part,
            'available': available,
            'price': base_price,
            'quantity_available': 10 if available else 0,
            'estimated_delivery_days': 1 if available else 7,
            'supplier': 'AutoParts Plus',
            'last_updated': datetime.utcnow().isoformat()
        }
    
    def _calculate_earliest_delivery(self, availability_results: Dict) -> str:
        """Calculate earliest delivery date for all parts"""
        
        max_delivery_days = 0
        
        for part_info in availability_results.values():
            if part_info['available']:
                delivery_days = part_info.get('estimated_delivery_days', 1)
                max_delivery_days = max(max_delivery_days, delivery_days)
        
        earliest_date = datetime.utcnow() + timedelta(days=max_delivery_days)
        return earliest_date.isoformat()
    
    def _get_recommended_suppliers(self, availability_results: Dict) -> List[str]:
        """Get recommended suppliers based on availability and pricing"""
        
        suppliers = set()
        
        for part_info in availability_results.values():
            if part_info['available']:
                suppliers.add(part_info['supplier'])
        
        return list(suppliers)
    
    async def notify_emergency_services(self, decision) -> bool:
        """
        Notify emergency services for critical maintenance issues
        """
        
        try:
            # Only notify for critical safety issues
            if decision.urgency.value != 'critical':
                return False
            
            # Check if component is safety-critical
            safety_critical_components = ['tire', 'brake']
            if decision.component_type.value not in safety_critical_components:
                return False
            
            # In production, this would send notifications to:
            # - Fleet management systems
            # - Emergency response teams
            # - Vehicle operators
            
            logger.info(f"Emergency notification sent for {decision.vehicle_id} - {decision.component_type.value}")
            
            # Simulate notification
            notification_data = {
                'vehicle_id': decision.vehicle_id,
                'component': decision.component_type.value,
                'urgency': decision.urgency.value,
                'recommended_action': decision.recommended_action,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # In production, send to emergency services API
            return True
            
        except Exception as e:
            logger.error(f"Error sending emergency notification: {str(e)}")
            return False
    
    async def get_service_center_capacity(self, center_id: str, date: datetime) -> Dict[str, Any]:
        """
        Get service center capacity for a specific date
        """
        
        try:
            # In production, this would call service center APIs
            # For now, simulate capacity check
            
            # Simulate different capacity levels
            base_capacity = 15
            current_bookings = 8  # Simulate current bookings
            
            return {
                'center_id': center_id,
                'date': date.isoformat(),
                'total_capacity': base_capacity,
                'current_bookings': current_bookings,
                'available_slots': base_capacity - current_bookings,
                'next_available': (date + timedelta(hours=2)).isoformat(),
                'specialties': ['tire', 'brake', 'engine', 'general'],
                'cost_multiplier': 1.0
            }
            
        except Exception as e:
            logger.error(f"Error getting service center capacity: {str(e)}")
            return {'error': str(e)}
    
    async def update_parts_inventory(self, parts_used: List[str]) -> bool:
        """
        Update parts inventory after maintenance completion
        """
        
        try:
            # In production, this would update supplier inventory systems
            logger.info(f"Updated inventory for parts: {parts_used}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating parts inventory: {str(e)}")
            return False
    
    async def get_traffic_conditions(self, latitude: float, longitude: float) -> Dict[str, Any]:
        """
        Get current traffic conditions for route planning
        """
        
        try:
            # In production, this would call traffic APIs (Google Maps, HERE, etc.)
            # For now, simulate traffic conditions
            
            return {
                'traffic_level': 'moderate',
                'average_speed': 45,  # km/h
                'congestion_factor': 1.2,
                'estimated_delay': 5,  # minutes
                'last_updated': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting traffic conditions: {str(e)}")
            return {'traffic_level': 'unknown'}
    
    def get_api_health(self) -> Dict[str, Any]:
        """Get health status of external API integrations"""
        
        return {
            'weather_api': {
                'configured': bool(self.weather_api_key),
                'status': 'active' if self.weather_api_key else 'not_configured'
            },
            'parts_suppliers': {
                'configured': bool(self.parts_api_config),
                'count': len(self.parts_api_config)
            },
            'service_centers': {
                'configured': bool(self.service_centers_config),
                'count': len(self.service_centers_config)
            },
            'emergency_services': {
                'configured': bool(self.emergency_config),
                'status': 'active' if self.emergency_config else 'not_configured'
            }
        }
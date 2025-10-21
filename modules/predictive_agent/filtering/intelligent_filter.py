"""
Intelligent Telemetry Filtering System

This module implements the critical filtering logic that identifies which telemetry
messages need agent intelligence (1%) vs normal processing (99%).

The filter uses multiple criteria to catch potential issues while minimizing
false positives that would increase costs.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class FilterReason(Enum):
    THRESHOLD_BREACH = "threshold_breach"
    TREND_ANOMALY = "trend_anomaly"
    PATTERN_MATCH = "pattern_match"
    VEHICLE_RISK = "vehicle_risk"
    CORRELATION = "correlation"
    EXTERNAL_FACTOR = "external_factor"


@dataclass
class FilterResult:
    """Result of telemetry filtering"""
    should_escalate: bool
    confidence: float
    reasons: List[FilterReason]
    risk_score: int
    urgency: str
    estimated_time_to_failure: Optional[timedelta]


class IntelligentTelemetryFilter:
    """
    Multi-layered filtering system to identify telemetry needing agent intelligence
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Adaptive thresholds (learned from historical data)
        self.adaptive_thresholds = self._load_adaptive_thresholds()
        
        # Vehicle risk profiles (cached for performance)
        self.vehicle_risk_cache = {}
        self.cache_ttl = timedelta(hours=1)
        
        # Pattern definitions (learned failure patterns)
        self.failure_patterns = self._load_failure_patterns()
        
        # Recent telemetry cache for trend analysis
        self.recent_telemetry_cache = {}
        
        logger.info("Intelligent Telemetry Filter initialized")
    
    async def should_escalate_to_agent(self, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Main filtering logic - determines if telemetry needs agent intelligence
        
        Returns FilterResult indicating whether to escalate and why
        """
        
        vehicle_id = telemetry.get('vehicle_id')
        timestamp = datetime.fromisoformat(telemetry.get('timestamp', datetime.utcnow().isoformat()))
        
        # Initialize filter result
        result = FilterResult(
            should_escalate=False,
            confidence=0.0,
            reasons=[],
            risk_score=0,
            urgency='normal',
            estimated_time_to_failure=None
        )
        
        # Layer 1: Critical threshold breaches (immediate escalation)
        threshold_result = await self._check_critical_thresholds(telemetry)
        if threshold_result.should_escalate:
            result.should_escalate = True
            result.reasons.extend(threshold_result.reasons)
            result.risk_score += threshold_result.risk_score
            result.urgency = 'critical'
        
        # Layer 2: Trend anomaly detection
        trend_result = await self._check_trend_anomalies(vehicle_id, telemetry)
        if trend_result.should_escalate:
            result.should_escalate = True
            result.reasons.extend(trend_result.reasons)
            result.risk_score += trend_result.risk_score
            if result.urgency == 'normal':
                result.urgency = 'high'
        
        # Layer 3: Pattern matching (learned failure patterns)
        pattern_result = await self._check_failure_patterns(vehicle_id, telemetry)
        if pattern_result.should_escalate:
            result.should_escalate = True
            result.reasons.extend(pattern_result.reasons)
            result.risk_score += pattern_result.risk_score
            if result.urgency == 'normal':
                result.urgency = 'medium'
        
        # Layer 4: Vehicle-specific risk factors
        risk_result = await self._check_vehicle_risk_factors(vehicle_id, telemetry)
        if risk_result.should_escalate:
            result.should_escalate = True
            result.reasons.extend(risk_result.reasons)
            result.risk_score += risk_result.risk_score
        
        # Layer 5: Cross-vehicle correlations
        correlation_result = await self._check_correlations(vehicle_id, telemetry)
        if correlation_result.should_escalate:
            result.should_escalate = True
            result.reasons.extend(correlation_result.reasons)
            result.risk_score += correlation_result.risk_score
        
        # Layer 6: External factors (weather, traffic, etc.)
        external_result = await self._check_external_factors(vehicle_id, telemetry)
        if external_result.should_escalate:
            result.should_escalate = True
            result.reasons.extend(external_result.reasons)
            result.risk_score += external_result.risk_score
        
        # Calculate overall confidence and final decision
        result.confidence = min(1.0, result.risk_score / 100.0)
        
        # Final escalation decision based on risk score
        if result.risk_score >= 70:  # High confidence threshold
            result.should_escalate = True
        elif result.risk_score >= 40 and len(result.reasons) >= 2:  # Multiple moderate indicators
            result.should_escalate = True
        
        # Update cache for future trend analysis
        await self._update_telemetry_cache(vehicle_id, telemetry, timestamp)
        
        return result
    
    async def _check_critical_thresholds(self, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Layer 1: Check for critical threshold breaches that need immediate attention
        """
        
        result = FilterResult(False, 0.0, [], 0, 'normal', None)
        vehicle_id = telemetry.get('vehicle_id')
        
        # Get adaptive thresholds for this vehicle (or defaults)
        thresholds = self.adaptive_thresholds.get(vehicle_id, self._get_default_thresholds())
        
        # Tire pressure checks
        tire_positions = ['fl', 'fr', 'rl', 'rr']
        for position in tire_positions:
            pressure_key = f'tire_pressure_{position}'
            temp_key = f'tire_temperature_{position}'
            
            if pressure_key in telemetry:
                pressure = telemetry[pressure_key]
                
                # Critical pressure thresholds
                if pressure < thresholds['tire_pressure_critical']:
                    result.should_escalate = True
                    result.reasons.append(FilterReason.THRESHOLD_BREACH)
                    result.risk_score += 50
                    result.urgency = 'critical'
                    result.estimated_time_to_failure = timedelta(minutes=30)
                    
                elif pressure < thresholds['tire_pressure_warning']:
                    result.should_escalate = True
                    result.reasons.append(FilterReason.THRESHOLD_BREACH)
                    result.risk_score += 30
                    result.urgency = 'high'
                    result.estimated_time_to_failure = timedelta(hours=4)
            
            # Temperature checks
            if temp_key in telemetry:
                temperature = telemetry[temp_key]
                
                if temperature > thresholds['tire_temperature_critical']:
                    result.should_escalate = True
                    result.reasons.append(FilterReason.THRESHOLD_BREACH)
                    result.risk_score += 40
                    result.urgency = 'critical'
                    result.estimated_time_to_failure = timedelta(minutes=15)
        
        # Engine temperature
        if 'engine_temperature' in telemetry:
            engine_temp = telemetry['engine_temperature']
            if engine_temp > thresholds['engine_temperature_critical']:
                result.should_escalate = True
                result.reasons.append(FilterReason.THRESHOLD_BREACH)
                result.risk_score += 45
                result.urgency = 'critical'
        
        # Brake wear
        if 'brake_wear_front' in telemetry:
            brake_wear = telemetry['brake_wear_front']
            if brake_wear > thresholds['brake_wear_critical']:
                result.should_escalate = True
                result.reasons.append(FilterReason.THRESHOLD_BREACH)
                result.risk_score += 35
                result.urgency = 'high'
        
        return result
    
    async def _check_trend_anomalies(self, vehicle_id: str, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Layer 2: Detect concerning trends in telemetry data
        """
        
        result = FilterResult(False, 0.0, [], 0, 'normal', None)
        
        # Get recent telemetry for trend analysis
        recent_data = self.recent_telemetry_cache.get(vehicle_id, [])
        
        if len(recent_data) < 5:  # Need at least 5 data points
            return result
        
        # Analyze tire pressure trends
        for position in ['fl', 'fr', 'rl', 'rr']:
            pressure_key = f'tire_pressure_{position}'
            
            if pressure_key in telemetry:
                current_pressure = telemetry[pressure_key]
                
                # Extract recent pressures for this position
                recent_pressures = []
                for data_point in recent_data[-10:]:  # Last 10 readings
                    if pressure_key in data_point:
                        recent_pressures.append((
                            datetime.fromisoformat(data_point['timestamp']),
                            data_point[pressure_key]
                        ))
                
                if len(recent_pressures) >= 3:
                    # Calculate pressure drop rate
                    drop_rate = self._calculate_pressure_drop_rate(recent_pressures, current_pressure)
                    
                    if drop_rate > 50:  # > 50 mbar/hour
                        result.should_escalate = True
                        result.reasons.append(FilterReason.TREND_ANOMALY)
                        result.risk_score += 25
                        result.urgency = 'high'
                        
                        # Estimate time to critical pressure
                        time_to_critical = (current_pressure - 1800) / drop_rate  # hours
                        result.estimated_time_to_failure = timedelta(hours=max(1, time_to_critical))
                    
                    elif drop_rate > 20:  # > 20 mbar/hour
                        result.should_escalate = True
                        result.reasons.append(FilterReason.TREND_ANOMALY)
                        result.risk_score += 15
                        result.urgency = 'medium'
        
        # Analyze engine temperature trends
        if 'engine_temperature' in telemetry:
            engine_temp_trend = self._analyze_engine_temp_trend(recent_data, telemetry)
            if engine_temp_trend['concerning']:
                result.should_escalate = True
                result.reasons.append(FilterReason.TREND_ANOMALY)
                result.risk_score += engine_temp_trend['risk_score']
        
        return result
    
    async def _check_failure_patterns(self, vehicle_id: str, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Layer 3: Check against learned failure patterns
        """
        
        result = FilterResult(False, 0.0, [], 0, 'normal', None)
        
        # Check each learned pattern
        for pattern in self.failure_patterns:
            if await self._matches_failure_pattern(telemetry, pattern):
                result.should_escalate = True
                result.reasons.append(FilterReason.PATTERN_MATCH)
                result.risk_score += pattern['risk_score']
                
                if pattern['urgency'] == 'high' and result.urgency == 'normal':
                    result.urgency = 'high'
                
                # Use pattern's time-to-failure estimate
                if pattern.get('time_to_failure_hours'):
                    result.estimated_time_to_failure = timedelta(hours=pattern['time_to_failure_hours'])
        
        return result
    
    async def _check_vehicle_risk_factors(self, vehicle_id: str, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Layer 4: Check vehicle-specific risk factors
        """
        
        result = FilterResult(False, 0.0, [], 0, 'normal', None)
        
        # Get vehicle risk profile (cached)
        risk_profile = await self._get_vehicle_risk_profile(vehicle_id)
        
        # High-risk vehicle multiplier
        if risk_profile['risk_level'] == 'high':
            # Lower thresholds for high-risk vehicles
            if self._has_moderate_anomalies(telemetry):
                result.should_escalate = True
                result.reasons.append(FilterReason.VEHICLE_RISK)
                result.risk_score += 20
        
        # Commercial vehicle considerations
        if risk_profile['usage_pattern'] == 'commercial':
            if self._has_commercial_risk_indicators(telemetry):
                result.should_escalate = True
                result.reasons.append(FilterReason.VEHICLE_RISK)
                result.risk_score += 15
        
        # High mileage vehicle considerations
        if risk_profile['mileage'] > 100000:
            if self._has_wear_indicators(telemetry):
                result.should_escalate = True
                result.reasons.append(FilterReason.VEHICLE_RISK)
                result.risk_score += 10
        
        return result
    
    async def _check_correlations(self, vehicle_id: str, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Layer 5: Check for cross-vehicle correlations
        """
        
        result = FilterResult(False, 0.0, [], 0, 'normal', None)
        
        # Check if similar vehicles are experiencing issues
        similar_vehicles = await self._get_similar_vehicles(vehicle_id)
        
        for similar_vehicle_id in similar_vehicles:
            recent_issues = await self._get_recent_issues(similar_vehicle_id)
            
            if recent_issues and self._telemetry_shows_similar_pattern(telemetry, recent_issues):
                result.should_escalate = True
                result.reasons.append(FilterReason.CORRELATION)
                result.risk_score += 15
        
        return result
    
    async def _check_external_factors(self, vehicle_id: str, telemetry: Dict[str, Any]) -> FilterResult:
        """
        Layer 6: Check external factors that increase risk
        """
        
        result = FilterResult(False, 0.0, [], 0, 'normal', None)
        
        # Get vehicle location and external conditions
        location = await self._get_vehicle_location(vehicle_id)
        weather = await self._get_weather_conditions(location)
        
        # Bad weather increases risk thresholds
        if weather['condition'] in ['rain', 'snow', 'ice']:
            if self._has_weather_sensitive_issues(telemetry):
                result.should_escalate = True
                result.reasons.append(FilterReason.EXTERNAL_FACTOR)
                result.risk_score += 10
        
        # High traffic areas increase brake wear risk
        traffic = await self._get_traffic_conditions(location)
        if traffic['level'] == 'heavy':
            if self._has_traffic_related_issues(telemetry):
                result.should_escalate = True
                result.reasons.append(FilterReason.EXTERNAL_FACTOR)
                result.risk_score += 5
        
        return result
    
    # Helper methods for pattern matching and analysis
    
    def _calculate_pressure_drop_rate(self, recent_pressures: List[Tuple], current_pressure: float) -> float:
        """Calculate pressure drop rate in mbar/hour"""
        
        if len(recent_pressures) < 2:
            return 0.0
        
        # Sort by timestamp
        recent_pressures.sort(key=lambda x: x[0])
        
        # Calculate rate over last few readings
        time_span = (recent_pressures[-1][0] - recent_pressures[0][0]).total_seconds() / 3600  # hours
        pressure_drop = recent_pressures[0][1] - current_pressure  # mbar
        
        return pressure_drop / time_span if time_span > 0 else 0.0
    
    async def _matches_failure_pattern(self, telemetry: Dict[str, Any], pattern: Dict[str, Any]) -> bool:
        """Check if telemetry matches a learned failure pattern"""
        
        pattern_conditions = pattern['conditions']
        matches = 0
        
        for condition in pattern_conditions:
            field = condition['field']
            operator = condition['operator']
            value = condition['value']
            
            if field in telemetry:
                telemetry_value = telemetry[field]
                
                if operator == 'lt' and telemetry_value < value:
                    matches += 1
                elif operator == 'gt' and telemetry_value > value:
                    matches += 1
                elif operator == 'eq' and telemetry_value == value:
                    matches += 1
        
        # Pattern matches if most conditions are met
        return matches >= len(pattern_conditions) * 0.7
    
    def _load_adaptive_thresholds(self) -> Dict[str, Dict[str, float]]:
        """Load adaptive thresholds (learned from historical data)"""
        
        # In production, this would load from database/cache
        return {
            'default': {
                'tire_pressure_critical': 1800,
                'tire_pressure_warning': 2000,
                'tire_temperature_critical': 80,
                'tire_temperature_warning': 70,
                'engine_temperature_critical': 110,
                'brake_wear_critical': 85
            }
        }
    
    def _load_failure_patterns(self) -> List[Dict[str, Any]]:
        """Load learned failure patterns"""
        
        return [
            {
                'pattern_id': 'gradual_pressure_loss',
                'conditions': [
                    {'field': 'tire_pressure_fl', 'operator': 'lt', 'value': 2100},
                    {'field': 'tire_temperature_fl', 'operator': 'gt', 'value': 50}
                ],
                'risk_score': 25,
                'urgency': 'medium',
                'time_to_failure_hours': 8,
                'confidence': 0.8
            },
            {
                'pattern_id': 'overheating_pattern',
                'conditions': [
                    {'field': 'engine_temperature', 'operator': 'gt', 'value': 95},
                    {'field': 'tire_temperature_fl', 'operator': 'gt', 'value': 65},
                    {'field': 'tire_temperature_fr', 'operator': 'gt', 'value': 65}
                ],
                'risk_score': 35,
                'urgency': 'high',
                'time_to_failure_hours': 2,
                'confidence': 0.9
            }
        ]
    
    def _get_default_thresholds(self) -> Dict[str, float]:
        """Get default thresholds when vehicle-specific ones aren't available"""
        return self.adaptive_thresholds['default']
    
    async def _update_telemetry_cache(self, vehicle_id: str, telemetry: Dict[str, Any], timestamp: datetime):
        """Update recent telemetry cache for trend analysis"""
        
        if vehicle_id not in self.recent_telemetry_cache:
            self.recent_telemetry_cache[vehicle_id] = []
        
        # Add current telemetry
        self.recent_telemetry_cache[vehicle_id].append({
            **telemetry,
            'timestamp': timestamp.isoformat()
        })
        
        # Keep only last 20 readings
        self.recent_telemetry_cache[vehicle_id] = self.recent_telemetry_cache[vehicle_id][-20:]
    
    # Placeholder methods for external data (implement based on your infrastructure)
    
    async def _get_vehicle_risk_profile(self, vehicle_id: str) -> Dict[str, Any]:
        """Get vehicle risk profile from cache/database"""
        return {
            'risk_level': 'medium',
            'usage_pattern': 'mixed',
            'mileage': 45000,
            'last_service': '2024-09-15'
        }
    
    async def _get_similar_vehicles(self, vehicle_id: str) -> List[str]:
        """Get list of similar vehicles for correlation analysis"""
        return []  # Implement based on your vehicle classification
    
    async def _get_vehicle_location(self, vehicle_id: str) -> Dict[str, float]:
        """Get current vehicle location"""
        return {'latitude': 40.7128, 'longitude': -74.0060}
    
    async def _get_weather_conditions(self, location: Dict[str, float]) -> Dict[str, Any]:
        """Get weather conditions for location"""
        return {'condition': 'clear', 'temperature': 25}
    
    def _has_moderate_anomalies(self, telemetry: Dict[str, Any]) -> bool:
        """Check for moderate anomalies in telemetry"""
        return False  # Implement based on your criteria
    
    def _has_commercial_risk_indicators(self, telemetry: Dict[str, Any]) -> bool:
        """Check for commercial vehicle risk indicators"""
        return False  # Implement based on your criteria
"""
Tire Prediction Model

ML model for predicting tire maintenance needs based on:
- Tire pressure and temperature telemetry
- Tread depth measurements
- Driving patterns and road conditions
- Vehicle load and usage patterns
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TireTelemetryData:
    """Structured tire telemetry data"""
    vehicle_id: str
    timestamp: datetime
    position: str  # FL, FR, RL, RR
    pressure_mbar: float
    temperature_celsius: float
    tread_depth_mm: Optional[float]
    condition: str  # NORMAL, WARNING, CRITICAL
    latitude: float
    longitude: float


class TirePredictionModel:
    """
    Advanced tire prediction model using multiple data sources
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_version = config.get('model_version', '1.0')
        
        # Model parameters (in production, these would be loaded from trained model)
        self.pressure_thresholds = {
            'critical_low': 1800,  # mbar
            'warning_low': 2000,
            'optimal_min': 2200,
            'optimal_max': 2400,
            'warning_high': 2600,
            'critical_high': 2800
        }
        
        self.temperature_thresholds = {
            'critical_low': -10,   # celsius
            'warning_low': 0,
            'optimal_min': 10,
            'optimal_max': 60,
            'warning_high': 70,
            'critical_high': 80
        }
        
        self.tread_depth_thresholds = {
            'critical': 1.6,      # mm - legal minimum
            'warning': 3.0,       # mm - replacement recommended
            'good': 6.0           # mm - good condition
        }
        
        logger.info(f"Tire Prediction Model v{self.model_version} initialized")
    
    async def predict(self, tire_telemetry: Dict[str, Any], vehicle_context: Any) -> Dict[str, Any]:
        """
        Predict tire maintenance needs based on telemetry and context
        """
        
        try:
            # Parse tire telemetry data
            tire_data = self._parse_tire_telemetry(tire_telemetry)
            
            if not tire_data:
                return {'error': 'No valid tire telemetry data'}
            
            # Analyze each tire position
            tire_predictions = {}
            overall_failure_probability = 0.0
            critical_issues = []
            
            for position, data in tire_data.items():
                prediction = await self._predict_tire_failure(data, vehicle_context)
                tire_predictions[position] = prediction
                
                # Track highest failure probability
                if prediction['failure_probability'] > overall_failure_probability:
                    overall_failure_probability = prediction['failure_probability']
                
                # Collect critical issues
                if prediction['urgency'] == 'critical':
                    critical_issues.append(f"{position}: {prediction['failure_mode']}")
            
            # Determine overall maintenance recommendation
            maintenance_recommendation = self._generate_maintenance_recommendation(
                tire_predictions, overall_failure_probability
            )
            
            # Calculate predicted failure date
            predicted_failure_date = self._calculate_failure_date(
                tire_predictions, vehicle_context
            )
            
            return {
                'failure_probability': overall_failure_probability,
                'predicted_failure_date': predicted_failure_date,
                'confidence': self._calculate_confidence(tire_predictions),
                'failure_mode': self._determine_primary_failure_mode(tire_predictions),
                'tire_predictions': tire_predictions,
                'critical_issues': critical_issues,
                'parts_needed': self._determine_parts_needed(tire_predictions),
                'service_time_hours': self._estimate_service_time(tire_predictions),
                'maintenance_recommendation': maintenance_recommendation,
                'model_version': self.model_version
            }
            
        except Exception as e:
            logger.error(f"Error in tire prediction: {str(e)}")
            return {'error': str(e)}
    
    def _parse_tire_telemetry(self, telemetry: Dict[str, Any]) -> Dict[str, TireTelemetryData]:
        """Parse raw telemetry into structured tire data"""
        
        tire_data = {}
        
        # Handle different telemetry formats
        if 'tires' in telemetry:
            # Array format with multiple tire readings
            for tire_reading in telemetry['tires']:
                position = tire_reading.get('position', 'unknown')
                if position != 'unknown':
                    tire_data[position] = TireTelemetryData(
                        vehicle_id=telemetry.get('vehicle_id', ''),
                        timestamp=datetime.fromisoformat(tire_reading.get('timestamp', datetime.utcnow().isoformat())),
                        position=position,
                        pressure_mbar=tire_reading.get('pressure_mbar', 0),
                        temperature_celsius=tire_reading.get('temperature_celsius', 20),
                        tread_depth_mm=tire_reading.get('tread_depth_mm'),
                        condition=tire_reading.get('condition', 'NORMAL'),
                        latitude=tire_reading.get('latitude', 0),
                        longitude=tire_reading.get('longitude', 0)
                    )
        
        else:
            # Individual tire fields format
            positions = ['FL', 'FR', 'RL', 'RR']
            for position in positions:
                pressure_key = f'tpms_pressure_{position.lower()}_mbar'
                temp_key = f'tpms_temperature_{position.lower()}_celsius'
                
                if pressure_key in telemetry:
                    tire_data[position] = TireTelemetryData(
                        vehicle_id=telemetry.get('vehicle_id', ''),
                        timestamp=datetime.fromisoformat(telemetry.get('timestamp', datetime.utcnow().isoformat())),
                        position=position,
                        pressure_mbar=telemetry.get(pressure_key, 0),
                        temperature_celsius=telemetry.get(temp_key, 20),
                        tread_depth_mm=telemetry.get(f'tread_depth_{position.lower()}_mm'),
                        condition=telemetry.get(f'tpms_condition_{position.lower()}', 'NORMAL'),
                        latitude=telemetry.get('latitude', 0),
                        longitude=telemetry.get('longitude', 0)
                    )
        
        return tire_data
    
    async def _predict_tire_failure(self, tire_data: TireTelemetryData, vehicle_context: Any) -> Dict[str, Any]:
        """Predict failure for individual tire"""
        
        failure_factors = []
        failure_probability = 0.0
        urgency = 'monitor'
        failure_mode = 'normal_wear'
        
        # Analyze pressure
        pressure_analysis = self._analyze_pressure(tire_data.pressure_mbar)
        failure_factors.append(pressure_analysis)
        
        # Analyze temperature
        temperature_analysis = self._analyze_temperature(tire_data.temperature_celsius)
        failure_factors.append(temperature_analysis)
        
        # Analyze tread depth if available
        if tire_data.tread_depth_mm is not None:
            tread_analysis = self._analyze_tread_depth(tire_data.tread_depth_mm)
            failure_factors.append(tread_analysis)
        
        # Analyze driving conditions
        driving_analysis = self._analyze_driving_conditions(tire_data, vehicle_context)
        failure_factors.append(driving_analysis)
        
        # Combine factors to determine overall failure probability
        failure_probability = self._combine_failure_factors(failure_factors)
        
        # Determine urgency and failure mode
        urgency, failure_mode = self._determine_urgency_and_mode(failure_factors, failure_probability)
        
        return {
            'position': tire_data.position,
            'failure_probability': failure_probability,
            'urgency': urgency,
            'failure_mode': failure_mode,
            'factors': failure_factors,
            'current_pressure': tire_data.pressure_mbar,
            'current_temperature': tire_data.temperature_celsius,
            'current_tread_depth': tire_data.tread_depth_mm
        }
    
    def _analyze_pressure(self, pressure_mbar: float) -> Dict[str, Any]:
        """Analyze tire pressure for failure indicators"""
        
        if pressure_mbar <= self.pressure_thresholds['critical_low']:
            return {
                'factor': 'pressure',
                'severity': 'critical',
                'score': 0.9,
                'description': f'Critically low pressure: {pressure_mbar} mbar'
            }
        elif pressure_mbar <= self.pressure_thresholds['warning_low']:
            return {
                'factor': 'pressure',
                'severity': 'high',
                'score': 0.7,
                'description': f'Low pressure: {pressure_mbar} mbar'
            }
        elif pressure_mbar >= self.pressure_thresholds['critical_high']:
            return {
                'factor': 'pressure',
                'severity': 'high',
                'score': 0.6,
                'description': f'Critically high pressure: {pressure_mbar} mbar'
            }
        elif pressure_mbar >= self.pressure_thresholds['warning_high']:
            return {
                'factor': 'pressure',
                'severity': 'medium',
                'score': 0.4,
                'description': f'High pressure: {pressure_mbar} mbar'
            }
        else:
            return {
                'factor': 'pressure',
                'severity': 'normal',
                'score': 0.1,
                'description': f'Normal pressure: {pressure_mbar} mbar'
            }
    
    def _analyze_temperature(self, temperature_celsius: float) -> Dict[str, Any]:
        """Analyze tire temperature for failure indicators"""
        
        if temperature_celsius >= self.temperature_thresholds['critical_high']:
            return {
                'factor': 'temperature',
                'severity': 'critical',
                'score': 0.8,
                'description': f'Critically high temperature: {temperature_celsius}°C'
            }
        elif temperature_celsius >= self.temperature_thresholds['warning_high']:
            return {
                'factor': 'temperature',
                'severity': 'high',
                'score': 0.6,
                'description': f'High temperature: {temperature_celsius}°C'
            }
        elif temperature_celsius <= self.temperature_thresholds['critical_low']:
            return {
                'factor': 'temperature',
                'severity': 'medium',
                'score': 0.3,
                'description': f'Very low temperature: {temperature_celsius}°C'
            }
        else:
            return {
                'factor': 'temperature',
                'severity': 'normal',
                'score': 0.1,
                'description': f'Normal temperature: {temperature_celsius}°C'
            }
    
    def _analyze_tread_depth(self, tread_depth_mm: float) -> Dict[str, Any]:
        """Analyze tread depth for replacement needs"""
        
        if tread_depth_mm <= self.tread_depth_thresholds['critical']:
            return {
                'factor': 'tread_depth',
                'severity': 'critical',
                'score': 0.95,
                'description': f'Tread depth below legal limit: {tread_depth_mm}mm'
            }
        elif tread_depth_mm <= self.tread_depth_thresholds['warning']:
            return {
                'factor': 'tread_depth',
                'severity': 'high',
                'score': 0.7,
                'description': f'Low tread depth: {tread_depth_mm}mm'
            }
        elif tread_depth_mm <= self.tread_depth_thresholds['good']:
            return {
                'factor': 'tread_depth',
                'severity': 'medium',
                'score': 0.4,
                'description': f'Moderate tread wear: {tread_depth_mm}mm'
            }
        else:
            return {
                'factor': 'tread_depth',
                'severity': 'normal',
                'score': 0.1,
                'description': f'Good tread depth: {tread_depth_mm}mm'
            }
    
    def _analyze_driving_conditions(self, tire_data: TireTelemetryData, vehicle_context: Any) -> Dict[str, Any]:
        """Analyze driving conditions impact on tire wear"""
        
        # Get driving behavior score from context
        driver_score = getattr(vehicle_context, 'driver_behavior_score', 0.5)
        usage_pattern = getattr(vehicle_context, 'usage_pattern', 'mixed')
        
        # Calculate wear factor based on usage
        wear_factor = 0.1  # Base wear
        
        if usage_pattern == 'commercial':
            wear_factor += 0.3
        elif usage_pattern == 'city':
            wear_factor += 0.2
        elif usage_pattern == 'highway':
            wear_factor += 0.1
        
        # Adjust for driver behavior (lower score = more aggressive driving)
        if driver_score < 0.3:
            wear_factor += 0.4
        elif driver_score < 0.6:
            wear_factor += 0.2
        
        severity = 'normal'
        if wear_factor >= 0.6:
            severity = 'high'
        elif wear_factor >= 0.4:
            severity = 'medium'
        
        return {
            'factor': 'driving_conditions',
            'severity': severity,
            'score': min(wear_factor, 0.8),
            'description': f'Usage pattern: {usage_pattern}, driver score: {driver_score:.2f}'
        }
    
    def _combine_failure_factors(self, factors: List[Dict[str, Any]]) -> float:
        """Combine multiple failure factors into overall probability"""
        
        # Weight factors by importance
        weights = {
            'pressure': 0.3,
            'temperature': 0.25,
            'tread_depth': 0.35,
            'driving_conditions': 0.1
        }
        
        weighted_score = 0.0
        total_weight = 0.0
        
        for factor in factors:
            factor_name = factor['factor']
            weight = weights.get(factor_name, 0.1)
            weighted_score += factor['score'] * weight
            total_weight += weight
        
        # Normalize to probability
        if total_weight > 0:
            probability = weighted_score / total_weight
        else:
            probability = 0.0
        
        return min(probability, 1.0)
    
    def _determine_urgency_and_mode(self, factors: List[Dict[str, Any]], probability: float) -> tuple:
        """Determine urgency level and primary failure mode"""
        
        # Find most severe factor
        critical_factors = [f for f in factors if f['severity'] == 'critical']
        high_factors = [f for f in factors if f['severity'] == 'high']
        
        if critical_factors:
            urgency = 'critical'
            failure_mode = critical_factors[0]['factor'] + '_critical'
        elif high_factors:
            urgency = 'high'
            failure_mode = high_factors[0]['factor'] + '_warning'
        elif probability >= 0.5:
            urgency = 'medium'
            failure_mode = 'general_wear'
        elif probability >= 0.3:
            urgency = 'low'
            failure_mode = 'normal_wear'
        else:
            urgency = 'monitor'
            failure_mode = 'normal_wear'
        
        return urgency, failure_mode
    
    def _generate_maintenance_recommendation(self, tire_predictions: Dict, overall_probability: float) -> str:
        """Generate human-readable maintenance recommendation"""
        
        critical_tires = [pos for pos, pred in tire_predictions.items() if pred['urgency'] == 'critical']
        high_tires = [pos for pos, pred in tire_predictions.items() if pred['urgency'] == 'high']
        
        if critical_tires:
            return f"IMMEDIATE ACTION REQUIRED: Replace tires at positions {', '.join(critical_tires)}"
        elif high_tires:
            return f"Schedule tire replacement for positions {', '.join(high_tires)} within 1-3 days"
        elif overall_probability >= 0.5:
            return "Schedule tire inspection and consider replacement within 1-2 weeks"
        elif overall_probability >= 0.3:
            return "Monitor tire condition closely, schedule inspection within 1 month"
        else:
            return "Continue normal tire monitoring"
    
    def _calculate_failure_date(self, tire_predictions: Dict, vehicle_context: Any) -> Optional[datetime]:
        """Calculate predicted failure date based on wear patterns"""
        
        # Find tire with highest failure probability
        max_probability = 0.0
        critical_tire = None
        
        for position, prediction in tire_predictions.items():
            if prediction['failure_probability'] > max_probability:
                max_probability = prediction['failure_probability']
                critical_tire = prediction
        
        if not critical_tire or max_probability < 0.3:
            return None
        
        # Estimate days to failure based on probability and wear rate
        if max_probability >= 0.9:
            days_to_failure = 1
        elif max_probability >= 0.7:
            days_to_failure = 7
        elif max_probability >= 0.5:
            days_to_failure = 30
        else:
            days_to_failure = 90
        
        # Adjust based on usage pattern
        usage_pattern = getattr(vehicle_context, 'usage_pattern', 'mixed')
        if usage_pattern == 'commercial':
            days_to_failure = int(days_to_failure * 0.7)  # Faster wear
        elif usage_pattern == 'highway':
            days_to_failure = int(days_to_failure * 1.2)  # Slower wear
        
        return datetime.utcnow() + timedelta(days=days_to_failure)
    
    def _calculate_confidence(self, tire_predictions: Dict) -> float:
        """Calculate confidence in the overall prediction"""
        
        # Base confidence on data availability and consistency
        confidence = 0.8  # Base confidence
        
        # Reduce confidence if missing tread depth data
        tires_with_tread_data = sum(1 for pred in tire_predictions.values() 
                                  if pred.get('current_tread_depth') is not None)
        
        if tires_with_tread_data == 0:
            confidence -= 0.2
        elif tires_with_tread_data < len(tire_predictions):
            confidence -= 0.1
        
        # Increase confidence if multiple tires show consistent patterns
        failure_probs = [pred['failure_probability'] for pred in tire_predictions.values()]
        if len(failure_probs) > 1:
            prob_variance = np.var(failure_probs)
            if prob_variance < 0.1:  # Consistent predictions
                confidence += 0.1
        
        return min(confidence, 1.0)
    
    def _determine_primary_failure_mode(self, tire_predictions: Dict) -> str:
        """Determine the primary failure mode across all tires"""
        
        failure_modes = [pred['failure_mode'] for pred in tire_predictions.values()]
        
        # Count occurrences of each failure mode
        mode_counts = {}
        for mode in failure_modes:
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
        
        # Return most common failure mode
        if mode_counts:
            return max(mode_counts.items(), key=lambda x: x[1])[0]
        else:
            return 'normal_wear'
    
    def _determine_parts_needed(self, tire_predictions: Dict) -> List[str]:
        """Determine what parts are needed for maintenance"""
        
        parts = []
        
        for position, prediction in tire_predictions.items():
            if prediction['urgency'] in ['critical', 'high']:
                parts.append(f'tire_{position.lower()}')
        
        # Add common maintenance items
        if parts:
            parts.extend(['tire_valve_stems', 'wheel_weights'])
        
        return parts
    
    def _estimate_service_time(self, tire_predictions: Dict) -> float:
        """Estimate service time in hours"""
        
        tires_to_replace = sum(1 for pred in tire_predictions.values() 
                             if pred['urgency'] in ['critical', 'high'])
        
        if tires_to_replace == 0:
            return 0.5  # Inspection only
        elif tires_to_replace <= 2:
            return 1.0  # 1-2 tires
        else:
            return 1.5  # 3-4 tires
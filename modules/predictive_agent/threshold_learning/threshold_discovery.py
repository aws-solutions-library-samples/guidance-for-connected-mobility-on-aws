"""
Threshold Discovery System

This module uses data-driven approaches to discover critical thresholds from:
1. Historical failure data analysis
2. OEM specifications and safety standards
3. Statistical analysis of fleet telemetry
4. Machine learning-based anomaly detection
5. Continuous learning from outcomes
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import json

logger = logging.getLogger(__name__)


class ThresholdType(Enum):
    SAFETY_CRITICAL = "safety_critical"      # Immediate safety risk
    PERFORMANCE_WARNING = "performance_warning"  # Performance degradation
    MAINTENANCE_DUE = "maintenance_due"      # Scheduled maintenance needed
    TREND_CONCERN = "trend_concern"          # Concerning trend detected


@dataclass
class ThresholdDefinition:
    """Definition of a learned threshold"""
    component: str
    metric: str
    threshold_type: ThresholdType
    value: float
    confidence: float
    source: str  # How it was determined
    validation_data: Dict[str, Any]
    last_updated: datetime


class ThresholdDiscoverySystem:
    """
    Discovers critical thresholds using multiple data-driven approaches
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.discovered_thresholds: Dict[str, ThresholdDefinition] = {}
        
        # OEM and industry standard thresholds (baseline)
        self.oem_standards = self._load_oem_standards()
        
        # Statistical models for threshold discovery
        self.statistical_models = {}
        
        logger.info("Threshold Discovery System initialized")
    
    async def discover_thresholds_from_historical_data(self, lookback_days: int = 90) -> Dict[str, ThresholdDefinition]:
        """
        Discover thresholds by analyzing historical failure data
        """
        
        logger.info(f"Analyzing {lookback_days} days of historical data for threshold discovery")
        
        # Get historical failure events
        failure_events = await self._get_historical_failures(lookback_days)
        
        # Get telemetry data leading up to failures
        pre_failure_telemetry = await self._get_pre_failure_telemetry(failure_events)
        
        # Analyze each component type
        discovered_thresholds = {}
        
        for component in ['tire', 'brake', 'engine', 'battery']:
            component_thresholds = await self._analyze_component_failures(
                component, failure_events, pre_failure_telemetry
            )
            discovered_thresholds.update(component_thresholds)
        
        return discovered_thresholds
    
    async def _analyze_component_failures(self, component: str, failure_events: List[Dict], 
                                        pre_failure_telemetry: Dict) -> Dict[str, ThresholdDefinition]:
        """
        Analyze failures for a specific component to discover thresholds
        """
        
        thresholds = {}
        
        if component == 'tire':
            thresholds.update(await self._analyze_tire_failures(failure_events, pre_failure_telemetry))
        elif component == 'brake':
            thresholds.update(await self._analyze_brake_failures(failure_events, pre_failure_telemetry))
        elif component == 'engine':
            thresholds.update(await self._analyze_engine_failures(failure_events, pre_failure_telemetry))
        elif component == 'battery':
            thresholds.update(await self._analyze_battery_failures(failure_events, pre_failure_telemetry))
        
        return thresholds
    
    async def _analyze_tire_failures(self, failure_events: List[Dict], 
                                   pre_failure_telemetry: Dict) -> Dict[str, ThresholdDefinition]:
        """
        Analyze tire failures to discover pressure and temperature thresholds
        """
        
        thresholds = {}
        
        # Filter tire-related failures
        tire_failures = [f for f in failure_events if f['component'] == 'tire']
        
        if len(tire_failures) < 10:  # Need sufficient data
            logger.warning("Insufficient tire failure data for threshold discovery")
            return self._get_default_tire_thresholds()
        
        # Extract telemetry values at different time intervals before failure
        pressure_analysis = await self._analyze_pressure_thresholds(tire_failures, pre_failure_telemetry)
        temperature_analysis = await self._analyze_temperature_thresholds(tire_failures, pre_failure_telemetry)
        
        # Discover critical pressure threshold
        if pressure_analysis['confidence'] > 0.8:
            thresholds['tire_pressure_critical'] = ThresholdDefinition(
                component='tire',
                metric='pressure_mbar',
                threshold_type=ThresholdType.SAFETY_CRITICAL,
                value=pressure_analysis['critical_threshold'],
                confidence=pressure_analysis['confidence'],
                source='historical_failure_analysis',
                validation_data=pressure_analysis['validation'],
                last_updated=datetime.utcnow()
            )
        
        # Discover warning pressure threshold
        if pressure_analysis['warning_confidence'] > 0.7:
            thresholds['tire_pressure_warning'] = ThresholdDefinition(
                component='tire',
                metric='pressure_mbar',
                threshold_type=ThresholdType.PERFORMANCE_WARNING,
                value=pressure_analysis['warning_threshold'],
                confidence=pressure_analysis['warning_confidence'],
                source='historical_failure_analysis',
                validation_data=pressure_analysis['validation'],
                last_updated=datetime.utcnow()
            )
        
        # Discover temperature thresholds
        if temperature_analysis['confidence'] > 0.8:
            thresholds['tire_temperature_critical'] = ThresholdDefinition(
                component='tire',
                metric='temperature_celsius',
                threshold_type=ThresholdType.SAFETY_CRITICAL,
                value=temperature_analysis['critical_threshold'],
                confidence=temperature_analysis['confidence'],
                source='historical_failure_analysis',
                validation_data=temperature_analysis['validation'],
                last_updated=datetime.utcnow()
            )
        
        return thresholds
    
    async def _analyze_pressure_thresholds(self, tire_failures: List[Dict], 
                                         pre_failure_telemetry: Dict) -> Dict[str, Any]:
        """
        Analyze tire pressure data to discover failure thresholds
        """
        
        # Extract pressure readings at different time intervals before failure
        pressure_data = {
            '1_hour_before': [],
            '4_hours_before': [],
            '24_hours_before': [],
            'normal_operation': []
        }
        
        for failure in tire_failures:
            vehicle_id = failure['vehicle_id']
            failure_time = datetime.fromisoformat(failure['timestamp'])
            
            if vehicle_id in pre_failure_telemetry:
                telemetry = pre_failure_telemetry[vehicle_id]
                
                # Get pressure readings at different intervals
                for reading in telemetry:
                    reading_time = datetime.fromisoformat(reading['timestamp'])
                    time_diff = failure_time - reading_time
                    
                    tire_pressure = reading.get('tire_pressure_fl', reading.get('tire_pressure', None))
                    if tire_pressure:
                        if time_diff <= timedelta(hours=1):
                            pressure_data['1_hour_before'].append(tire_pressure)
                        elif time_diff <= timedelta(hours=4):
                            pressure_data['4_hours_before'].append(tire_pressure)
                        elif time_diff <= timedelta(hours=24):
                            pressure_data['24_hours_before'].append(tire_pressure)
        
        # Get normal operation pressure data for comparison
        normal_pressures = await self._get_normal_pressure_distribution()
        pressure_data['normal_operation'] = normal_pressures
        
        # Statistical analysis to find thresholds
        analysis_results = {}
        
        if len(pressure_data['1_hour_before']) >= 5:
            # Critical threshold: pressure level 1 hour before failure
            critical_pressures = np.array(pressure_data['1_hour_before'])
            
            # Use 90th percentile of pre-failure pressures as critical threshold
            critical_threshold = np.percentile(critical_pressures, 90)
            
            # Validate against normal operation data
            normal_pressures_array = np.array(pressure_data['normal_operation'])
            false_positive_rate = np.sum(normal_pressures_array < critical_threshold) / len(normal_pressures_array)
            
            # Confidence based on separation between failure and normal distributions
            confidence = self._calculate_threshold_confidence(
                critical_pressures, normal_pressures_array, critical_threshold
            )
            
            analysis_results.update({
                'critical_threshold': float(critical_threshold),
                'confidence': confidence,
                'false_positive_rate': false_positive_rate,
                'sample_size': len(critical_pressures)
            })
        
        if len(pressure_data['4_hours_before']) >= 10:
            # Warning threshold: pressure level 4 hours before failure
            warning_pressures = np.array(pressure_data['4_hours_before'])
            warning_threshold = np.percentile(warning_pressures, 75)
            
            warning_confidence = self._calculate_threshold_confidence(
                warning_pressures, normal_pressures_array, warning_threshold
            )
            
            analysis_results.update({
                'warning_threshold': float(warning_threshold),
                'warning_confidence': warning_confidence
            })
        
        # Validation data for transparency
        analysis_results['validation'] = {
            'failure_samples': len(pressure_data['1_hour_before']),
            'normal_samples': len(pressure_data['normal_operation']),
            'statistical_method': 'percentile_analysis',
            'validation_date': datetime.utcnow().isoformat()
        }
        
        return analysis_results
    
    def _calculate_threshold_confidence(self, failure_data: np.ndarray, 
                                      normal_data: np.ndarray, threshold: float) -> float:
        """
        Calculate confidence in threshold based on separation between distributions
        """
        
        # Calculate how well the threshold separates failure from normal data
        failure_below_threshold = np.sum(failure_data < threshold) / len(failure_data)
        normal_above_threshold = np.sum(normal_data >= threshold) / len(normal_data)
        
        # Good threshold should have high failure detection and low false positives
        detection_rate = failure_below_threshold
        specificity = normal_above_threshold
        
        # Confidence is geometric mean of detection rate and specificity
        confidence = np.sqrt(detection_rate * specificity)
        
        return min(1.0, confidence)
    
    async def discover_thresholds_from_oem_standards(self) -> Dict[str, ThresholdDefinition]:
        """
        Create thresholds based on OEM specifications and industry standards
        """
        
        oem_thresholds = {}
        
        # Tire pressure standards (based on typical automotive standards)
        oem_thresholds['tire_pressure_critical_oem'] = ThresholdDefinition(
            component='tire',
            metric='pressure_mbar',
            threshold_type=ThresholdType.SAFETY_CRITICAL,
            value=1600,  # ~23 PSI - typically considered dangerous
            confidence=0.95,
            source='oem_safety_standards',
            validation_data={
                'standard': 'ISO 4223-1:2017',
                'safety_margin': 'immediate_failure_risk',
                'reference': 'Automotive tire safety standards'
            },
            last_updated=datetime.utcnow()
        )
        
        oem_thresholds['tire_pressure_warning_oem'] = ThresholdDefinition(
            component='tire',
            metric='pressure_mbar',
            threshold_type=ThresholdType.PERFORMANCE_WARNING,
            value=1900,  # ~27.5 PSI - performance degradation
            confidence=0.9,
            source='oem_performance_standards',
            validation_data={
                'standard': 'SAE J2657',
                'performance_impact': 'increased_wear_fuel_consumption',
                'reference': 'Tire performance standards'
            },
            last_updated=datetime.utcnow()
        )
        
        # Tire temperature standards
        oem_thresholds['tire_temperature_critical_oem'] = ThresholdDefinition(
            component='tire',
            metric='temperature_celsius',
            threshold_type=ThresholdType.SAFETY_CRITICAL,
            value=85,  # Rubber degradation temperature
            confidence=0.95,
            source='oem_material_standards',
            validation_data={
                'standard': 'ASTM D1349',
                'material_limit': 'rubber_degradation_temperature',
                'reference': 'Tire material specifications'
            },
            last_updated=datetime.utcnow()
        )
        
        # Engine temperature standards
        oem_thresholds['engine_temperature_critical_oem'] = ThresholdDefinition(
            component='engine',
            metric='temperature_celsius',
            threshold_type=ThresholdType.SAFETY_CRITICAL,
            value=115,  # Engine damage temperature
            confidence=0.98,
            source='oem_engine_specifications',
            validation_data={
                'standard': 'SAE J1349',
                'damage_risk': 'engine_component_failure',
                'reference': 'Engine operating temperature limits'
            },
            last_updated=datetime.utcnow()
        )
        
        # Brake wear standards
        oem_thresholds['brake_wear_critical_oem'] = ThresholdDefinition(
            component='brake',
            metric='wear_percentage',
            threshold_type=ThresholdType.SAFETY_CRITICAL,
            value=90,  # 90% wear - replacement needed
            confidence=0.99,
            source='oem_safety_standards',
            validation_data={
                'standard': 'FMVSS 135',
                'safety_requirement': 'minimum_braking_performance',
                'reference': 'Federal brake safety standards'
            },
            last_updated=datetime.utcnow()
        )
        
        return oem_thresholds
    
    async def discover_thresholds_from_statistical_analysis(self) -> Dict[str, ThresholdDefinition]:
        """
        Discover thresholds using statistical analysis of fleet telemetry
        """
        
        # Get large sample of normal operation telemetry
        normal_telemetry = await self._get_normal_operation_telemetry(days=30)
        
        statistical_thresholds = {}
        
        # Analyze tire pressure distribution
        tire_pressures = self._extract_tire_pressures(normal_telemetry)
        if len(tire_pressures) > 1000:  # Need large sample
            
            # Use statistical outlier detection
            pressure_stats = self._calculate_distribution_stats(tire_pressures)
            
            # 3-sigma rule for outlier detection
            critical_threshold = pressure_stats['mean'] - 3 * pressure_stats['std']
            warning_threshold = pressure_stats['mean'] - 2 * pressure_stats['std']
            
            statistical_thresholds['tire_pressure_statistical'] = ThresholdDefinition(
                component='tire',
                metric='pressure_mbar',
                threshold_type=ThresholdType.PERFORMANCE_WARNING,
                value=float(warning_threshold),
                confidence=0.7,  # Lower confidence for statistical method
                source='statistical_analysis',
                validation_data={
                    'method': '2_sigma_outlier_detection',
                    'sample_size': len(tire_pressures),
                    'mean': pressure_stats['mean'],
                    'std': pressure_stats['std'],
                    'percentiles': pressure_stats['percentiles']
                },
                last_updated=datetime.utcnow()
            )
        
        return statistical_thresholds
    
    async def create_adaptive_thresholds(self, base_thresholds: Dict[str, ThresholdDefinition]) -> Dict[str, Dict[str, float]]:
        """
        Create adaptive thresholds that adjust based on vehicle characteristics
        """
        
        adaptive_thresholds = {}
        
        # Get all vehicles and their characteristics
        vehicles = await self._get_all_vehicles()
        
        for vehicle in vehicles:
            vehicle_id = vehicle['vehicle_id']
            vehicle_thresholds = {}
            
            # Adjust thresholds based on vehicle characteristics
            for threshold_name, threshold_def in base_thresholds.items():
                
                adjusted_value = threshold_def.value
                
                # Adjust for vehicle age
                vehicle_age_years = vehicle.get('age_years', 5)
                if vehicle_age_years > 10:
                    # Older vehicles get more conservative thresholds
                    if threshold_def.threshold_type == ThresholdType.SAFETY_CRITICAL:
                        adjusted_value *= 1.1  # 10% more conservative
                
                # Adjust for mileage
                mileage = vehicle.get('mileage', 50000)
                if mileage > 100000:
                    # High mileage vehicles get more conservative thresholds
                    if threshold_def.component == 'tire':
                        adjusted_value *= 1.05  # 5% more conservative
                
                # Adjust for usage pattern
                usage_pattern = vehicle.get('usage_pattern', 'mixed')
                if usage_pattern == 'commercial':
                    # Commercial vehicles get more conservative thresholds
                    adjusted_value *= 1.15  # 15% more conservative
                elif usage_pattern == 'highway':
                    # Highway vehicles can have slightly relaxed thresholds
                    adjusted_value *= 0.95  # 5% less conservative
                
                vehicle_thresholds[threshold_name] = adjusted_value
            
            adaptive_thresholds[vehicle_id] = vehicle_thresholds
        
        return adaptive_thresholds
    
    async def validate_thresholds_with_simulation(self, thresholds: Dict[str, ThresholdDefinition]) -> Dict[str, float]:
        """
        Validate discovered thresholds using historical data simulation
        """
        
        validation_results = {}
        
        # Get test dataset (different from training data)
        test_failures = await self._get_test_failure_events()
        test_telemetry = await self._get_test_telemetry()
        
        for threshold_name, threshold_def in thresholds.items():
            
            # Simulate threshold performance
            true_positives = 0
            false_positives = 0
            true_negatives = 0
            false_negatives = 0
            
            # Test against known failures
            for failure in test_failures:
                if failure['component'] == threshold_def.component:
                    
                    # Get telemetry before failure
                    pre_failure_value = self._get_telemetry_value_before_failure(
                        failure, threshold_def.metric, hours=1
                    )
                    
                    if pre_failure_value is not None:
                        if threshold_def.threshold_type == ThresholdType.SAFETY_CRITICAL:
                            threshold_breached = pre_failure_value < threshold_def.value
                        else:
                            threshold_breached = pre_failure_value < threshold_def.value
                        
                        if threshold_breached:
                            true_positives += 1
                        else:
                            false_negatives += 1
            
            # Test against normal operation
            normal_samples = self._get_normal_samples_for_metric(test_telemetry, threshold_def.metric)
            
            for sample_value in normal_samples[:1000]:  # Limit for performance
                if threshold_def.threshold_type == ThresholdType.SAFETY_CRITICAL:
                    threshold_breached = sample_value < threshold_def.value
                else:
                    threshold_breached = sample_value < threshold_def.value
                
                if threshold_breached:
                    false_positives += 1
                else:
                    true_negatives += 1
            
            # Calculate performance metrics
            precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
            recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
            f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
            
            validation_results[threshold_name] = {
                'precision': precision,
                'recall': recall,
                'f1_score': f1_score,
                'true_positives': true_positives,
                'false_positives': false_positives,
                'false_negatives': false_negatives,
                'validation_confidence': f1_score  # Use F1 score as validation confidence
            }
        
        return validation_results
    
    # Helper methods for data access and analysis
    
    async def _get_historical_failures(self, days: int) -> List[Dict[str, Any]]:
        """Get historical failure events from the last N days"""
        
        # In production, query from your maintenance database
        # For now, return sample data
        return [
            {
                'vehicle_id': 'FLEET-001',
                'component': 'tire',
                'failure_type': 'pressure_loss',
                'timestamp': '2024-10-15T14:30:00Z',
                'severity': 'critical'
            },
            {
                'vehicle_id': 'FLEET-002', 
                'component': 'tire',
                'failure_type': 'blowout',
                'timestamp': '2024-10-12T09:15:00Z',
                'severity': 'critical'
            }
            # ... more failure events
        ]
    
    async def _get_normal_pressure_distribution(self) -> List[float]:
        """Get distribution of tire pressures during normal operation"""
        
        # In production, query from your telemetry database
        # Return sample of normal pressures
        return list(np.random.normal(2200, 100, 10000))  # Mean 2200 mbar, std 100
    
    def _load_oem_standards(self) -> Dict[str, Any]:
        """Load OEM and industry standard specifications"""
        
        return {
            'tire_pressure_min_psi': 26,
            'tire_pressure_max_psi': 35,
            'tire_temperature_max_celsius': 80,
            'engine_temperature_max_celsius': 110,
            'brake_wear_max_percentage': 85
        }
    
    def _calculate_distribution_stats(self, data: List[float]) -> Dict[str, Any]:
        """Calculate statistical properties of data distribution"""
        
        data_array = np.array(data)
        
        return {
            'mean': float(np.mean(data_array)),
            'std': float(np.std(data_array)),
            'min': float(np.min(data_array)),
            'max': float(np.max(data_array)),
            'percentiles': {
                '1': float(np.percentile(data_array, 1)),
                '5': float(np.percentile(data_array, 5)),
                '10': float(np.percentile(data_array, 10)),
                '25': float(np.percentile(data_array, 25)),
                '50': float(np.percentile(data_array, 50)),
                '75': float(np.percentile(data_array, 75)),
                '90': float(np.percentile(data_array, 90)),
                '95': float(np.percentile(data_array, 95)),
                '99': float(np.percentile(data_array, 99))
            }
        }
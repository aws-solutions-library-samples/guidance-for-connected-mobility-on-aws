"""
Risk Assessor

Evaluates safety and operational risks associated with vehicle components:
- Safety criticality assessment
- Environmental risk factors
- Operational impact analysis
- Risk mitigation recommendations
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    MINIMAL = "minimal"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskFactor:
    """Individual risk factor"""
    factor_type: str
    severity: RiskLevel
    impact_score: float
    description: str
    mitigation: str


@dataclass
class RiskAssessment:
    """Complete risk assessment"""
    overall_risk: RiskLevel
    safety_criticality: float
    operational_impact: float
    environmental_factors: List[RiskFactor]
    risk_factors: List[RiskFactor]
    mitigation_recommendations: List[str]


class RiskAssessor:
    """
    Assesses risks associated with vehicle component failures
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Component safety criticality ratings
        self.safety_criticality = {
            'tire': 0.9,      # Very high - affects vehicle control
            'brake': 0.95,    # Critical - directly affects stopping
            'engine': 0.7,    # High - affects mobility
            'battery': 0.6,   # Moderate-High - affects operation
            'transmission': 0.8,  # High - affects mobility
            'suspension': 0.7     # High - affects handling
        }
        
        # Environmental risk factors
        self.environmental_risks = {
            'weather': {
                'rain': 1.3,
                'snow': 1.5,
                'ice': 1.8,
                'fog': 1.2,
                'clear': 1.0
            },
            'road_conditions': {
                'poor': 1.4,
                'construction': 1.3,
                'good': 1.0,
                'excellent': 0.9
            },
            'traffic': {
                'heavy': 1.2,
                'moderate': 1.1,
                'light': 1.0
            }
        }
        
        # Usage pattern risk multipliers
        self.usage_risk_multipliers = {
            'commercial': 1.4,    # Higher risk due to heavy use
            'emergency': 1.6,     # Highest risk - critical operations
            'personal': 1.0,      # Baseline risk
            'mixed': 1.2          # Moderate increase
        }
        
        logger.info("Risk Assessor initialized")
    
    async def assess_component_risk(
        self, 
        vehicle_context: Any, 
        component_type: Any, 
        prediction: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Assess risk for a specific component
        """
        
        try:
            component_str = component_type.value if hasattr(component_type, 'value') else str(component_type)
            
            # Get base safety criticality
            base_criticality = self.safety_criticality.get(component_str, 0.5)
            
            # Assess environmental risk factors
            environmental_factors = self._assess_environmental_risks(vehicle_context)
            
            # Assess operational risk factors
            operational_factors = self._assess_operational_risks(vehicle_context, prediction)
            
            # Assess component-specific risks
            component_factors = self._assess_component_specific_risks(component_type, prediction)
            
            # Combine all risk factors
            all_factors = environmental_factors + operational_factors + component_factors
            
            # Calculate overall risk scores
            safety_criticality = self._calculate_safety_criticality(base_criticality, all_factors)
            operational_impact = self._calculate_operational_impact(vehicle_context, all_factors)
            overall_risk = self._determine_overall_risk(safety_criticality, operational_impact)
            
            # Generate mitigation recommendations
            mitigation_recommendations = self._generate_mitigation_recommendations(
                component_type, all_factors, overall_risk
            )
            
            return {
                'overall_risk': overall_risk.value,
                'safety_criticality': safety_criticality,
                'operational_impact': operational_impact,
                'risk_factors': [self._factor_to_dict(f) for f in all_factors],
                'mitigation_recommendations': mitigation_recommendations,
                'assessment_timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error in risk assessment: {str(e)}")
            return {
                'overall_risk': 'moderate',
                'safety_criticality': 0.5,
                'operational_impact': 0.5,
                'error': str(e)
            }
    
    def _assess_environmental_risks(self, vehicle_context: Any) -> List[RiskFactor]:
        """Assess environmental risk factors"""
        
        factors = []
        
        # Get environmental conditions
        env_conditions = getattr(vehicle_context, 'environmental_conditions', {})
        
        # Weather conditions
        weather = env_conditions.get('weather', 'clear')
        weather_multiplier = self.environmental_risks['weather'].get(weather, 1.0)
        
        if weather_multiplier > 1.2:
            factors.append(RiskFactor(
                factor_type='weather',
                severity=RiskLevel.HIGH if weather_multiplier > 1.5 else RiskLevel.MODERATE,
                impact_score=weather_multiplier,
                description=f'Adverse weather conditions: {weather}',
                mitigation='Increase monitoring frequency and consider postponing non-critical maintenance'
            ))
        
        # Road conditions
        road_conditions = env_conditions.get('road_conditions', 'good')
        road_multiplier = self.environmental_risks['road_conditions'].get(road_conditions, 1.0)
        
        if road_multiplier > 1.1:
            factors.append(RiskFactor(
                factor_type='road_conditions',
                severity=RiskLevel.MODERATE if road_multiplier > 1.3 else RiskLevel.LOW,
                impact_score=road_multiplier,
                description=f'Poor road conditions: {road_conditions}',
                mitigation='Prioritize suspension and tire maintenance'
            ))
        
        # Temperature extremes
        temperature = env_conditions.get('temperature', 20)
        if temperature < -10 or temperature > 40:
            severity = RiskLevel.HIGH if abs(temperature - 20) > 30 else RiskLevel.MODERATE
            factors.append(RiskFactor(
                factor_type='temperature',
                severity=severity,
                impact_score=1.2 if abs(temperature - 20) > 20 else 1.1,
                description=f'Extreme temperature: {temperature}°C',
                mitigation='Monitor battery and tire pressure more frequently'
            ))
        
        return factors
    
    def _assess_operational_risks(self, vehicle_context: Any, prediction: Dict) -> List[RiskFactor]:
        """Assess operational risk factors"""
        
        factors = []
        
        # Usage pattern risk
        usage_pattern = getattr(vehicle_context, 'usage_pattern', 'personal')
        usage_multiplier = self.usage_risk_multipliers.get(usage_pattern, 1.0)
        
        if usage_multiplier > 1.2:
            factors.append(RiskFactor(
                factor_type='usage_pattern',
                severity=RiskLevel.HIGH if usage_multiplier > 1.5 else RiskLevel.MODERATE,
                impact_score=usage_multiplier,
                description=f'High-risk usage pattern: {usage_pattern}',
                mitigation='Implement more frequent inspections and proactive maintenance'
            ))
        
        # Driver behavior risk
        driver_score = getattr(vehicle_context, 'driver_behavior_score', 0.5)
        if driver_score < 0.4:
            factors.append(RiskFactor(
                factor_type='driver_behavior',
                severity=RiskLevel.HIGH,
                impact_score=1.5,
                description=f'Aggressive driving behavior (score: {driver_score:.2f})',
                mitigation='Provide driver training and increase component monitoring'
            ))
        elif driver_score < 0.6:
            factors.append(RiskFactor(
                factor_type='driver_behavior',
                severity=RiskLevel.MODERATE,
                impact_score=1.2,
                description=f'Suboptimal driving behavior (score: {driver_score:.2f})',
                mitigation='Monitor wear patterns more closely'
            ))
        
        # Vehicle age and mileage
        current_mileage = getattr(vehicle_context, 'current_mileage', 0)
        if current_mileage > 100000:
            severity = RiskLevel.HIGH if current_mileage > 200000 else RiskLevel.MODERATE
            factors.append(RiskFactor(
                factor_type='vehicle_age',
                severity=severity,
                impact_score=1.3 if current_mileage > 200000 else 1.2,
                description=f'High mileage vehicle: {current_mileage:,} miles',
                mitigation='Increase maintenance frequency and component inspections'
            ))
        
        # Service history gaps
        last_service = getattr(vehicle_context, 'last_service_date', None)
        if last_service:
            days_since_service = (datetime.utcnow() - last_service).days
            if days_since_service > 180:  # 6 months
                factors.append(RiskFactor(
                    factor_type='service_gap',
                    severity=RiskLevel.MODERATE,
                    impact_score=1.3,
                    description=f'Extended service gap: {days_since_service} days',
                    mitigation='Schedule comprehensive inspection immediately'
                ))
        
        return factors
    
    def _assess_component_specific_risks(self, component_type: Any, prediction: Dict) -> List[RiskFactor]:
        """Assess component-specific risk factors"""
        
        factors = []
        component_str = component_type.value if hasattr(component_type, 'value') else str(component_type)
        
        # High failure probability
        failure_prob = prediction.get('failure_probability', 0.0)
        if failure_prob > 0.8:
            factors.append(RiskFactor(
                factor_type='failure_probability',
                severity=RiskLevel.CRITICAL,
                impact_score=2.0,
                description=f'Very high failure probability: {failure_prob:.1%}',
                mitigation='Schedule immediate maintenance or replacement'
            ))
        elif failure_prob > 0.6:
            factors.append(RiskFactor(
                factor_type='failure_probability',
                severity=RiskLevel.HIGH,
                impact_score=1.5,
                description=f'High failure probability: {failure_prob:.1%}',
                mitigation='Schedule maintenance within 1-3 days'
            ))
        
        # Component-specific risks
        if component_str == 'tire':
            self._assess_tire_specific_risks(factors, prediction)
        elif component_str == 'brake':
            self._assess_brake_specific_risks(factors, prediction)
        elif component_str == 'engine':
            self._assess_engine_specific_risks(factors, prediction)
        elif component_str == 'battery':
            self._assess_battery_specific_risks(factors, prediction)
        
        return factors
    
    def _assess_tire_specific_risks(self, factors: List[RiskFactor], prediction: Dict):
        """Assess tire-specific risks"""
        
        failure_mode = prediction.get('failure_mode', '')
        
        if 'pressure' in failure_mode:
            if 'critical' in failure_mode:
                factors.append(RiskFactor(
                    factor_type='tire_pressure',
                    severity=RiskLevel.CRITICAL,
                    impact_score=2.0,
                    description='Critical tire pressure issue - immediate blowout risk',
                    mitigation='Stop driving immediately and replace tire'
                ))
            else:
                factors.append(RiskFactor(
                    factor_type='tire_pressure',
                    severity=RiskLevel.HIGH,
                    impact_score=1.5,
                    description='Tire pressure issue - increased wear and handling problems',
                    mitigation='Check and adjust tire pressure immediately'
                ))
        
        if 'tread' in failure_mode:
            factors.append(RiskFactor(
                factor_type='tire_tread',
                severity=RiskLevel.HIGH,
                impact_score=1.8,
                description='Low tread depth - reduced traction and stopping distance',
                mitigation='Replace tire before wet weather driving'
            ))
    
    def _assess_brake_specific_risks(self, factors: List[RiskFactor], prediction: Dict):
        """Assess brake-specific risks"""
        
        failure_mode = prediction.get('failure_mode', '')
        
        if 'pad' in failure_mode:
            factors.append(RiskFactor(
                factor_type='brake_pads',
                severity=RiskLevel.HIGH,
                impact_score=1.8,
                description='Brake pad wear - reduced stopping power',
                mitigation='Replace brake pads immediately'
            ))
        
        if 'fluid' in failure_mode:
            factors.append(RiskFactor(
                factor_type='brake_fluid',
                severity=RiskLevel.CRITICAL,
                impact_score=2.0,
                description='Brake fluid issue - potential brake failure',
                mitigation='Inspect brake system immediately and do not drive until resolved'
            ))
    
    def _assess_engine_specific_risks(self, factors: List[RiskFactor], prediction: Dict):
        """Assess engine-specific risks"""
        
        failure_mode = prediction.get('failure_mode', '')
        
        if 'oil' in failure_mode:
            factors.append(RiskFactor(
                factor_type='engine_oil',
                severity=RiskLevel.HIGH,
                impact_score=1.6,
                description='Engine oil degradation - potential engine damage',
                mitigation='Change oil immediately and check for leaks'
            ))
        
        if 'coolant' in failure_mode:
            factors.append(RiskFactor(
                factor_type='engine_coolant',
                severity=RiskLevel.HIGH,
                impact_score=1.7,
                description='Coolant system issue - overheating risk',
                mitigation='Check coolant level and inspect for leaks'
            ))
    
    def _assess_battery_specific_risks(self, factors: List[RiskFactor], prediction: Dict):
        """Assess battery-specific risks"""
        
        failure_mode = prediction.get('failure_mode', '')
        
        if 'capacity' in failure_mode:
            factors.append(RiskFactor(
                factor_type='battery_capacity',
                severity=RiskLevel.MODERATE,
                impact_score=1.3,
                description='Battery capacity degradation - reduced range',
                mitigation='Plan for shorter trips and consider replacement'
            ))
        
        if 'charging' in failure_mode:
            factors.append(RiskFactor(
                factor_type='battery_charging',
                severity=RiskLevel.HIGH,
                impact_score=1.6,
                description='Battery charging issue - potential stranding',
                mitigation='Inspect charging system and avoid long trips'
            ))
    
    def _calculate_safety_criticality(self, base_criticality: float, factors: List[RiskFactor]) -> float:
        """Calculate overall safety criticality score"""
        
        # Start with base criticality
        criticality = base_criticality
        
        # Apply risk factor multipliers
        for factor in factors:
            if factor.factor_type in ['weather', 'driver_behavior', 'failure_probability']:
                criticality *= factor.impact_score
        
        # Cap at 1.0
        return min(criticality, 1.0)
    
    def _calculate_operational_impact(self, vehicle_context: Any, factors: List[RiskFactor]) -> float:
        """Calculate operational impact score"""
        
        # Base impact depends on usage pattern
        usage_pattern = getattr(vehicle_context, 'usage_pattern', 'personal')
        base_impact = {
            'emergency': 0.9,
            'commercial': 0.8,
            'mixed': 0.6,
            'personal': 0.4
        }.get(usage_pattern, 0.5)
        
        # Apply operational risk factors
        for factor in factors:
            if factor.factor_type in ['usage_pattern', 'service_gap', 'vehicle_age']:
                base_impact *= factor.impact_score
        
        return min(base_impact, 1.0)
    
    def _determine_overall_risk(self, safety_criticality: float, operational_impact: float) -> RiskLevel:
        """Determine overall risk level"""
        
        # Weighted combination (safety weighted higher)
        overall_score = (safety_criticality * 0.7) + (operational_impact * 0.3)
        
        if overall_score >= 0.8:
            return RiskLevel.CRITICAL
        elif overall_score >= 0.6:
            return RiskLevel.HIGH
        elif overall_score >= 0.4:
            return RiskLevel.MODERATE
        elif overall_score >= 0.2:
            return RiskLevel.LOW
        else:
            return RiskLevel.MINIMAL
    
    def _generate_mitigation_recommendations(
        self, 
        component_type: Any, 
        factors: List[RiskFactor], 
        overall_risk: RiskLevel
    ) -> List[str]:
        """Generate risk mitigation recommendations"""
        
        recommendations = []
        
        # Add factor-specific mitigations
        for factor in factors:
            if factor.mitigation not in recommendations:
                recommendations.append(factor.mitigation)
        
        # Add overall risk-based recommendations
        if overall_risk == RiskLevel.CRITICAL:
            recommendations.append("Immediate action required - do not operate vehicle until resolved")
        elif overall_risk == RiskLevel.HIGH:
            recommendations.append("Schedule maintenance within 24-48 hours")
            recommendations.append("Avoid high-stress driving conditions")
        elif overall_risk == RiskLevel.MODERATE:
            recommendations.append("Schedule maintenance within 1-2 weeks")
            recommendations.append("Monitor component closely")
        
        return recommendations[:5]  # Limit to top 5 recommendations
    
    def _factor_to_dict(self, factor: RiskFactor) -> Dict[str, Any]:
        """Convert RiskFactor to dictionary"""
        
        return {
            'factor_type': factor.factor_type,
            'severity': factor.severity.value,
            'impact_score': factor.impact_score,
            'description': factor.description,
            'mitigation': factor.mitigation
        }
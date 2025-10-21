"""
Decision Engine Core

Central decision-making system that evaluates ML predictions and makes
autonomous maintenance decisions based on multiple factors.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from ..agent.core import MaintenanceDecision, MaintenanceUrgency, ComponentType, VehicleContext
from .scheduler import MaintenanceScheduler
from .cost_optimizer import CostOptimizer
from .risk_assessor import RiskAssessor

logger = logging.getLogger(__name__)


@dataclass
class DecisionCriteria:
    """Criteria used for making maintenance decisions"""
    failure_probability_threshold: float = 0.7
    cost_benefit_ratio_threshold: float = 2.0
    safety_criticality_multiplier: float = 1.5
    fleet_coordination_weight: float = 0.3
    environmental_factor_weight: float = 0.2


class DecisionEngine:
    """
    Core decision engine that converts ML predictions into actionable maintenance decisions
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.criteria = DecisionCriteria(**config.get('criteria', {}))
        
        # Initialize sub-components
        self.scheduler = MaintenanceScheduler(config.get('scheduler', {}))
        self.cost_optimizer = CostOptimizer(config.get('cost_optimizer', {}))
        self.risk_assessor = RiskAssessor(config.get('risk_assessor', {}))
        
        # Decision history for learning
        self.decision_history: List[MaintenanceDecision] = []
        
        logger.info("Decision Engine initialized")
    
    async def evaluate_maintenance_need(
        self, 
        context: VehicleContext, 
        component_type: ComponentType, 
        prediction: Dict[str, Any]
    ) -> Optional[MaintenanceDecision]:
        """
        Evaluate whether maintenance is needed based on prediction and context
        """
        
        try:
            # Extract prediction metrics
            failure_probability = prediction.get('failure_probability', 0.0)
            predicted_failure_date = prediction.get('predicted_failure_date')
            confidence_score = prediction.get('confidence', 0.0)
            
            # Skip if prediction confidence is too low
            if confidence_score < self.config.get('min_confidence', 0.6):
                logger.debug(f"Skipping {component_type.value} for {context.vehicle_id} - low confidence")
                return None
            
            # Assess risk level
            risk_assessment = await self.risk_assessor.assess_component_risk(
                context, component_type, prediction
            )
            
            # Determine urgency based on multiple factors
            urgency = self._determine_urgency(
                failure_probability,
                predicted_failure_date,
                risk_assessment,
                context
            )
            
            # Skip if urgency is too low
            if urgency == MaintenanceUrgency.MONITOR and failure_probability < self.criteria.failure_probability_threshold:
                return None
            
            # Calculate cost-benefit analysis
            cost_analysis = await self.cost_optimizer.analyze_maintenance_cost(
                context, component_type, prediction, urgency
            )
            
            # Make final decision
            if self._should_schedule_maintenance(cost_analysis, risk_assessment, urgency):
                
                # Generate maintenance decision
                decision = MaintenanceDecision(
                    vehicle_id=context.vehicle_id,
                    component_type=component_type,
                    urgency=urgency,
                    predicted_failure_date=predicted_failure_date,
                    confidence_score=confidence_score,
                    recommended_action=self._generate_recommended_action(component_type, prediction),
                    cost_estimate=cost_analysis.get('total_cost'),
                    parts_needed=prediction.get('parts_needed', []),
                    service_time_hours=prediction.get('service_time_hours', 2.0),
                    reasoning=self._generate_reasoning(prediction, risk_assessment, cost_analysis),
                    created_at=datetime.utcnow()
                )
                
                # Store decision for learning
                self.decision_history.append(decision)
                
                logger.info(f"Generated {urgency.value} maintenance decision for {context.vehicle_id} {component_type.value}")
                return decision
            
            return None
            
        except Exception as e:
            logger.error(f"Error evaluating maintenance need: {str(e)}")
            return None
    
    def _determine_urgency(
        self, 
        failure_probability: float, 
        predicted_failure_date: Optional[datetime],
        risk_assessment: Dict[str, Any],
        context: VehicleContext
    ) -> MaintenanceUrgency:
        """Determine maintenance urgency based on multiple factors"""
        
        # Base urgency on failure probability
        if failure_probability >= 0.9:
            base_urgency = MaintenanceUrgency.CRITICAL
        elif failure_probability >= 0.7:
            base_urgency = MaintenanceUrgency.HIGH
        elif failure_probability >= 0.5:
            base_urgency = MaintenanceUrgency.MEDIUM
        elif failure_probability >= 0.3:
            base_urgency = MaintenanceUrgency.LOW
        else:
            base_urgency = MaintenanceUrgency.MONITOR
        
        # Adjust based on time to failure
        if predicted_failure_date:
            days_to_failure = (predicted_failure_date - datetime.utcnow()).days
            
            if days_to_failure <= 1:
                base_urgency = MaintenanceUrgency.CRITICAL
            elif days_to_failure <= 7 and base_urgency.value in ['high', 'medium']:
                base_urgency = MaintenanceUrgency.HIGH
        
        # Adjust based on safety criticality
        safety_score = risk_assessment.get('safety_criticality', 0.5)
        if safety_score >= 0.8 and base_urgency != MaintenanceUrgency.MONITOR:
            # Escalate urgency for safety-critical components
            urgency_levels = [MaintenanceUrgency.MONITOR, MaintenanceUrgency.LOW, 
                            MaintenanceUrgency.MEDIUM, MaintenanceUrgency.HIGH, 
                            MaintenanceUrgency.CRITICAL]
            current_index = urgency_levels.index(base_urgency)
            if current_index < len(urgency_levels) - 1:
                base_urgency = urgency_levels[current_index + 1]
        
        # Adjust based on vehicle usage pattern
        if context.usage_pattern == 'commercial' and base_urgency != MaintenanceUrgency.MONITOR:
            # Commercial vehicles need more proactive maintenance
            urgency_levels = [MaintenanceUrgency.MONITOR, MaintenanceUrgency.LOW, 
                            MaintenanceUrgency.MEDIUM, MaintenanceUrgency.HIGH, 
                            MaintenanceUrgency.CRITICAL]
            current_index = urgency_levels.index(base_urgency)
            if current_index < len(urgency_levels) - 1:
                base_urgency = urgency_levels[current_index + 1]
        
        return base_urgency
    
    def _should_schedule_maintenance(
        self, 
        cost_analysis: Dict[str, Any], 
        risk_assessment: Dict[str, Any], 
        urgency: MaintenanceUrgency
    ) -> bool:
        """Determine if maintenance should be scheduled"""
        
        # Always schedule critical maintenance
        if urgency == MaintenanceUrgency.CRITICAL:
            return True
        
        # Check cost-benefit ratio
        cost_benefit_ratio = cost_analysis.get('cost_benefit_ratio', 0.0)
        if cost_benefit_ratio < self.criteria.cost_benefit_ratio_threshold:
            return False
        
        # Check if maintenance provides sufficient value
        expected_savings = cost_analysis.get('expected_savings', 0.0)
        maintenance_cost = cost_analysis.get('total_cost', 0.0)
        
        if expected_savings > maintenance_cost * 1.2:  # 20% margin
            return True
        
        # Consider safety factors
        safety_score = risk_assessment.get('safety_criticality', 0.0)
        if safety_score >= 0.7 and urgency in [MaintenanceUrgency.HIGH, MaintenanceUrgency.MEDIUM]:
            return True
        
        return False
    
    def _generate_recommended_action(self, component_type: ComponentType, prediction: Dict[str, Any]) -> str:
        """Generate human-readable recommended action"""
        
        failure_mode = prediction.get('failure_mode', 'general_wear')
        
        actions = {
            ComponentType.TIRE: {
                'pressure_loss': 'Check tire pressure and inspect for leaks',
                'tread_wear': 'Replace tire due to excessive tread wear',
                'temperature_high': 'Inspect tire for damage and check alignment',
                'general_wear': 'Inspect tire condition and consider replacement'
            },
            ComponentType.BRAKE: {
                'pad_wear': 'Replace brake pads',
                'fluid_low': 'Check brake fluid level and inspect for leaks',
                'rotor_wear': 'Inspect brake rotors and replace if necessary',
                'general_wear': 'Perform brake system inspection'
            },
            ComponentType.ENGINE: {
                'oil_degradation': 'Change engine oil and filter',
                'coolant_issue': 'Inspect cooling system and replace coolant',
                'performance_drop': 'Perform engine diagnostic and tune-up',
                'general_wear': 'Perform comprehensive engine inspection'
            },
            ComponentType.BATTERY: {
                'capacity_loss': 'Test battery capacity and consider replacement',
                'charging_issue': 'Inspect charging system and connections',
                'temperature_issue': 'Check battery thermal management system',
                'general_wear': 'Perform battery health assessment'
            }
        }
        
        component_actions = actions.get(component_type, {})
        return component_actions.get(failure_mode, f'Inspect {component_type.value} system')
    
    def _generate_reasoning(
        self, 
        prediction: Dict[str, Any], 
        risk_assessment: Dict[str, Any], 
        cost_analysis: Dict[str, Any]
    ) -> str:
        """Generate explanation for the maintenance decision"""
        
        failure_prob = prediction.get('failure_probability', 0.0)
        safety_score = risk_assessment.get('safety_criticality', 0.0)
        cost_benefit = cost_analysis.get('cost_benefit_ratio', 0.0)
        
        reasoning_parts = []
        
        # Failure probability reasoning
        if failure_prob >= 0.8:
            reasoning_parts.append(f"High failure probability ({failure_prob:.1%})")
        elif failure_prob >= 0.5:
            reasoning_parts.append(f"Moderate failure probability ({failure_prob:.1%})")
        
        # Safety reasoning
        if safety_score >= 0.7:
            reasoning_parts.append("Safety-critical component")
        
        # Cost reasoning
        if cost_benefit >= 3.0:
            reasoning_parts.append("High cost-benefit ratio")
        elif cost_benefit >= 2.0:
            reasoning_parts.append("Positive cost-benefit ratio")
        
        # Time reasoning
        predicted_date = prediction.get('predicted_failure_date')
        if predicted_date:
            days_to_failure = (predicted_date - datetime.utcnow()).days
            if days_to_failure <= 7:
                reasoning_parts.append(f"Failure expected within {days_to_failure} days")
        
        return "; ".join(reasoning_parts) if reasoning_parts else "Preventive maintenance recommended"
    
    async def optimize_fleet_schedule(self, fleet_decisions: Dict[str, List[MaintenanceDecision]]):
        """Optimize maintenance scheduling across the fleet"""
        
        # Use scheduler to optimize timing
        optimized_schedule = await self.scheduler.optimize_fleet_schedule(fleet_decisions)
        
        # Apply cost optimizations
        await self.cost_optimizer.optimize_fleet_costs(optimized_schedule)
        
        logger.info(f"Optimized maintenance schedule for {len(fleet_decisions)} vehicles")
        
        return optimized_schedule
    
    def get_decision_metrics(self) -> Dict[str, Any]:
        """Get metrics about decision-making performance"""
        
        if not self.decision_history:
            return {'total_decisions': 0}
        
        total_decisions = len(self.decision_history)
        
        # Count by urgency
        urgency_counts = {}
        for decision in self.decision_history:
            urgency = decision.urgency.value
            urgency_counts[urgency] = urgency_counts.get(urgency, 0) + 1
        
        # Count by component
        component_counts = {}
        for decision in self.decision_history:
            component = decision.component_type.value
            component_counts[component] = component_counts.get(component, 0) + 1
        
        # Calculate average confidence
        avg_confidence = sum(d.confidence_score for d in self.decision_history) / total_decisions
        
        return {
            'total_decisions': total_decisions,
            'urgency_distribution': urgency_counts,
            'component_distribution': component_counts,
            'average_confidence': round(avg_confidence, 3),
            'decisions_last_24h': len([d for d in self.decision_history 
                                     if (datetime.utcnow() - d.created_at).days < 1])
        }
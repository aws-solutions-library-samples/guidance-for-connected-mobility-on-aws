"""
Agentic Behaviors for Predictive Maintenance

This module implements truly agentic behaviors:
- Goal-oriented planning
- Proactive strategy development  
- Learning from outcomes
- Multi-agent coordination
- Causal reasoning
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AgentGoal(Enum):
    MINIMIZE_DOWNTIME = "minimize_downtime"
    MINIMIZE_COST = "minimize_cost"
    MAXIMIZE_SAFETY = "maximize_safety"
    OPTIMIZE_RESOURCES = "optimize_resources"
    IMPROVE_EFFICIENCY = "improve_efficiency"


@dataclass
class MaintenanceStrategy:
    """Strategic maintenance plan developed by the agent"""
    strategy_id: str
    target_vehicles: List[str]
    planned_actions: List[Dict[str, Any]]
    expected_outcomes: Dict[str, float]
    timeline: Dict[str, datetime]
    resource_requirements: Dict[str, Any]
    success_metrics: Dict[str, float]
    created_at: datetime
    status: str = "planned"


@dataclass
class AgentDecision:
    """Enhanced decision with agentic reasoning"""
    decision_id: str
    vehicle_id: str
    reasoning_chain: List[str]
    causal_factors: List[str]
    alternative_options: List[Dict[str, Any]]
    selected_option: Dict[str, Any]
    expected_outcome: Dict[str, float]
    confidence: float
    goals_impact: Dict[AgentGoal, float]
    created_at: datetime


class AgentMemory:
    """Agent's memory system for learning and adaptation"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.decision_outcomes: List[Dict[str, Any]] = []
        self.learned_patterns: Dict[str, Any] = {}
        self.causal_models: Dict[str, Any] = {}
        self.success_patterns: List[Dict[str, Any]] = []
        self.failure_patterns: List[Dict[str, Any]] = []
    
    async def store_decision_outcome(self, decision: AgentDecision, outcome: Dict[str, Any]):
        """Store decision outcome for learning"""
        
        outcome_record = {
            'decision_id': decision.decision_id,
            'decision_factors': decision.reasoning_chain,
            'expected_outcome': decision.expected_outcome,
            'actual_outcome': outcome,
            'success': outcome.get('success', False),
            'cost_impact': outcome.get('cost_impact', 0),
            'safety_impact': outcome.get('safety_impact', 0),
            'timestamp': datetime.utcnow()
        }
        
        self.decision_outcomes.append(outcome_record)
        
        # Update learned patterns
        await self._update_learned_patterns(outcome_record)
    
    async def _update_learned_patterns(self, outcome_record: Dict[str, Any]):
        """Update learned patterns based on outcomes"""
        
        if outcome_record['success']:
            self.success_patterns.append({
                'pattern': outcome_record['decision_factors'],
                'outcome': outcome_record['actual_outcome'],
                'confidence': 0.8  # Initial confidence
            })
        else:
            self.failure_patterns.append({
                'pattern': outcome_record['decision_factors'],
                'outcome': outcome_record['actual_outcome'],
                'confidence': 0.8
            })
    
    def get_similar_decisions(self, current_factors: List[str]) -> List[Dict[str, Any]]:
        """Find similar past decisions for learning"""
        
        similar_decisions = []
        
        for outcome in self.decision_outcomes:
            similarity = self._calculate_similarity(current_factors, outcome['decision_factors'])
            if similarity > 0.7:  # High similarity threshold
                similar_decisions.append({
                    'outcome': outcome,
                    'similarity': similarity
                })
        
        return sorted(similar_decisions, key=lambda x: x['similarity'], reverse=True)
    
    def _calculate_similarity(self, factors1: List[str], factors2: List[str]) -> float:
        """Calculate similarity between decision factors"""
        
        if not factors1 or not factors2:
            return 0.0
        
        common_factors = set(factors1) & set(factors2)
        total_factors = set(factors1) | set(factors2)
        
        return len(common_factors) / len(total_factors) if total_factors else 0.0


class StrategicPlanner:
    """Strategic planning component for proactive maintenance"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.planning_horizon_days = config.get('planning_horizon_days', 90)
        self.strategy_templates = self._load_strategy_templates()
    
    async def develop_fleet_strategy(self, fleet_data: Dict[str, Any], goals: Dict[AgentGoal, float]) -> MaintenanceStrategy:
        """Develop proactive maintenance strategy for entire fleet"""
        
        # Analyze current fleet state
        fleet_analysis = await self._analyze_fleet_state(fleet_data)
        
        # Predict future maintenance needs
        future_needs = await self._forecast_maintenance_needs(fleet_analysis)
        
        # Generate strategic options
        strategy_options = await self._generate_strategy_options(fleet_analysis, future_needs)
        
        # Evaluate options against goals
        best_strategy = await self._select_optimal_strategy(strategy_options, goals)
        
        return best_strategy
    
    async def _analyze_fleet_state(self, fleet_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze current fleet health and patterns"""
        
        analysis = {
            'fleet_health_score': 0.0,
            'risk_distribution': {},
            'maintenance_backlog': [],
            'resource_utilization': {},
            'cost_trends': {},
            'failure_patterns': []
        }
        
        # Calculate fleet health score
        total_vehicles = len(fleet_data.get('vehicles', []))
        healthy_vehicles = 0
        
        for vehicle_id, vehicle_data in fleet_data.get('vehicles', {}).items():
            health_score = vehicle_data.get('health_score', 0.5)
            if health_score > 0.7:
                healthy_vehicles += 1
        
        analysis['fleet_health_score'] = healthy_vehicles / total_vehicles if total_vehicles > 0 else 0
        
        # Analyze risk distribution
        risk_levels = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
        for vehicle_data in fleet_data.get('vehicles', {}).values():
            risk_level = vehicle_data.get('risk_level', 'medium')
            risk_levels[risk_level] += 1
        
        analysis['risk_distribution'] = risk_levels
        
        return analysis
    
    async def _forecast_maintenance_needs(self, fleet_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Forecast future maintenance needs"""
        
        forecast = {
            'predicted_failures': [],
            'resource_demand': {},
            'cost_projections': {},
            'timeline': {}
        }
        
        # Simple forecasting based on current trends
        # In production, this would use sophisticated ML models
        
        current_failure_rate = fleet_analysis.get('failure_rate', 0.1)
        
        for days_ahead in [7, 14, 30, 60, 90]:
            predicted_failures = int(current_failure_rate * days_ahead * 0.1)
            forecast['predicted_failures'].append({
                'days_ahead': days_ahead,
                'expected_failures': predicted_failures,
                'confidence': 0.7
            })
        
        return forecast
    
    async def _generate_strategy_options(self, fleet_analysis: Dict[str, Any], future_needs: Dict[str, Any]) -> List[MaintenanceStrategy]:
        """Generate multiple strategic options"""
        
        strategies = []
        
        # Strategy 1: Aggressive Preventive Maintenance
        aggressive_strategy = MaintenanceStrategy(
            strategy_id="aggressive_preventive",
            target_vehicles=list(fleet_analysis.get('high_risk_vehicles', [])),
            planned_actions=[
                {'action': 'increase_inspection_frequency', 'frequency': 'weekly'},
                {'action': 'proactive_component_replacement', 'threshold': 0.3},
                {'action': 'enhanced_monitoring', 'components': ['tire', 'brake']}
            ],
            expected_outcomes={
                'failure_reduction': 0.8,
                'cost_increase': 0.3,
                'downtime_reduction': 0.7
            },
            timeline={
                'start': datetime.utcnow(),
                'full_implementation': datetime.utcnow() + timedelta(days=30)
            },
            resource_requirements={
                'additional_technicians': 2,
                'parts_inventory_increase': 0.5,
                'budget_increase': 0.3
            },
            success_metrics={
                'target_failure_rate': 0.05,
                'target_uptime': 0.98,
                'max_cost_increase': 0.35
            },
            created_at=datetime.utcnow()
        )
        strategies.append(aggressive_strategy)
        
        # Strategy 2: Cost-Optimized Maintenance
        cost_optimized_strategy = MaintenanceStrategy(
            strategy_id="cost_optimized",
            target_vehicles=list(fleet_analysis.get('all_vehicles', [])),
            planned_actions=[
                {'action': 'optimize_maintenance_intervals', 'method': 'data_driven'},
                {'action': 'bulk_parts_purchasing', 'discount_target': 0.15},
                {'action': 'service_center_load_balancing', 'efficiency_gain': 0.2}
            ],
            expected_outcomes={
                'cost_reduction': 0.2,
                'failure_increase': 0.1,
                'efficiency_improvement': 0.25
            },
            timeline={
                'start': datetime.utcnow(),
                'full_implementation': datetime.utcnow() + timedelta(days=60)
            },
            resource_requirements={
                'process_optimization': True,
                'staff_training': True,
                'system_upgrades': True
            },
            success_metrics={
                'target_cost_reduction': 0.18,
                'max_failure_increase': 0.12,
                'min_efficiency_gain': 0.2
            },
            created_at=datetime.utcnow()
        )
        strategies.append(cost_optimized_strategy)
        
        # Strategy 3: Safety-First Maintenance
        safety_first_strategy = MaintenanceStrategy(
            strategy_id="safety_first",
            target_vehicles=list(fleet_analysis.get('safety_critical_vehicles', [])),
            planned_actions=[
                {'action': 'zero_tolerance_safety_policy', 'components': ['tire', 'brake']},
                {'action': 'real_time_safety_monitoring', 'alert_threshold': 0.1},
                {'action': 'emergency_response_protocol', 'response_time': '< 1 hour'}
            ],
            expected_outcomes={
                'safety_improvement': 0.95,
                'cost_increase': 0.4,
                'operational_complexity': 0.3
            },
            timeline={
                'start': datetime.utcnow(),
                'full_implementation': datetime.utcnow() + timedelta(days=14)
            },
            resource_requirements={
                'emergency_response_team': True,
                'enhanced_monitoring_systems': True,
                'safety_training': True
            },
            success_metrics={
                'zero_safety_incidents': True,
                'max_response_time': 3600,  # 1 hour in seconds
                'min_safety_score': 0.95
            },
            created_at=datetime.utcnow()
        )
        strategies.append(safety_first_strategy)
        
        return strategies
    
    async def _select_optimal_strategy(self, strategies: List[MaintenanceStrategy], goals: Dict[AgentGoal, float]) -> MaintenanceStrategy:
        """Select optimal strategy based on goals"""
        
        best_strategy = None
        best_score = 0.0
        
        for strategy in strategies:
            score = await self._evaluate_strategy_score(strategy, goals)
            if score > best_score:
                best_score = score
                best_strategy = strategy
        
        return best_strategy
    
    async def _evaluate_strategy_score(self, strategy: MaintenanceStrategy, goals: Dict[AgentGoal, float]) -> float:
        """Evaluate strategy against agent goals"""
        
        score = 0.0
        
        # Evaluate against each goal
        for goal, weight in goals.items():
            goal_score = 0.0
            
            if goal == AgentGoal.MINIMIZE_COST:
                cost_impact = strategy.expected_outcomes.get('cost_reduction', 0) - strategy.expected_outcomes.get('cost_increase', 0)
                goal_score = max(0, cost_impact)  # Positive for cost reduction
                
            elif goal == AgentGoal.MAXIMIZE_SAFETY:
                safety_impact = strategy.expected_outcomes.get('safety_improvement', 0)
                goal_score = safety_impact
                
            elif goal == AgentGoal.MINIMIZE_DOWNTIME:
                downtime_impact = strategy.expected_outcomes.get('downtime_reduction', 0)
                goal_score = downtime_impact
                
            elif goal == AgentGoal.OPTIMIZE_RESOURCES:
                efficiency_impact = strategy.expected_outcomes.get('efficiency_improvement', 0)
                goal_score = efficiency_impact
            
            score += goal_score * weight
        
        return score
    
    def _load_strategy_templates(self) -> Dict[str, Any]:
        """Load strategy templates for different scenarios"""
        
        return {
            'high_failure_rate': {
                'actions': ['increase_monitoring', 'proactive_replacement'],
                'timeline': 'immediate'
            },
            'cost_pressure': {
                'actions': ['optimize_intervals', 'bulk_purchasing'],
                'timeline': 'gradual'
            },
            'safety_concerns': {
                'actions': ['enhanced_inspections', 'zero_tolerance'],
                'timeline': 'immediate'
            }
        }


class OutcomeLearner:
    """Learning component that improves agent performance over time"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.learning_rate = config.get('learning_rate', 0.1)
        self.adaptation_threshold = config.get('adaptation_threshold', 0.8)
    
    async def learn_from_outcomes(self, memory: AgentMemory) -> Dict[str, Any]:
        """Learn from decision outcomes and adapt behavior"""
        
        learning_insights = {
            'successful_patterns': [],
            'failure_patterns': [],
            'threshold_adjustments': {},
            'strategy_improvements': []
        }
        
        # Analyze recent outcomes
        recent_outcomes = [o for o in memory.decision_outcomes if 
                          (datetime.utcnow() - o['timestamp']).days <= 30]
        
        if len(recent_outcomes) < 10:  # Need sufficient data
            return learning_insights
        
        # Identify successful patterns
        successful_outcomes = [o for o in recent_outcomes if o['success']]
        if successful_outcomes:
            success_patterns = await self._extract_success_patterns(successful_outcomes)
            learning_insights['successful_patterns'] = success_patterns
        
        # Identify failure patterns
        failed_outcomes = [o for o in recent_outcomes if not o['success']]
        if failed_outcomes:
            failure_patterns = await self._extract_failure_patterns(failed_outcomes)
            learning_insights['failure_patterns'] = failure_patterns
        
        # Calculate threshold adjustments
        threshold_adjustments = await self._calculate_threshold_adjustments(recent_outcomes)
        learning_insights['threshold_adjustments'] = threshold_adjustments
        
        return learning_insights
    
    async def _extract_success_patterns(self, successful_outcomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract patterns from successful decisions"""
        
        patterns = []
        
        # Group by similar decision factors
        factor_groups = {}
        for outcome in successful_outcomes:
            factors_key = tuple(sorted(outcome['decision_factors']))
            if factors_key not in factor_groups:
                factor_groups[factors_key] = []
            factor_groups[factors_key].append(outcome)
        
        # Identify patterns with high success rates
        for factors, outcomes in factor_groups.items():
            if len(outcomes) >= 3:  # Minimum occurrences
                avg_cost_impact = sum(o['actual_outcome'].get('cost_impact', 0) for o in outcomes) / len(outcomes)
                avg_safety_impact = sum(o['actual_outcome'].get('safety_impact', 0) for o in outcomes) / len(outcomes)
                
                patterns.append({
                    'factors': list(factors),
                    'success_rate': 1.0,  # All were successful
                    'avg_cost_impact': avg_cost_impact,
                    'avg_safety_impact': avg_safety_impact,
                    'occurrences': len(outcomes),
                    'confidence': min(0.95, len(outcomes) * 0.1)
                })
        
        return patterns
    
    async def _extract_failure_patterns(self, failed_outcomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract patterns from failed decisions"""
        
        patterns = []
        
        # Similar grouping logic for failures
        factor_groups = {}
        for outcome in failed_outcomes:
            factors_key = tuple(sorted(outcome['decision_factors']))
            if factors_key not in factor_groups:
                factor_groups[factors_key] = []
            factor_groups[factors_key].append(outcome)
        
        for factors, outcomes in factor_groups.items():
            if len(outcomes) >= 2:  # Lower threshold for failure patterns
                patterns.append({
                    'factors': list(factors),
                    'failure_rate': 1.0,
                    'common_failure_reasons': [o['actual_outcome'].get('failure_reason', 'unknown') for o in outcomes],
                    'occurrences': len(outcomes),
                    'confidence': min(0.9, len(outcomes) * 0.15)
                })
        
        return patterns
    
    async def _calculate_threshold_adjustments(self, outcomes: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate adjustments to decision thresholds based on outcomes"""
        
        adjustments = {}
        
        # Analyze threshold effectiveness
        threshold_performance = {}
        
        for outcome in outcomes:
            # Extract threshold information from decision factors
            for factor in outcome['decision_factors']:
                if 'threshold' in factor:
                    threshold_key = factor.split('_threshold')[0]
                    if threshold_key not in threshold_performance:
                        threshold_performance[threshold_key] = {'successes': 0, 'failures': 0}
                    
                    if outcome['success']:
                        threshold_performance[threshold_key]['successes'] += 1
                    else:
                        threshold_performance[threshold_key]['failures'] += 1
        
        # Calculate adjustments
        for threshold_key, performance in threshold_performance.items():
            total = performance['successes'] + performance['failures']
            if total >= 5:  # Minimum data points
                success_rate = performance['successes'] / total
                
                if success_rate < 0.6:  # Poor performance
                    adjustments[threshold_key] = -0.1  # Lower threshold
                elif success_rate > 0.9:  # Excellent performance
                    adjustments[threshold_key] = 0.05  # Slightly raise threshold
        
        return adjustments


class FleetKnowledgeBase:
    """Knowledge base for fleet-wide insights and patterns"""
    
    def __init__(self):
        self.vehicle_profiles: Dict[str, Dict[str, Any]] = {}
        self.failure_patterns: Dict[str, List[Dict[str, Any]]] = {}
        self.maintenance_best_practices: List[Dict[str, Any]] = []
        self.cost_benchmarks: Dict[str, float] = {}
    
    async def update_vehicle_profile(self, vehicle_id: str, profile_data: Dict[str, Any]):
        """Update vehicle profile with new insights"""
        
        if vehicle_id not in self.vehicle_profiles:
            self.vehicle_profiles[vehicle_id] = {}
        
        self.vehicle_profiles[vehicle_id].update(profile_data)
        self.vehicle_profiles[vehicle_id]['last_updated'] = datetime.utcnow()
    
    async def get_similar_vehicles(self, vehicle_id: str) -> List[str]:
        """Find vehicles with similar characteristics"""
        
        if vehicle_id not in self.vehicle_profiles:
            return []
        
        target_profile = self.vehicle_profiles[vehicle_id]
        similar_vehicles = []
        
        for other_id, other_profile in self.vehicle_profiles.items():
            if other_id != vehicle_id:
                similarity = self._calculate_profile_similarity(target_profile, other_profile)
                if similarity > 0.7:
                    similar_vehicles.append(other_id)
        
        return similar_vehicles
    
    def _calculate_profile_similarity(self, profile1: Dict[str, Any], profile2: Dict[str, Any]) -> float:
        """Calculate similarity between vehicle profiles"""
        
        # Simple similarity based on common attributes
        common_attrs = ['usage_pattern', 'vehicle_type', 'age_category', 'mileage_category']
        matches = 0
        
        for attr in common_attrs:
            if profile1.get(attr) == profile2.get(attr):
                matches += 1
        
        return matches / len(common_attrs) if common_attrs else 0.0
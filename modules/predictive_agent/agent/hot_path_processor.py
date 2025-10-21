"""
Hot Path Processor - Real-Time Agentic Decision Making

This module handles real-time telemetry events and makes autonomous decisions
within seconds using contextual reasoning and goal optimization.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from .agentic_behaviors import AgentGoal, AgentDecision, AgentMemory

logger = logging.getLogger(__name__)


class ActionUrgency(Enum):
    IMMEDIATE = "immediate"      # < 30 seconds
    URGENT = "urgent"           # < 5 minutes  
    SCHEDULED = "scheduled"     # < 1 hour
    MONITOR = "monitor"         # Continuous observation


@dataclass
class TelemetryEvent:
    """Real-time telemetry event from Flink"""
    vehicle_id: str
    device_id: str
    component_type: str
    trigger_type: str
    urgency: str
    component_position: str
    timestamp: str
    telemetry_snapshot: Dict[str, Any]
    source: str = "flink_telemetry_processor"


@dataclass
class DecisionContext:
    """Complete context for making real-time decisions"""
    vehicle_profile: Dict[str, Any]
    current_location: Dict[str, float]
    weather_conditions: Dict[str, Any]
    traffic_conditions: Dict[str, Any]
    driver_profile: Dict[str, Any]
    recent_maintenance: List[Dict[str, Any]]
    similar_vehicle_patterns: List[Dict[str, Any]]
    service_center_availability: Dict[str, Any]
    parts_availability: Dict[str, Any]


@dataclass
class ActionOption:
    """Possible action the agent can take"""
    action_id: str
    action_type: str
    description: str
    urgency: ActionUrgency
    estimated_duration: timedelta
    resource_requirements: Dict[str, Any]
    expected_outcomes: Dict[str, float]
    risk_assessment: Dict[str, float]
    cost_estimate: float
    safety_impact: float
    goal_alignment: Dict[AgentGoal, float]


class HotPathProcessor:
    """
    Processes real-time telemetry events and makes autonomous decisions
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.response_time_target = config.get('hot_path_response_time_seconds', 10)
        self.context_cache = {}  # Cache recent context for speed
        self.decision_templates = self._load_decision_templates()
        
        # Agent goals for hot path decisions
        self.hot_path_goals = {
            AgentGoal.MAXIMIZE_SAFETY: 0.5,      # Safety is primary in hot path
            AgentGoal.MINIMIZE_DOWNTIME: 0.3,    # Quick resolution important
            AgentGoal.MINIMIZE_COST: 0.2         # Cost secondary in emergencies
        }
        
        logger.info("Hot Path Processor initialized")
    
    async def process_telemetry_event(self, event: TelemetryEvent) -> AgentDecision:
        """
        Main entry point for processing real-time telemetry events
        """
        
        start_time = datetime.utcnow()
        
        try:
            # Step 1: Rapid Context Gathering (< 2 seconds)
            context = await self._gather_rapid_context(event)
            
            # Step 2: Situation Assessment (< 1 second)
            situation = await self._assess_situation(event, context)
            
            # Step 3: Generate Action Options (< 2 seconds)
            options = await self._generate_action_options(event, context, situation)
            
            # Step 4: Select Optimal Action (< 1 second)
            selected_action = await self._select_optimal_action(options, situation)
            
            # Step 5: Execute Decision (< 4 seconds)
            execution_result = await self._execute_decision(selected_action, context)
            
            # Create decision record
            decision = AgentDecision(
                decision_id=f"hot_{event.vehicle_id}_{int(start_time.timestamp())}",
                vehicle_id=event.vehicle_id,
                reasoning_chain=self._build_reasoning_chain(event, context, situation, options, selected_action),
                causal_factors=situation.get('causal_factors', []),
                alternative_options=[self._option_to_dict(opt) for opt in options],
                selected_option=self._option_to_dict(selected_action),
                expected_outcome=selected_action.expected_outcomes,
                confidence=situation.get('confidence', 0.8),
                goals_impact=selected_action.goal_alignment,
                created_at=start_time
            )
            
            # Log performance
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"Hot path decision completed in {processing_time:.2f}s for {event.vehicle_id}")
            
            return decision
            
        except Exception as e:
            logger.error(f"Hot path processing failed for {event.vehicle_id}: {str(e)}")
            # Return safe fallback decision
            return await self._create_fallback_decision(event, start_time)
    
    async def _gather_rapid_context(self, event: TelemetryEvent) -> DecisionContext:
        """
        Rapidly gather essential context for decision making
        Target: < 2 seconds
        """
        
        # Check cache first for speed
        cache_key = f"{event.vehicle_id}_{event.component_type}"
        if cache_key in self.context_cache:
            cached_context = self.context_cache[cache_key]
            # Use cached context if less than 5 minutes old
            if (datetime.utcnow() - cached_context['timestamp']).seconds < 300:
                return cached_context['context']
        
        # Gather context in parallel for speed
        context_tasks = [
            self._get_vehicle_profile(event.vehicle_id),
            self._get_current_location(event.vehicle_id),
            self._get_weather_conditions(event.vehicle_id),
            self._get_driver_profile(event.vehicle_id),
            self._get_recent_maintenance(event.vehicle_id),
            self._get_service_center_availability(event.vehicle_id)
        ]
        
        results = await asyncio.gather(*context_tasks, return_exceptions=True)
        
        # Build context from results
        context = DecisionContext(
            vehicle_profile=results[0] if not isinstance(results[0], Exception) else {},
            current_location=results[1] if not isinstance(results[1], Exception) else {},
            weather_conditions=results[2] if not isinstance(results[2], Exception) else {},
            traffic_conditions={},  # Skip traffic for speed in hot path
            driver_profile=results[3] if not isinstance(results[3], Exception) else {},
            recent_maintenance=results[4] if not isinstance(results[4], Exception) else [],
            similar_vehicle_patterns=[],  # Skip for speed in hot path
            service_center_availability=results[5] if not isinstance(results[5], Exception) else {},
            parts_availability={}  # Skip for speed in hot path
        )
        
        # Cache for future use
        self.context_cache[cache_key] = {
            'context': context,
            'timestamp': datetime.utcnow()
        }
        
        return context
    
    async def _assess_situation(self, event: TelemetryEvent, context: DecisionContext) -> Dict[str, Any]:
        """
        Assess the current situation and determine severity/causality
        Target: < 1 second
        """
        
        situation = {
            'severity_level': 'medium',
            'safety_risk': 0.5,
            'immediate_danger': False,
            'causal_factors': [],
            'confidence': 0.8,
            'time_to_failure': None,
            'intervention_window': timedelta(hours=24)
        }
        
        # Parse telemetry snapshot
        telemetry = event.telemetry_snapshot
        
        # Assess severity based on component and values
        if event.component_type == 'tire':
            situation.update(await self._assess_tire_situation(telemetry, context))
        elif event.component_type == 'brake':
            situation.update(await self._assess_brake_situation(telemetry, context))
        elif event.component_type == 'engine':
            situation.update(await self._assess_engine_situation(telemetry, context))
        
        # Identify causal factors
        situation['causal_factors'] = await self._identify_causal_factors(event, context, situation)
        
        return situation
    
    async def _assess_tire_situation(self, telemetry: Dict[str, Any], context: DecisionContext) -> Dict[str, Any]:
        """Assess tire-specific situation"""
        
        pressure = telemetry.get('pressure_mbar', 2200)
        temperature = telemetry.get('temperature_celsius', 40)
        tread_depth = telemetry.get('tread_depth_mm')
        position = telemetry.get('position', 'unknown')
        
        assessment = {}
        
        # Critical pressure assessment
        if pressure < 1500:
            assessment.update({
                'severity_level': 'critical',
                'safety_risk': 0.95,
                'immediate_danger': True,
                'time_to_failure': timedelta(minutes=30),
                'intervention_window': timedelta(minutes=15)
            })
        elif pressure < 1800:
            assessment.update({
                'severity_level': 'high',
                'safety_risk': 0.8,
                'immediate_danger': False,
                'time_to_failure': timedelta(hours=2),
                'intervention_window': timedelta(hours=1)
            })
        elif pressure < 2000:
            assessment.update({
                'severity_level': 'medium',
                'safety_risk': 0.4,
                'time_to_failure': timedelta(days=1),
                'intervention_window': timedelta(hours=8)
            })
        
        # Temperature assessment
        if temperature > 80:
            assessment['safety_risk'] = max(assessment.get('safety_risk', 0), 0.9)
            assessment['immediate_danger'] = True
        
        # Tread depth assessment
        if tread_depth and tread_depth < 1.6:  # Legal minimum
            assessment['safety_risk'] = max(assessment.get('safety_risk', 0), 0.85)
            assessment['severity_level'] = 'high'
        
        # Weather impact
        weather = context.weather_conditions
        if weather.get('weather') in ['rain', 'snow'] and assessment.get('safety_risk', 0) > 0.5:
            assessment['safety_risk'] *= 1.3  # Increase risk in bad weather
            assessment['immediate_danger'] = True
        
        return assessment
    
    async def _identify_causal_factors(self, event: TelemetryEvent, context: DecisionContext, situation: Dict[str, Any]) -> List[str]:
        """Identify likely causal factors for the current situation"""
        
        factors = []
        
        # Driver behavior factors
        driver_score = context.driver_profile.get('behavior_score', 0.5)
        if driver_score < 0.4:
            factors.append('aggressive_driving_behavior')
        
        # Environmental factors
        weather = context.weather_conditions.get('weather', 'clear')
        if weather in ['rain', 'snow', 'ice']:
            factors.append(f'adverse_weather_{weather}')
        
        # Vehicle usage factors
        usage_pattern = context.vehicle_profile.get('usage_pattern', 'mixed')
        if usage_pattern == 'commercial':
            factors.append('heavy_commercial_usage')
        
        # Maintenance factors
        last_maintenance = context.recent_maintenance
        if last_maintenance:
            days_since = (datetime.utcnow() - datetime.fromisoformat(last_maintenance[0].get('date', '2024-01-01'))).days
            if days_since > 90:
                factors.append('overdue_maintenance')
        else:
            factors.append('no_recent_maintenance_history')
        
        # Component-specific factors
        if event.component_type == 'tire':
            mileage = context.vehicle_profile.get('current_mileage', 0)
            if mileage > 50000:
                factors.append('high_mileage_tire_wear')
        
        return factors
    
    async def _generate_action_options(self, event: TelemetryEvent, context: DecisionContext, situation: Dict[str, Any]) -> List[ActionOption]:
        """
        Generate possible action options based on situation
        Target: < 2 seconds
        """
        
        options = []
        severity = situation.get('severity_level', 'medium')
        safety_risk = situation.get('safety_risk', 0.5)
        
        # Option 1: Immediate Stop/Service
        if severity == 'critical' or situation.get('immediate_danger', False):
            options.append(ActionOption(
                action_id='immediate_stop',
                action_type='emergency_intervention',
                description='Immediate vehicle stop and emergency service',
                urgency=ActionUrgency.IMMEDIATE,
                estimated_duration=timedelta(hours=2),
                resource_requirements={
                    'emergency_technician': True,
                    'tow_truck': True,
                    'replacement_parts': True
                },
                expected_outcomes={
                    'safety_improvement': 0.95,
                    'downtime': 0.8,
                    'cost_impact': 0.9
                },
                risk_assessment={
                    'continued_operation_risk': 0.95,
                    'intervention_risk': 0.1
                },
                cost_estimate=800.0,
                safety_impact=0.95,
                goal_alignment={
                    AgentGoal.MAXIMIZE_SAFETY: 0.95,
                    AgentGoal.MINIMIZE_DOWNTIME: 0.2,
                    AgentGoal.MINIMIZE_COST: 0.1
                }
            ))
        
        # Option 2: Route to Nearest Service Center
        if severity in ['high', 'critical']:
            nearest_center = context.service_center_availability.get('nearest_center')
            if nearest_center:
                options.append(ActionOption(
                    action_id='route_to_service',
                    action_type='guided_intervention',
                    description=f'Route to {nearest_center.get("name", "service center")} for immediate service',
                    urgency=ActionUrgency.URGENT,
                    estimated_duration=timedelta(hours=1),
                    resource_requirements={
                        'service_appointment': True,
                        'route_guidance': True,
                        'parts_availability': True
                    },
                    expected_outcomes={
                        'safety_improvement': 0.8,
                        'downtime': 0.4,
                        'cost_impact': 0.5
                    },
                    risk_assessment={
                        'travel_risk': safety_risk * 0.3,
                        'delay_risk': 0.2
                    },
                    cost_estimate=400.0,
                    safety_impact=0.8,
                    goal_alignment={
                        AgentGoal.MAXIMIZE_SAFETY: 0.8,
                        AgentGoal.MINIMIZE_DOWNTIME: 0.6,
                        AgentGoal.MINIMIZE_COST: 0.5
                    }
                ))
        
        # Option 3: Enhanced Monitoring with Restrictions
        if severity in ['medium', 'high']:
            options.append(ActionOption(
                action_id='enhanced_monitoring',
                action_type='monitored_operation',
                description='Continue operation with enhanced monitoring and driving restrictions',
                urgency=ActionUrgency.SCHEDULED,
                estimated_duration=timedelta(hours=8),
                resource_requirements={
                    'real_time_monitoring': True,
                    'driver_notification': True,
                    'route_restrictions': True
                },
                expected_outcomes={
                    'safety_improvement': 0.6,
                    'downtime': 0.1,
                    'cost_impact': 0.2
                },
                risk_assessment={
                    'continued_operation_risk': safety_risk * 0.6,
                    'monitoring_effectiveness': 0.8
                },
                cost_estimate=100.0,
                safety_impact=0.6,
                goal_alignment={
                    AgentGoal.MAXIMIZE_SAFETY: 0.6,
                    AgentGoal.MINIMIZE_DOWNTIME: 0.9,
                    AgentGoal.MINIMIZE_COST: 0.8
                }
            ))
        
        # Option 4: Scheduled Maintenance
        if severity in ['low', 'medium']:
            options.append(ActionOption(
                action_id='scheduled_maintenance',
                action_type='preventive_maintenance',
                description='Schedule maintenance within optimal time window',
                urgency=ActionUrgency.SCHEDULED,
                estimated_duration=timedelta(hours=24),
                resource_requirements={
                    'maintenance_appointment': True,
                    'parts_ordering': True,
                    'schedule_optimization': True
                },
                expected_outcomes={
                    'safety_improvement': 0.7,
                    'downtime': 0.2,
                    'cost_impact': 0.3
                },
                risk_assessment={
                    'delay_risk': 0.3,
                    'cost_optimization': 0.8
                },
                cost_estimate=250.0,
                safety_impact=0.7,
                goal_alignment={
                    AgentGoal.MAXIMIZE_SAFETY: 0.7,
                    AgentGoal.MINIMIZE_DOWNTIME: 0.8,
                    AgentGoal.MINIMIZE_COST: 0.7
                }
            ))
        
        return options
    
    async def _select_optimal_action(self, options: List[ActionOption], situation: Dict[str, Any]) -> ActionOption:
        """
        Select the optimal action based on goals and situation
        Target: < 1 second
        """
        
        if not options:
            raise ValueError("No action options available")
        
        best_option = None
        best_score = 0.0
        
        for option in options:
            # Calculate weighted score based on goals
            score = 0.0
            for goal, weight in self.hot_path_goals.items():
                goal_score = option.goal_alignment.get(goal, 0.0)
                score += goal_score * weight
            
            # Apply situation-specific adjustments
            if situation.get('immediate_danger', False):
                # Heavily favor safety in dangerous situations
                safety_score = option.goal_alignment.get(AgentGoal.MAXIMIZE_SAFETY, 0.0)
                score = safety_score * 0.8 + score * 0.2
            
            # Consider confidence in the assessment
            confidence = situation.get('confidence', 0.8)
            score *= confidence
            
            if score > best_score:
                best_score = score
                best_option = option
        
        return best_option
    
    async def _execute_decision(self, action: ActionOption, context: DecisionContext) -> Dict[str, Any]:
        """
        Execute the selected action
        Target: < 4 seconds
        """
        
        execution_result = {
            'action_initiated': False,
            'notifications_sent': [],
            'resources_allocated': {},
            'estimated_completion': None
        }
        
        try:
            if action.action_type == 'emergency_intervention':
                # Send emergency notifications
                await self._send_emergency_notifications(action, context)
                execution_result['notifications_sent'].append('emergency_services')
                execution_result['notifications_sent'].append('fleet_manager')
                
                # Request emergency resources
                await self._request_emergency_resources(action, context)
                execution_result['resources_allocated']['emergency_technician'] = True
                
            elif action.action_type == 'guided_intervention':
                # Send route guidance
                await self._send_route_guidance(action, context)
                execution_result['notifications_sent'].append('driver_navigation')
                
                # Book service appointment
                await self._book_service_appointment(action, context)
                execution_result['resources_allocated']['service_appointment'] = True
                
            elif action.action_type == 'monitored_operation':
                # Enable enhanced monitoring
                await self._enable_enhanced_monitoring(action, context)
                execution_result['notifications_sent'].append('monitoring_system')
                
                # Send driver restrictions
                await self._send_driving_restrictions(action, context)
                execution_result['notifications_sent'].append('driver_restrictions')
                
            elif action.action_type == 'preventive_maintenance':
                # Schedule maintenance
                await self._schedule_maintenance(action, context)
                execution_result['resources_allocated']['maintenance_slot'] = True
                
                # Order parts if needed
                await self._order_parts(action, context)
                execution_result['resources_allocated']['parts_ordered'] = True
            
            execution_result['action_initiated'] = True
            execution_result['estimated_completion'] = datetime.utcnow() + action.estimated_duration
            
        except Exception as e:
            logger.error(f"Action execution failed: {str(e)}")
            execution_result['error'] = str(e)
        
        return execution_result
    
    # Helper methods for context gathering
    async def _get_vehicle_profile(self, vehicle_id: str) -> Dict[str, Any]:
        """Get vehicle profile quickly"""
        # In production, this would query Redis cache first, then DynamoDB
        return {
            'vehicle_id': vehicle_id,
            'usage_pattern': 'mixed',
            'current_mileage': 45000,
            'vehicle_type': 'sedan'
        }
    
    async def _get_current_location(self, vehicle_id: str) -> Dict[str, float]:
        """Get current vehicle location"""
        # In production, query Redis for latest location
        return {'latitude': 40.7128, 'longitude': -74.0060}
    
    async def _get_weather_conditions(self, vehicle_id: str) -> Dict[str, Any]:
        """Get current weather conditions"""
        # In production, call weather API based on location
        return {'weather': 'clear', 'temperature': 25, 'visibility': 10}
    
    async def _get_driver_profile(self, vehicle_id: str) -> Dict[str, Any]:
        """Get driver profile"""
        return {'behavior_score': 0.7, 'experience_years': 5}
    
    async def _get_recent_maintenance(self, vehicle_id: str) -> List[Dict[str, Any]]:
        """Get recent maintenance history"""
        return [{'date': '2024-09-15', 'type': 'oil_change', 'mileage': 42000}]
    
    async def _get_service_center_availability(self, vehicle_id: str) -> Dict[str, Any]:
        """Get nearby service center availability"""
        return {
            'nearest_center': {
                'name': 'Downtown Service Center',
                'distance_km': 5.2,
                'availability': 'immediate'
            }
        }
    
    # Helper methods for action execution
    async def _send_emergency_notifications(self, action: ActionOption, context: DecisionContext):
        """Send emergency notifications"""
        logger.info(f"Emergency notification sent for action: {action.action_id}")
    
    async def _request_emergency_resources(self, action: ActionOption, context: DecisionContext):
        """Request emergency resources"""
        logger.info(f"Emergency resources requested for action: {action.action_id}")
    
    async def _send_route_guidance(self, action: ActionOption, context: DecisionContext):
        """Send route guidance to driver"""
        logger.info(f"Route guidance sent for action: {action.action_id}")
    
    async def _book_service_appointment(self, action: ActionOption, context: DecisionContext):
        """Book service center appointment"""
        logger.info(f"Service appointment booked for action: {action.action_id}")
    
    async def _enable_enhanced_monitoring(self, action: ActionOption, context: DecisionContext):
        """Enable enhanced monitoring"""
        logger.info(f"Enhanced monitoring enabled for action: {action.action_id}")
    
    async def _send_driving_restrictions(self, action: ActionOption, context: DecisionContext):
        """Send driving restrictions to driver"""
        logger.info(f"Driving restrictions sent for action: {action.action_id}")
    
    async def _schedule_maintenance(self, action: ActionOption, context: DecisionContext):
        """Schedule maintenance appointment"""
        logger.info(f"Maintenance scheduled for action: {action.action_id}")
    
    async def _order_parts(self, action: ActionOption, context: DecisionContext):
        """Order required parts"""
        logger.info(f"Parts ordered for action: {action.action_id}")
    
    # Utility methods
    def _build_reasoning_chain(self, event: TelemetryEvent, context: DecisionContext, 
                              situation: Dict[str, Any], options: List[ActionOption], 
                              selected_action: ActionOption) -> List[str]:
        """Build reasoning chain for decision transparency"""
        
        chain = [
            f"Received {event.trigger_type} event for {event.component_type}",
            f"Assessed situation severity as {situation.get('severity_level')}",
            f"Safety risk calculated as {situation.get('safety_risk', 0):.2f}",
            f"Identified causal factors: {', '.join(situation.get('causal_factors', []))}",
            f"Generated {len(options)} action options",
            f"Selected {selected_action.action_type} based on goal optimization",
            f"Expected safety improvement: {selected_action.expected_outcomes.get('safety_improvement', 0):.2f}"
        ]
        
        return chain
    
    def _option_to_dict(self, option: ActionOption) -> Dict[str, Any]:
        """Convert ActionOption to dictionary"""
        
        return {
            'action_id': option.action_id,
            'action_type': option.action_type,
            'description': option.description,
            'urgency': option.urgency.value,
            'cost_estimate': option.cost_estimate,
            'safety_impact': option.safety_impact,
            'expected_outcomes': option.expected_outcomes
        }
    
    async def _create_fallback_decision(self, event: TelemetryEvent, start_time: datetime) -> AgentDecision:
        """Create safe fallback decision when processing fails"""
        
        return AgentDecision(
            decision_id=f"fallback_{event.vehicle_id}_{int(start_time.timestamp())}",
            vehicle_id=event.vehicle_id,
            reasoning_chain=["Processing failed", "Applied safe fallback", "Enhanced monitoring enabled"],
            causal_factors=["system_error"],
            alternative_options=[],
            selected_option={
                'action_type': 'enhanced_monitoring',
                'description': 'Safe fallback: enhanced monitoring',
                'safety_impact': 0.6
            },
            expected_outcome={'safety_improvement': 0.6},
            confidence=0.5,
            goals_impact={AgentGoal.MAXIMIZE_SAFETY: 0.6},
            created_at=start_time
        )
    
    def _load_decision_templates(self) -> Dict[str, Any]:
        """Load decision templates for common scenarios"""
        
        return {
            'tire_pressure_critical': {
                'default_action': 'immediate_stop',
                'safety_priority': True
            },
            'brake_failure': {
                'default_action': 'immediate_stop',
                'safety_priority': True
            },
            'engine_overheating': {
                'default_action': 'route_to_service',
                'safety_priority': True
            }
        }
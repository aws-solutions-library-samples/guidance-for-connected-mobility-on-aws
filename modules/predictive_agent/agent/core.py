"""
Predictive Maintenance Agent Core

The main agent orchestrator that coordinates between different ML models,
decision engines, and external integrations to make autonomous maintenance decisions.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

from ..models import ModelRegistry
from ..decision_engine import DecisionEngine
from ..integrations import CMSConnector, ExternalAPIs

logger = logging.getLogger(__name__)


class MaintenanceUrgency(Enum):
    CRITICAL = "critical"           # Immediate action required
    HIGH = "high"                  # Schedule within 1-3 days
    MEDIUM = "medium"              # Schedule within 1-2 weeks
    LOW = "low"                    # Schedule within 1-3 months
    MONITOR = "monitor"            # Increase monitoring frequency


class ComponentType(Enum):
    TIRE = "tire"
    BRAKE = "brake"
    ENGINE = "engine"
    BATTERY = "battery"
    TRANSMISSION = "transmission"
    SUSPENSION = "suspension"


@dataclass
class MaintenanceDecision:
    """Represents an autonomous maintenance decision made by the agent"""
    vehicle_id: str
    component_type: ComponentType
    urgency: MaintenanceUrgency
    predicted_failure_date: Optional[datetime]
    confidence_score: float
    recommended_action: str
    cost_estimate: Optional[float]
    parts_needed: List[str]
    service_time_hours: float
    reasoning: str
    created_at: datetime


@dataclass
class VehicleContext:
    """Complete context about a vehicle for decision making"""
    vehicle_id: str
    current_mileage: int
    last_service_date: Optional[datetime]
    service_history: List[Dict]
    current_location: Dict[str, float]  # lat, lon
    usage_pattern: str  # "city", "highway", "mixed", "commercial"
    driver_behavior_score: float
    environmental_conditions: Dict[str, Any]


class PredictiveMaintenanceAgent:
    """
    Agentic maintenance system with autonomous planning, learning, and goal optimization
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_registry = ModelRegistry(config.get('models', {}))
        self.decision_engine = DecisionEngine(config.get('decision_engine', {}))
        self.cms_connector = CMSConnector(config.get('cms_integration', {}))
        self.external_apis = ExternalAPIs(config.get('external_apis', {}))
        
        # Agentic components
        self.goals = self._initialize_goals(config.get('goals', {}))
        self.memory = AgentMemory(config.get('memory', {}))
        self.planner = StrategicPlanner(config.get('planner', {}))
        self.learner = OutcomeLearner(config.get('learner', {}))
        
        # Agent state
        self.active_strategies: Dict[str, MaintenanceStrategy] = {}
        self.fleet_knowledge: FleetKnowledgeBase = FleetKnowledgeBase()
        self.decision_history: List[AgentDecision] = []
        
        logger.info("Agentic Predictive Maintenance System initialized")
    
    async def analyze_vehicle(self, vehicle_id: str) -> List[MaintenanceDecision]:
        """
        Perform complete analysis of a vehicle and return maintenance decisions
        """
        try:
            # Gather vehicle context
            context = await self._gather_vehicle_context(vehicle_id)
            
            # Get latest telemetry data
            telemetry = await self.cms_connector.get_recent_telemetry(
                vehicle_id, 
                hours=24
            )
            
            # Run predictions for each component
            predictions = await self._run_component_predictions(context, telemetry)
            
            # Make maintenance decisions
            decisions = await self._make_maintenance_decisions(context, predictions)
            
            # Store decisions for tracking
            self.active_decisions[vehicle_id] = decisions
            
            # Publish decisions to external systems
            await self._publish_decisions(decisions)
            
            logger.info(f"Analyzed vehicle {vehicle_id}, generated {len(decisions)} decisions")
            return decisions
            
        except Exception as e:
            logger.error(f"Error analyzing vehicle {vehicle_id}: {str(e)}")
            return []
    
    async def analyze_fleet(self, vehicle_ids: Optional[List[str]] = None) -> Dict[str, List[MaintenanceDecision]]:
        """
        Analyze entire fleet or subset of vehicles
        """
        if vehicle_ids is None:
            vehicle_ids = await self.cms_connector.get_active_vehicles()
        
        # Process vehicles in batches to avoid overwhelming systems
        batch_size = self.config.get('batch_size', 10)
        results = {}
        
        for i in range(0, len(vehicle_ids), batch_size):
            batch = vehicle_ids[i:i + batch_size]
            
            # Process batch concurrently
            tasks = [self.analyze_vehicle(vid) for vid in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Collect results
            for vehicle_id, decisions in zip(batch, batch_results):
                if isinstance(decisions, Exception):
                    logger.error(f"Failed to analyze {vehicle_id}: {decisions}")
                    results[vehicle_id] = []
                else:
                    results[vehicle_id] = decisions
            
            # Brief pause between batches
            await asyncio.sleep(1)
        
        # Perform fleet-level optimizations
        await self._optimize_fleet_maintenance(results)
        
        return results
    
    async def _gather_vehicle_context(self, vehicle_id: str) -> VehicleContext:
        """Gather comprehensive context about a vehicle"""
        
        # Get vehicle data from CMS platform
        vehicle_data = await self.cms_connector.get_vehicle_data(vehicle_id)
        service_history = await self.cms_connector.get_service_history(vehicle_id)
        
        # Get external context
        location = vehicle_data.get('last_known_location', {})
        weather = await self.external_apis.get_weather_conditions(
            location.get('latitude'), 
            location.get('longitude')
        )
        
        return VehicleContext(
            vehicle_id=vehicle_id,
            current_mileage=vehicle_data.get('odometer', 0),
            last_service_date=vehicle_data.get('last_service_date'),
            service_history=service_history,
            current_location=location,
            usage_pattern=vehicle_data.get('usage_pattern', 'mixed'),
            driver_behavior_score=vehicle_data.get('driver_score', 0.5),
            environmental_conditions=weather
        )
    
    async def _run_component_predictions(self, context: VehicleContext, telemetry: Dict) -> Dict[ComponentType, Dict]:
        """Run ML predictions for all vehicle components"""
        
        predictions = {}
        
        # Tire prediction
        if 'tire' in telemetry:
            tire_model = self.model_registry.get_model('tire_prediction')
            predictions[ComponentType.TIRE] = await tire_model.predict(
                telemetry['tire'], context
            )
        
        # Brake prediction
        if 'brake' in telemetry:
            brake_model = self.model_registry.get_model('brake_prediction')
            predictions[ComponentType.BRAKE] = await brake_model.predict(
                telemetry['brake'], context
            )
        
        # Engine prediction
        if 'engine' in telemetry:
            engine_model = self.model_registry.get_model('engine_prediction')
            predictions[ComponentType.ENGINE] = await engine_model.predict(
                telemetry['engine'], context
            )
        
        # Battery prediction (for EVs)
        if 'battery' in telemetry:
            battery_model = self.model_registry.get_model('battery_prediction')
            predictions[ComponentType.BATTERY] = await battery_model.predict(
                telemetry['battery'], context
            )
        
        return predictions
    
    async def _make_maintenance_decisions(self, context: VehicleContext, predictions: Dict) -> List[MaintenanceDecision]:
        """Convert predictions into actionable maintenance decisions"""
        
        decisions = []
        
        for component_type, prediction in predictions.items():
            decision = await self.decision_engine.evaluate_maintenance_need(
                context, component_type, prediction
            )
            
            if decision:
                decisions.append(decision)
        
        # Sort by urgency and confidence
        decisions.sort(key=lambda d: (d.urgency.value, -d.confidence_score))
        
        return decisions
    
    async def _publish_decisions(self, decisions: List[MaintenanceDecision]):
        """Publish decisions to external systems"""
        
        for decision in decisions:
            # Publish to EventBridge for CMS platform
            await self.cms_connector.publish_maintenance_decision(decision)
            
            # Notify external services for critical issues
            if decision.urgency == MaintenanceUrgency.CRITICAL:
                await self.external_apis.notify_emergency_services(decision)
            
            # Update parts inventory systems
            if decision.parts_needed:
                await self.external_apis.check_parts_availability(decision.parts_needed)
    
    async def _optimize_fleet_maintenance(self, fleet_decisions: Dict[str, List[MaintenanceDecision]]):
        """Perform fleet-level maintenance optimization"""
        
        # Group decisions by service center proximity
        # Optimize scheduling to minimize downtime
        # Coordinate parts ordering across fleet
        # Balance workload across service centers
        
        await self.decision_engine.optimize_fleet_schedule(fleet_decisions)
    
    async def get_vehicle_status(self, vehicle_id: str) -> Dict[str, Any]:
        """Get current maintenance status for a vehicle"""
        
        decisions = self.active_decisions.get(vehicle_id, [])
        
        return {
            'vehicle_id': vehicle_id,
            'active_decisions': len(decisions),
            'critical_issues': len([d for d in decisions if d.urgency == MaintenanceUrgency.CRITICAL]),
            'next_maintenance': min([d.predicted_failure_date for d in decisions if d.predicted_failure_date], default=None),
            'monitoring_status': vehicle_id in self.monitoring_vehicles,
            'last_analysis': datetime.utcnow().isoformat()
        }
    
    async def start_monitoring(self):
        """Start continuous monitoring of fleet"""
        
        logger.info("Starting predictive maintenance monitoring")
        
        while True:
            try:
                # Analyze high-priority vehicles more frequently
                priority_vehicles = await self._get_priority_vehicles()
                if priority_vehicles:
                    await self.analyze_fleet(priority_vehicles)
                
                # Full fleet analysis less frequently
                if datetime.utcnow().hour == 2:  # 2 AM daily analysis
                    await self.analyze_fleet()
                
                # Wait before next cycle
                await asyncio.sleep(self.config.get('monitoring_interval', 300))  # 5 minutes
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    async def _get_priority_vehicles(self) -> List[str]:
        """Get list of vehicles that need frequent monitoring"""
        
        priority_vehicles = []
        
        for vehicle_id, decisions in self.active_decisions.items():
            # Vehicles with critical or high urgency decisions
            if any(d.urgency in [MaintenanceUrgency.CRITICAL, MaintenanceUrgency.HIGH] for d in decisions):
                priority_vehicles.append(vehicle_id)
        
        # Add vehicles in monitoring list
        priority_vehicles.extend(self.monitoring_vehicles)
        
        return list(set(priority_vehicles))
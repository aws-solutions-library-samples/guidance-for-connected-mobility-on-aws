"""
Cost Optimizer

Analyzes maintenance costs and optimizes decisions based on:
- Direct maintenance costs vs failure costs
- Fleet-wide resource optimization
- Parts inventory and availability
- Service center capacity and pricing
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CostAnalysis:
    """Cost analysis results"""
    maintenance_cost: float
    failure_cost: float
    cost_benefit_ratio: float
    expected_savings: float
    total_cost: float
    parts_cost: float
    labor_cost: float
    downtime_cost: float


class CostOptimizer:
    """
    Optimizes maintenance decisions based on cost-benefit analysis
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Cost parameters
        self.labor_rates = config.get('labor_rates', {
            'standard': 100.0,  # $/hour
            'emergency': 150.0,  # $/hour
            'weekend': 125.0    # $/hour
        })
        
        self.parts_costs = config.get('parts_costs', {
            'tire_fl': 200.0,
            'tire_fr': 200.0,
            'tire_rl': 200.0,
            'tire_rr': 200.0,
            'brake_pads_front': 150.0,
            'brake_pads_rear': 120.0,
            'brake_rotors_front': 300.0,
            'brake_rotors_rear': 250.0,
            'engine_oil': 50.0,
            'oil_filter': 25.0,
            'air_filter': 30.0,
            'battery_12v': 150.0,
            'battery_ev': 8000.0
        })
        
        self.failure_costs = config.get('failure_costs', {
            'tire': {
                'minor': 500.0,    # Flat tire, towing
                'major': 2000.0,   # Blowout, accident
                'critical': 10000.0 # Serious accident
            },
            'brake': {
                'minor': 800.0,    # Reduced braking
                'major': 3000.0,   # Brake failure
                'critical': 15000.0 # Accident
            },
            'engine': {
                'minor': 1000.0,   # Performance loss
                'major': 5000.0,   # Engine damage
                'critical': 12000.0 # Complete failure
            },
            'battery': {
                'minor': 200.0,    # Reduced range
                'major': 1000.0,   # Stranded vehicle
                'critical': 3000.0  # Complete failure
            }
        })
        
        self.downtime_costs = config.get('downtime_costs', {
            'personal': 50.0,      # $/hour for personal vehicles
            'commercial': 200.0,   # $/hour for commercial vehicles
            'emergency': 500.0     # $/hour for emergency vehicles
        })
        
        logger.info("Cost Optimizer initialized")
    
    async def analyze_maintenance_cost(
        self, 
        vehicle_context: Any, 
        component_type: Any, 
        prediction: Dict[str, Any], 
        urgency: Any
    ) -> Dict[str, Any]:
        """
        Analyze cost-benefit of maintenance decision
        """
        
        try:
            # Calculate direct maintenance costs
            maintenance_cost = self._calculate_maintenance_cost(
                component_type, prediction, urgency, vehicle_context
            )
            
            # Calculate potential failure costs
            failure_cost = self._calculate_failure_cost(
                component_type, prediction, vehicle_context
            )
            
            # Calculate downtime costs
            downtime_cost = self._calculate_downtime_cost(
                vehicle_context, prediction.get('service_time_hours', 2.0)
            )
            
            # Calculate expected savings
            failure_probability = prediction.get('failure_probability', 0.0)
            expected_savings = failure_cost * failure_probability
            
            # Calculate cost-benefit ratio
            total_cost = maintenance_cost + downtime_cost
            cost_benefit_ratio = expected_savings / total_cost if total_cost > 0 else 0
            
            return {
                'maintenance_cost': maintenance_cost,
                'failure_cost': failure_cost,
                'cost_benefit_ratio': cost_benefit_ratio,
                'expected_savings': expected_savings,
                'total_cost': total_cost,
                'parts_cost': self._calculate_parts_cost(prediction.get('parts_needed', [])),
                'labor_cost': self._calculate_labor_cost(prediction.get('service_time_hours', 2.0), urgency),
                'downtime_cost': downtime_cost,
                'recommendation': self._generate_cost_recommendation(cost_benefit_ratio, expected_savings, total_cost)
            }
            
        except Exception as e:
            logger.error(f"Error in cost analysis: {str(e)}")
            return {
                'maintenance_cost': 0.0,
                'failure_cost': 0.0,
                'cost_benefit_ratio': 0.0,
                'expected_savings': 0.0,
                'total_cost': 0.0,
                'error': str(e)
            }
    
    def _calculate_maintenance_cost(self, component_type: Any, prediction: Dict, urgency: Any, context: Any) -> float:
        """Calculate direct maintenance cost"""
        
        # Parts cost
        parts_needed = prediction.get('parts_needed', [])
        parts_cost = self._calculate_parts_cost(parts_needed)
        
        # Labor cost
        service_time = prediction.get('service_time_hours', 2.0)
        labor_cost = self._calculate_labor_cost(service_time, urgency)
        
        # Additional costs based on urgency
        urgency_multiplier = {
            'critical': 1.5,  # Emergency service premium
            'high': 1.2,      # Expedited service
            'medium': 1.0,    # Standard service
            'low': 0.9,       # Scheduled service discount
            'monitor': 0.8    # Preventive service discount
        }.get(urgency.value if hasattr(urgency, 'value') else str(urgency), 1.0)
        
        base_cost = parts_cost + labor_cost
        total_cost = base_cost * urgency_multiplier
        
        return total_cost
    
    def _calculate_parts_cost(self, parts_needed: List[str]) -> float:
        """Calculate cost of required parts"""
        
        total_cost = 0.0
        
        for part in parts_needed:
            part_cost = self.parts_costs.get(part, 100.0)  # Default cost
            total_cost += part_cost
        
        return total_cost
    
    def _calculate_labor_cost(self, service_time_hours: float, urgency: Any) -> float:
        """Calculate labor cost based on service time and urgency"""
        
        urgency_str = urgency.value if hasattr(urgency, 'value') else str(urgency)
        
        if urgency_str == 'critical':
            rate = self.labor_rates['emergency']
        elif datetime.now().weekday() >= 5:  # Weekend
            rate = self.labor_rates['weekend']
        else:
            rate = self.labor_rates['standard']
        
        return service_time_hours * rate
    
    def _calculate_failure_cost(self, component_type: Any, prediction: Dict, context: Any) -> float:
        """Calculate potential cost of component failure"""
        
        component_str = component_type.value if hasattr(component_type, 'value') else str(component_type)
        failure_mode = prediction.get('failure_mode', 'minor')
        
        # Determine failure severity
        if 'critical' in failure_mode or prediction.get('failure_probability', 0) >= 0.9:
            severity = 'critical'
        elif 'major' in failure_mode or prediction.get('failure_probability', 0) >= 0.7:
            severity = 'major'
        else:
            severity = 'minor'
        
        # Get base failure cost
        component_costs = self.failure_costs.get(component_str, {'minor': 1000, 'major': 3000, 'critical': 8000})
        base_cost = component_costs.get(severity, 1000)
        
        # Adjust for vehicle usage pattern
        usage_pattern = getattr(context, 'usage_pattern', 'mixed')
        usage_multiplier = {
            'commercial': 2.0,    # Higher impact for commercial vehicles
            'emergency': 3.0,     # Critical for emergency vehicles
            'personal': 1.0,      # Standard impact
            'mixed': 1.2          # Slightly higher than personal
        }.get(usage_pattern, 1.0)
        
        return base_cost * usage_multiplier
    
    def _calculate_downtime_cost(self, context: Any, service_time_hours: float) -> float:
        """Calculate cost of vehicle downtime during service"""
        
        usage_pattern = getattr(context, 'usage_pattern', 'personal')
        
        hourly_rate = self.downtime_costs.get(usage_pattern, 50.0)
        
        return service_time_hours * hourly_rate
    
    def _generate_cost_recommendation(self, cost_benefit_ratio: float, expected_savings: float, total_cost: float) -> str:
        """Generate cost-based recommendation"""
        
        if cost_benefit_ratio >= 3.0:
            return f"Highly cost-effective: Expected savings ${expected_savings:.0f} vs cost ${total_cost:.0f}"
        elif cost_benefit_ratio >= 2.0:
            return f"Cost-effective: Expected savings ${expected_savings:.0f} vs cost ${total_cost:.0f}"
        elif cost_benefit_ratio >= 1.0:
            return f"Marginally cost-effective: Expected savings ${expected_savings:.0f} vs cost ${total_cost:.0f}"
        else:
            return f"Not cost-effective: Expected savings ${expected_savings:.0f} vs cost ${total_cost:.0f}"
    
    async def optimize_fleet_costs(self, fleet_schedule: Dict[str, List]) -> Dict[str, Any]:
        """
        Optimize costs across the entire fleet
        """
        
        try:
            optimization_results = {
                'total_vehicles': len(fleet_schedule),
                'total_estimated_cost': 0.0,
                'total_expected_savings': 0.0,
                'cost_optimizations': []
            }
            
            # Analyze bulk purchasing opportunities
            all_parts = []
            for vehicle_slots in fleet_schedule.values():
                for slot in vehicle_slots:
                    all_parts.extend(slot.components)
            
            bulk_savings = self._calculate_bulk_purchase_savings(all_parts)
            optimization_results['bulk_purchase_savings'] = bulk_savings
            
            # Analyze service center load balancing
            center_utilization = self._analyze_service_center_utilization(fleet_schedule)
            optimization_results['service_center_utilization'] = center_utilization
            
            # Calculate total costs and savings
            for vehicle_id, slots in fleet_schedule.items():
                for slot in slots:
                    optimization_results['total_estimated_cost'] += slot.total_cost
            
            optimization_results['total_expected_savings'] = bulk_savings
            optimization_results['net_cost'] = optimization_results['total_estimated_cost'] - bulk_savings
            
            return optimization_results
            
        except Exception as e:
            logger.error(f"Error in fleet cost optimization: {str(e)}")
            return {'error': str(e)}
    
    def _calculate_bulk_purchase_savings(self, all_parts: List[str]) -> float:
        """Calculate savings from bulk parts purchasing"""
        
        # Count part quantities
        part_counts = {}
        for part in all_parts:
            part_counts[part] = part_counts.get(part, 0) + 1
        
        total_savings = 0.0
        
        for part, quantity in part_counts.items():
            base_cost = self.parts_costs.get(part, 100.0)
            
            # Apply bulk discount tiers
            if quantity >= 10:
                discount = 0.15  # 15% discount for 10+
            elif quantity >= 5:
                discount = 0.10  # 10% discount for 5+
            elif quantity >= 3:
                discount = 0.05  # 5% discount for 3+
            else:
                discount = 0.0
            
            savings = base_cost * quantity * discount
            total_savings += savings
        
        return total_savings
    
    def _analyze_service_center_utilization(self, fleet_schedule: Dict) -> Dict[str, Any]:
        """Analyze service center capacity utilization"""
        
        center_stats = {}
        
        for vehicle_slots in fleet_schedule.values():
            for slot in vehicle_slots:
                center_id = slot.service_center_id
                
                if center_id not in center_stats:
                    center_stats[center_id] = {
                        'scheduled_slots': 0,
                        'total_hours': 0.0,
                        'total_cost': 0.0
                    }
                
                center_stats[center_id]['scheduled_slots'] += 1
                center_stats[center_id]['total_hours'] += slot.estimated_duration.total_seconds() / 3600
                center_stats[center_id]['total_cost'] += slot.total_cost
        
        return center_stats
    
    def get_cost_metrics(self) -> Dict[str, Any]:
        """Get cost optimization metrics"""
        
        return {
            'labor_rates': self.labor_rates,
            'average_parts_cost': sum(self.parts_costs.values()) / len(self.parts_costs),
            'cost_categories': {
                'parts': len(self.parts_costs),
                'labor_rates': len(self.labor_rates),
                'failure_scenarios': sum(len(v) for v in self.failure_costs.values())
            }
        }
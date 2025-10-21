"""
Maintenance Scheduler

Intelligent scheduling system that optimizes maintenance timing based on:
- Vehicle usage patterns
- Service center capacity
- Parts availability
- Cost optimization
- Fleet coordination
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SchedulingStrategy(Enum):
    MINIMIZE_COST = "minimize_cost"
    MINIMIZE_DOWNTIME = "minimize_downtime"
    BALANCE_WORKLOAD = "balance_workload"
    EMERGENCY_ONLY = "emergency_only"


@dataclass
class ServiceCenter:
    """Service center information"""
    center_id: str
    name: str
    location: Dict[str, float]  # lat, lon
    capacity_per_day: int
    specialties: List[str]
    cost_multiplier: float
    current_bookings: int
    next_available: datetime


@dataclass
class MaintenanceSlot:
    """Scheduled maintenance slot"""
    vehicle_id: str
    service_center_id: str
    scheduled_date: datetime
    estimated_duration: timedelta
    components: List[str]
    total_cost: float
    urgency_score: float


class MaintenanceScheduler:
    """
    Intelligent maintenance scheduling system
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.service_centers: Dict[str, ServiceCenter] = {}
        self.scheduled_slots: List[MaintenanceSlot] = []
        self.strategy = SchedulingStrategy(config.get('strategy', 'balance_workload'))
        
        # Load service centers
        self._load_service_centers()
    
    def _load_service_centers(self):
        """Load service center information"""
        # In production, this would load from database
        centers_config = self.config.get('service_centers', [])
        
        for center_config in centers_config:
            center = ServiceCenter(
                center_id=center_config['id'],
                name=center_config['name'],
                location=center_config['location'],
                capacity_per_day=center_config.get('capacity', 10),
                specialties=center_config.get('specialties', []),
                cost_multiplier=center_config.get('cost_multiplier', 1.0),
                current_bookings=0,
                next_available=datetime.utcnow()
            )
            self.service_centers[center.center_id] = center
    
    async def schedule_maintenance(
        self, 
        vehicle_id: str,
        component_type: str,
        urgency: str,
        vehicle_location: Dict[str, float],
        estimated_duration: float,
        parts_needed: List[str]
    ) -> Optional[MaintenanceSlot]:
        """
        Schedule maintenance for a vehicle
        """
        
        # Find suitable service centers
        suitable_centers = self._find_suitable_centers(
            component_type, 
            vehicle_location,
            parts_needed
        )
        
        if not suitable_centers:
            logger.warning(f"No suitable service centers found for {vehicle_id}")
            return None
        
        # Calculate optimal scheduling
        best_slot = await self._optimize_scheduling(
            vehicle_id,
            suitable_centers,
            urgency,
            estimated_duration,
            parts_needed
        )
        
        if best_slot:
            self.scheduled_slots.append(best_slot)
            self._update_service_center_capacity(best_slot)
            logger.info(f"Scheduled maintenance for {vehicle_id} at {best_slot.service_center_id}")
        
        return best_slot
    
    def _find_suitable_centers(
        self, 
        component_type: str, 
        vehicle_location: Dict[str, float],
        parts_needed: List[str]
    ) -> List[ServiceCenter]:
        """Find service centers capable of handling the maintenance"""
        
        suitable = []
        
        for center in self.service_centers.values():
            # Check if center has required specialties
            if component_type in center.specialties or 'general' in center.specialties:
                # Calculate distance
                distance = self._calculate_distance(
                    vehicle_location, 
                    center.location
                )
                
                # Only consider centers within reasonable distance
                max_distance = self.config.get('max_service_distance_km', 100)
                if distance <= max_distance:
                    suitable.append(center)
        
        # Sort by distance and availability
        suitable.sort(key=lambda c: (
            self._calculate_distance(vehicle_location, c.location),
            c.current_bookings / c.capacity_per_day
        ))
        
        return suitable
    
    async def _optimize_scheduling(
        self,
        vehicle_id: str,
        centers: List[ServiceCenter],
        urgency: str,
        duration: float,
        parts_needed: List[str]
    ) -> Optional[MaintenanceSlot]:
        """
        Optimize scheduling based on strategy
        """
        
        best_slot = None
        best_score = float('inf')
        
        for center in centers:
            # Find next available slot
            available_date = self._find_next_available_slot(center, duration)
            
            if not available_date:
                continue
            
            # Calculate cost
            base_cost = self._estimate_maintenance_cost(parts_needed, duration)
            total_cost = base_cost * center.cost_multiplier
            
            # Calculate scheduling score based on strategy
            score = self._calculate_scheduling_score(
                center, available_date, total_cost, urgency
            )
            
            if score < best_score:
                best_score = score
                best_slot = MaintenanceSlot(
                    vehicle_id=vehicle_id,
                    service_center_id=center.center_id,
                    scheduled_date=available_date,
                    estimated_duration=timedelta(hours=duration),
                    components=[],  # Will be filled by caller
                    total_cost=total_cost,
                    urgency_score=self._urgency_to_score(urgency)
                )
        
        return best_slot
    
    def _find_next_available_slot(self, center: ServiceCenter, duration: float) -> Optional[datetime]:
        """Find next available time slot at service center"""
        
        # Start from next available time
        current_time = max(center.next_available, datetime.utcnow())
        
        # Check each day for availability
        for days_ahead in range(30):  # Look up to 30 days ahead
            check_date = current_time + timedelta(days=days_ahead)
            
            # Skip weekends if center doesn't work weekends
            if check_date.weekday() >= 5 and not self.config.get('weekend_service', False):
                continue
            
            # Check if center has capacity this day
            daily_bookings = self._get_daily_bookings(center.center_id, check_date)
            if daily_bookings < center.capacity_per_day:
                return check_date.replace(hour=8, minute=0, second=0, microsecond=0)
        
        return None
    
    def _calculate_scheduling_score(
        self, 
        center: ServiceCenter, 
        scheduled_date: datetime, 
        cost: float, 
        urgency: str
    ) -> float:
        """Calculate scheduling score based on strategy"""
        
        days_delay = (scheduled_date - datetime.utcnow()).days
        urgency_multiplier = self._urgency_to_score(urgency)
        
        if self.strategy == SchedulingStrategy.MINIMIZE_COST:
            return cost + (days_delay * urgency_multiplier * 10)
        
        elif self.strategy == SchedulingStrategy.MINIMIZE_DOWNTIME:
            return days_delay * urgency_multiplier * 100 + cost * 0.1
        
        elif self.strategy == SchedulingStrategy.BALANCE_WORKLOAD:
            workload_factor = center.current_bookings / center.capacity_per_day
            return cost * 0.3 + days_delay * urgency_multiplier * 20 + workload_factor * 50
        
        else:  # EMERGENCY_ONLY
            if urgency == 'critical':
                return days_delay * 1000
            else:
                return float('inf')  # Don't schedule non-critical
    
    def _urgency_to_score(self, urgency: str) -> float:
        """Convert urgency to numerical score"""
        urgency_scores = {
            'critical': 10.0,
            'high': 5.0,
            'medium': 2.0,
            'low': 1.0,
            'monitor': 0.1
        }
        return urgency_scores.get(urgency, 1.0)
    
    def _calculate_distance(self, loc1: Dict[str, float], loc2: Dict[str, float]) -> float:
        """Calculate distance between two locations (simplified)"""
        # Simplified distance calculation - in production use proper geospatial calculation
        lat_diff = abs(loc1.get('latitude', 0) - loc2.get('latitude', 0))
        lon_diff = abs(loc1.get('longitude', 0) - loc2.get('longitude', 0))
        return ((lat_diff ** 2 + lon_diff ** 2) ** 0.5) * 111  # Rough km conversion
    
    def _estimate_maintenance_cost(self, parts_needed: List[str], duration: float) -> float:
        """Estimate maintenance cost"""
        # Base labor cost
        labor_rate = self.config.get('labor_rate_per_hour', 100)
        labor_cost = duration * labor_rate
        
        # Parts cost (simplified)
        parts_cost = len(parts_needed) * self.config.get('average_part_cost', 150)
        
        return labor_cost + parts_cost
    
    def _get_daily_bookings(self, center_id: str, date: datetime) -> int:
        """Get number of bookings for a center on a specific date"""
        count = 0
        for slot in self.scheduled_slots:
            if (slot.service_center_id == center_id and 
                slot.scheduled_date.date() == date.date()):
                count += 1
        return count
    
    def _update_service_center_capacity(self, slot: MaintenanceSlot):
        """Update service center capacity after scheduling"""
        center = self.service_centers.get(slot.service_center_id)
        if center:
            center.current_bookings += 1
            # Update next available time if needed
            if slot.scheduled_date > center.next_available:
                center.next_available = slot.scheduled_date + slot.estimated_duration
    
    async def optimize_fleet_schedule(self, fleet_decisions: Dict) -> Dict[str, List[MaintenanceSlot]]:
        """
        Optimize maintenance scheduling across entire fleet
        """
        
        all_slots = {}
        
        # Group decisions by urgency
        critical_decisions = []
        high_decisions = []
        medium_decisions = []
        low_decisions = []
        
        for vehicle_id, decisions in fleet_decisions.items():
            for decision in decisions:
                decision_data = {
                    'vehicle_id': vehicle_id,
                    'decision': decision
                }
                
                if decision.urgency.value == 'critical':
                    critical_decisions.append(decision_data)
                elif decision.urgency.value == 'high':
                    high_decisions.append(decision_data)
                elif decision.urgency.value == 'medium':
                    medium_decisions.append(decision_data)
                else:
                    low_decisions.append(decision_data)
        
        # Schedule in order of urgency
        for decisions_group in [critical_decisions, high_decisions, medium_decisions, low_decisions]:
            for decision_data in decisions_group:
                vehicle_id = decision_data['vehicle_id']
                decision = decision_data['decision']
                
                # Get vehicle location (would come from CMS platform)
                vehicle_location = {'latitude': 0, 'longitude': 0}  # Placeholder
                
                slot = await self.schedule_maintenance(
                    vehicle_id=vehicle_id,
                    component_type=decision.component_type.value,
                    urgency=decision.urgency.value,
                    vehicle_location=vehicle_location,
                    estimated_duration=decision.service_time_hours,
                    parts_needed=decision.parts_needed
                )
                
                if slot:
                    if vehicle_id not in all_slots:
                        all_slots[vehicle_id] = []
                    all_slots[vehicle_id].append(slot)
        
        return all_slots
    
    def get_schedule_summary(self) -> Dict[str, Any]:
        """Get summary of current maintenance schedule"""
        
        total_slots = len(self.scheduled_slots)
        critical_slots = len([s for s in self.scheduled_slots if s.urgency_score >= 10])
        
        # Calculate average wait time
        current_time = datetime.utcnow()
        wait_times = [(s.scheduled_date - current_time).days for s in self.scheduled_slots]
        avg_wait_time = sum(wait_times) / len(wait_times) if wait_times else 0
        
        # Service center utilization
        center_utilization = {}
        for center_id, center in self.service_centers.items():
            utilization = center.current_bookings / center.capacity_per_day
            center_utilization[center_id] = utilization
        
        return {
            'total_scheduled': total_slots,
            'critical_maintenance': critical_slots,
            'average_wait_days': round(avg_wait_time, 1),
            'service_center_utilization': center_utilization,
            'next_7_days': len([s for s in self.scheduled_slots 
                              if (s.scheduled_date - current_time).days <= 7])
        }
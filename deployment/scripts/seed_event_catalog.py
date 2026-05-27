#!/usr/bin/env python3
"""
Seed Event Catalog — populates cms-{stage}-event-catalog DynamoDB table.

Usage:
    python3 seed_event_catalog.py
    AWS_PROFILE=myprofile DEPLOYMENT_STAGE=prod python3 seed_event_catalog.py
"""
import boto3
import os
import time
from decimal import Decimal

PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
REGION = os.environ.get('AWS_REGION', 'us-east-1')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')
TABLE = f'cms-{STAGE}-event-catalog'

# ── Event Catalog Definitions ──────────────────────────────────────────────
EVENTS = [
    # ── Safety: Edge-detected (condition-based campaigns) ──────────────
    {
        'event_id': 'safety.harsh_braking',
        'category': 'safety',
        'description': 'Sudden hard braking event',
        'severity': Decimal('1'),
        'trigger_signal': 'HarshBraking',
        'signal_id': Decimal('40'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('0.4'),
        'json_fields': ['deceleration'],
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-harsh-braking',
    },
    {
        'event_id': 'safety.harsh_acceleration',
        'category': 'safety',
        'description': 'Sudden hard acceleration event',
        'severity': Decimal('1'),
        'trigger_signal': 'HarshAcceleration',
        'signal_id': Decimal('41'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('0.35'),
        'json_fields': ['acceleration'],
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-harsh-accel',
    },
    {
        'event_id': 'safety.harsh_cornering',
        'category': 'safety',
        'description': 'Aggressive cornering / harsh turn',
        'severity': Decimal('1'),
        'trigger_signal': 'HarshTurn',
        'signal_id': Decimal('42'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('45'),
        'json_fields': ['harsh_turn'],
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-harsh-cornering',
    },
    {
        'event_id': 'safety.speeding',
        'category': 'safety',
        'description': 'Vehicle exceeding speed threshold',
        'severity': Decimal('2'),
        'trigger_signal': 'VehicleSpeed',
        'signal_id': Decimal('1'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('80'),
        'json_fields': ['speed'],
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-speeding',
    },
    {
        'event_id': 'safety.aeb_activation',
        'category': 'safety',
        'description': 'Automatic Emergency Braking activated',
        'severity': Decimal('3'),
        'trigger_signal': 'AEBActivation',
        'signal_id': Decimal('44'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('0'),
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-aeb-activation',
    },
    {
        'event_id': 'safety.esc_activation',
        'category': 'safety',
        'description': 'Electronic Stability Control activated',
        'severity': Decimal('2'),
        'trigger_signal': 'ESCActivation',
        'signal_id': Decimal('46'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('0'),
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-esc-activation',
    },
    {
        'event_id': 'safety.lane_departure',
        'category': 'safety',
        'description': 'Unintended lane departure detected via lateral G',
        'severity': Decimal('2'),
        'trigger_signal': 'LateralG',
        'signal_id': Decimal('76'),
        'condition_type': 'threshold',
        'threshold_operator': '>',
        'threshold_value': Decimal('5'),
        'detection': 'cloud',
        'edge_candidate': True,
        'scheme_name': 'cms-safety-lane-departure',
    },

    # ── Safety: Composite (edge trigger + cloud filter) ────────────────
    {
        'event_id': 'safety.phone_usage',
        'category': 'safety',
        'description': 'Phone usage while driving',
        'severity': Decimal('2'),
        'trigger_signal': 'PhoneUsage',
        'signal_id': Decimal('50'),
        'condition_type': 'composite',
        'detection': 'cloud',
        'edge_candidate': False,
        'duration_ms': Decimal('10000'),
        'threshold_operator': '=',
        'threshold_value': Decimal('1'),
        'json_fields': ['phone_use', 'driverPhoneUsage'],
        'composite_condition': {
            'logic': 'AND',
            'conditions': [
                {'signal': 'phone_use', 'operator': '=', 'value': Decimal('1'),
                 'json_fields': ['phone_use', 'driverPhoneUsage']},
                {'signal': 'speed', 'operator': '>', 'value': Decimal('5'),
                 'json_fields': ['speed']},
            ]
        },
    },
    {
        'event_id': 'safety.seatbelt_unfastened',
        'category': 'safety',
        'description': 'Seatbelt unfastened while vehicle in motion',
        'severity': Decimal('1'),
        'trigger_signal': 'SeatbeltViolation',
        'signal_id': Decimal('51'),
        'condition_type': 'composite',
        'detection': 'cloud',
        'edge_candidate': False,
        'threshold_operator': '=',
        'threshold_value': Decimal('1'),
        'composite_condition': {
            'logic': 'AND',
            'conditions': [
                {'signal': 'seatbelt_violation', 'operator': '=', 'value': Decimal('1'),
                 'json_fields': ['seatbelt_violation', 'SeatbeltViolation']},
                {'signal': 'speed', 'operator': '>', 'value': Decimal('5'),
                 'json_fields': ['speed']},
            ]
        },
    },
    {
        'event_id': 'safety.tailgating',
        'category': 'safety',
        'description': 'Following distance too close at speed',
        'severity': Decimal('2'),
        'trigger_signal': 'FollowingDistance',
        'signal_id': Decimal('77'),
        'condition_type': 'composite',
        'detection': 'cloud',
        'edge_candidate': False,
        'threshold_operator': '<',
        'threshold_value': Decimal('2'),
        'composite_condition': {
            'logic': 'AND',
            'conditions': [
                {'signal': 'following_distance', 'operator': '<', 'value': Decimal('2'),
                 'json_fields': ['following_distance', 'FollowingDistance']},
                {'signal': 'speed', 'operator': '>', 'value': Decimal('30'),
                 'json_fields': ['speed']},
            ]
        },
    },
    {
        'event_id': 'safety.unsafe_lane_change',
        'category': 'safety',
        'description': 'Lane change without turn signal at speed',
        'severity': Decimal('2'),
        'trigger_signal': 'LateralG',
        'signal_id': Decimal('76'),
        'condition_type': 'composite',
        'detection': 'cloud',
        'edge_candidate': False,
        'threshold_operator': '>',
        'threshold_value': Decimal('3'),
        'composite_condition': {
            'logic': 'AND',
            'conditions': [
                {'signal': 'lateral_g', 'operator': '>', 'value': Decimal('3'),
                 'json_fields': ['lateral_g', 'LateralG']},
                {'signal': 'turn_signal', 'operator': '=', 'value': Decimal('0'),
                 'json_fields': ['turn_signal', 'TurnSignalActive']},
                {'signal': 'speed', 'operator': '>', 'value': Decimal('40'),
                 'json_fields': ['speed']},
            ]
        },
    },
    {
        'event_id': 'safety.drowsy_driving',
        'category': 'safety',
        'description': 'Erratic steering pattern indicating driver fatigue',
        'severity': Decimal('3'),
        'trigger_signal': 'LateralG',
        'signal_id': Decimal('76'),
        'condition_type': 'composite',
        'detection': 'cloud',
        'edge_candidate': False,
        'duration_ms': Decimal('30000'),
        'threshold_operator': '>',
        'threshold_value': Decimal('1'),
        'composite_condition': {
            'logic': 'AND',
            'conditions': [
                {'signal': 'lateral_g', 'operator': '>', 'value': Decimal('1'),
                 'json_fields': ['lateral_g', 'LateralG']},
                {'signal': 'speed', 'operator': '>', 'value': Decimal('60'),
                 'json_fields': ['speed']},
            ]
        },
    },

    # ── Maintenance: Cloud-only ────────────────────────────────────────
    {
        'event_id': 'maintenance.low_fuel',
        'category': 'maintenance',
        'description': 'Fuel level critically low',
        'severity': Decimal('1'),
        'trigger_signal': 'FuelLevel',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('5'),
        'detection': 'cloud',
        'edge_candidate': False,
        'dtc_code': 'P0461',
    },
    {
        'event_id': 'maintenance.low_battery',
        'category': 'maintenance',
        'description': 'Battery voltage below safe threshold',
        'severity': Decimal('2'),
        'trigger_signal': 'BatteryVoltage',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('12'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.low_oil_pressure',
        'category': 'maintenance',
        'description': 'Engine oil pressure below safe threshold',
        'severity': Decimal('2'),
        'trigger_signal': 'OilPressure',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('20'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.high_engine_temp',
        'category': 'maintenance',
        'description': 'Engine temperature critically high',
        'severity': Decimal('3'),
        'trigger_signal': 'EngineTemp',
        'condition_type': 'simple',
        'threshold_operator': '>',
        'threshold_value': Decimal('110'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.tire_pressure',
        'category': 'maintenance',
        'description': 'Tire pressure below safe threshold',
        'severity': Decimal('2'),
        'trigger_signal': 'TirePressureFL',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('28'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.tire_tread_low',
        'category': 'maintenance',
        'description': 'Tire tread depth below safe minimum',
        'severity': Decimal('2'),
        'trigger_signal': 'TireTreadFL',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('3'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.brake_wear',
        'category': 'maintenance',
        'description': 'Brake pad wear exceeding threshold',
        'severity': Decimal('2'),
        'trigger_signal': 'BrakeWear',
        'condition_type': 'simple',
        'threshold_operator': '>',
        'threshold_value': Decimal('80'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.oil_life_low',
        'category': 'maintenance',
        'description': 'Oil life remaining below threshold',
        'severity': Decimal('1'),
        'trigger_signal': 'OilLife',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('10'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.filter_replacement',
        'category': 'maintenance',
        'description': 'Air filter life remaining below threshold',
        'severity': Decimal('1'),
        'trigger_signal': 'FilterLife',
        'condition_type': 'simple',
        'threshold_operator': '<',
        'threshold_value': Decimal('10'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
    {
        'event_id': 'maintenance.engine_overspeed',
        'category': 'maintenance',
        'description': 'Engine RPM exceeding safe limit',
        'severity': Decimal('3'),
        'trigger_signal': 'EngineRPM',
        'condition_type': 'simple',
        'threshold_operator': '>',
        'threshold_value': Decimal('6500'),
        'detection': 'cloud',
        'edge_candidate': False,
    },
]


def seed():
    table = dynamodb.Table(TABLE)
    print(f"Seeding {len(EVENTS)} events → {TABLE}")
    for event in EVENTS:
        table.put_item(Item=event)
        print(f"  ✅ {event['event_id']}")
    print(f"\n✅ Event catalog seeded: {len(EVENTS)} events")


if __name__ == '__main__':
    seed()

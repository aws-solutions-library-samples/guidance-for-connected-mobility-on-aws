"""
CAN Encoder — converts telemetry dict → CAN frames using DBC.
Pure encoding logic, no bus dependency. Testable anywhere.
"""

import cantools
import can
import os
from typing import Dict, List, Any

DBC_PATH = os.environ.get('DBC_PATH',
    os.path.join(os.path.dirname(__file__), 'can', 'cms-fleet.dbc'))


class CANEncoder:
    def __init__(self, dbc_path: str = None):
        self.db = cantools.database.load_file(dbc_path or DBC_PATH)
        # Build reverse lookup: json telemetry key → (message, signal)
        self._build_signal_map()

    def _build_signal_map(self):
        """Map telemetry JSON keys to DBC signals.
        Uses a convention: telemetry keys are camelCase versions of signal names,
        or explicit overrides for known mappings."""
        self.key_to_signal = {}  # telemetry_key → (message, signal_name)
        self.msg_signals = {}    # message_id → {signal_name: value}

        # Explicit mapping: telemetry JSON key → DBC signal name
        TELEMETRY_MAP = {
            'speed': 'VehicleSpeed', 'engineRPM': 'EngineRPM',
            'engineTemp': 'EngineTemp', 'ignitionOn': 'IgnitionOn',
            'oilPressure': 'OilPressure', 'engineLoad': 'EngineLoad',
            'throttlePosition': 'ThrottlePosition', 'coolantTemp': 'CoolantTemp',
            'intakeAirTemp': 'IntakeAirTemp', 'engineHours': 'EngineHoursTotal',
            'acceleration': 'Acceleration', 'deceleration': 'Deceleration',
            'odometer': 'Odometer', 'fuelRate': 'FuelRate',
            'transmissionTemp': 'TransmissionTemp', 'gear': 'GearPosition',
            'cruiseControl': 'CruiseControl', 'parkingBrake': 'ParkingBrake',
            'seatbelt': 'SeatbeltStatus', 'phoneConnected': 'PhoneConnected',
            'windowsUp': 'WindowsUp', 'trunkLocked': 'TrunkLocked',
            'alarmArmed': 'AlarmArmed', 'keylessEntry': 'KeylessEntry',
            'headlights': 'Headlights', 'hazardLights': 'HazardLights',
            'turnSignal': 'TurnSignalActive', 'wifiConnected': 'WifiConnected',
            'bluetoothDevices': 'BluetoothDevices', 'navigationActive': 'NavigationActive',
            'tire_fl': 'TirePressureFL', 'tire_fr': 'TirePressureFR',
            'tire_rl': 'TirePressureRL', 'tire_rr': 'TirePressureRR',
            'tireTempMax': 'TireTempMax',
            'tireTreadFL': 'TireTreadFL', 'tireTreadFR': 'TireTreadFR',
            'tireTreadRL': 'TireTreadRL', 'tireTreadRR': 'TireTreadRR',
            'harsh_brk': 'HarshBraking', 'harsh_acc': 'HarshAcceleration',
            'harsh_turn': 'HarshTurn', 'speed_viol': 'SpeedViolation',
            'aeb_act': 'AEBActivation', 'abs_act': 'ABSActivation',
            'esc_act': 'ESCActivation', 'airbagWarning': 'AirbagWarning',
            'tractionControl': 'TractionControl', 'stabilityControl': 'StabilityControl',
            'phone_use': 'PhoneUsage', 'seatbeltViolation': 'SeatbeltViolation',
            'lateralG': 'LateralG', 'followingDistance': 'FollowingDistance',
            'batteryVoltage': 'BatteryVoltage', 'alternatorVoltage': 'AlternatorOutput',
            'fuelLevel': 'FuelLevel',
            'isEV': 'IsEV', 'soc': 'StateOfCharge',
            'hvBatteryVoltage': 'HVBatteryVoltage', 'regenPower': 'RegenPower',
            'heading': 'Heading', 'dtcActive': 'DTCCodesActive',
            'hvacOn': 'HVACOn', 'targetTemp': 'TargetTemp',
            'cabinTemp': 'CabinTemp', 'seatHeatDriver': 'SeatHeatDriver',
            'oilLife': 'OilLife', 'brakeWear': 'BrakeWear',
            'filterLife': 'FilterLife', 'idleHours': 'IdleHoursTotal',
            'airPressure': 'AirPressure', 'hydraulicPressure': 'HydraulicPressure',
            'headlightMode': 'HeadlightMode', 'hazardActive': 'HazardActive',
            'turnSignalActive': 'TurnSignal',
        }

        # Build reverse: signal_name → message
        sig_to_msg = {}
        for message in self.db.messages:
            for signal in message.signals:
                sig_to_msg[signal.name] = message

        for telem_key, sig_name in TELEMETRY_MAP.items():
            if sig_name in sig_to_msg:
                self.key_to_signal[telem_key] = (sig_to_msg[sig_name], sig_name)

    def encode(self, telemetry: Dict[str, Any]) -> List[can.Message]:
        """Encode telemetry dict into CAN frames.
        Returns list of can.Message objects ready to send."""
        # Collect signal values grouped by CAN message
        msg_data: Dict[int, Dict[str, float]] = {}

        for telem_key, value in telemetry.items():
            if telem_key not in self.key_to_signal:
                continue
            message, sig_name = self.key_to_signal[telem_key]
            if message.frame_id not in msg_data:
                msg_data[message.frame_id] = {}

            # Convert booleans to int, clamp to signal range
            if isinstance(value, bool):
                value = int(value)
            elif isinstance(value, str):
                continue  # Skip string fields (vin, tripId, etc.)

            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            # Clamp to signal min/max
            signal = next(s for s in message.signals if s.name == sig_name)
            if signal.minimum is not None and value < signal.minimum:
                value = signal.minimum
            if signal.maximum is not None and value > signal.maximum:
                value = signal.maximum

            msg_data[message.frame_id][sig_name] = value

        # Encode each CAN message
        frames = []
        for frame_id, signals in msg_data.items():
            message = self.db.get_message_by_frame_id(frame_id)
            # Fill missing signals with defaults (0)
            all_signals = {}
            for sig in message.signals:
                all_signals[sig.name] = signals.get(sig.name, 0)
            try:
                data = message.encode(all_signals)
                frames.append(can.Message(
                    arbitration_id=frame_id,
                    data=data,
                    is_extended_id=False
                ))
            except Exception as e:
                print(f"⚠️ Failed to encode {message.name} (0x{frame_id:X}): {e}")

        return frames

    def decode(self, frame: can.Message) -> Dict[str, Any]:
        """Decode a CAN frame back to signal values (for testing)."""
        try:
            message = self.db.get_message_by_frame_id(frame.arbitration_id)
            return message.decode(frame.data)
        except Exception:
            return {}

    @property
    def message_count(self):
        return len(self.db.messages)

    @property
    def signal_count(self):
        return sum(len(m.signals) for m in self.db.messages)

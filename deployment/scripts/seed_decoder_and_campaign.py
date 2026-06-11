#!/usr/bin/env python3
"""
Seed decoder manifest and default campaign for FWE checkin sync.
Run after deploying the storage stack.

Usage:
    python3 seed_decoder_and_campaign.py
    AWS_PROFILE=myprofile DEPLOYMENT_STAGE=prod python3 seed_decoder_and_campaign.py
"""
import boto3
import json
import os
import base64
import time

try:
    import zstandard
except ImportError:
    print("Installing zstandard...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "zstandard", "-q"])
    import zstandard

PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
REGION = os.environ.get('AWS_REGION', 'us-east-1')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')
s3 = session.client('s3')

DECODER_TABLE = f'cms-{STAGE}-decoder-manifest'
DECODER_NAME = 'cms-fleet-v3'
DECODER_VERSION = '1'
CAMPAIGN_NAME = 'cms-fleet-telemetry-30s'
ACCOUNT = session.client('sts').get_caller_identity()['Account']
CAMPAIGN_BUCKET = f'cms-{STAGE}-transform-manifests-{REGION}-{ACCOUNT}'

# ── CAN Signal Definitions — loaded from DBC (single source of truth) ─────
DBC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'services', 'simulation', 'can', 'cms-fleet.dbc')

try:
    import cantools
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "cantools", "-q"])
    import cantools

# Map DBC signal names → VSS-style fully qualified names
_SIGNAL_FQN = {
    'VehicleSpeed': 'Vehicle.Speed', 'EngineRPM': 'Vehicle.Powertrain.Engine.RPM',
    'EngineTemp': 'Vehicle.Powertrain.Engine.Temperature', 'IgnitionOn': 'Vehicle.Powertrain.IgnitionOn',
    'OilPressure': 'Vehicle.Powertrain.Engine.OilPressure', 'EngineLoad': 'Vehicle.Powertrain.Engine.Load',
    'ThrottlePosition': 'Vehicle.Powertrain.Engine.ThrottlePosition',
    'CoolantTemp': 'Vehicle.Powertrain.Engine.CoolantTemp',
    'IntakeAirTemp': 'Vehicle.Powertrain.Engine.IntakeAirTemp',
    'EngineHoursTotal': 'Vehicle.Powertrain.Engine.EngineHoursTotal',
    'Acceleration': 'Vehicle.Performance.Acceleration', 'Deceleration': 'Vehicle.Performance.Deceleration',
    'Odometer': 'Vehicle.Powertrain.Odometer', 'FuelRate': 'Vehicle.Powertrain.Engine.FuelRate',
    'TransmissionTemp': 'Vehicle.Powertrain.Transmission.Temperature',
    'GearPosition': 'Vehicle.Powertrain.Transmission.Gear',
    'CruiseControl': 'Vehicle.ADAS.CruiseControl.Active',
    'ParkingBrake': 'Vehicle.Powertrain.Transmission.ParkingBrake',
    'SeatbeltStatus': 'Vehicle.Body.Seatbelt.Status', 'PhoneConnected': 'Vehicle.Body.PhoneConnected',
    'WindowsUp': 'Vehicle.Body.WindowsUp', 'TrunkLocked': 'Vehicle.Body.TrunkLocked',
    'AlarmArmed': 'Vehicle.Body.Alarm.Armed', 'KeylessEntry': 'Vehicle.Body.KeylessEntry.Active',
    'Headlights': 'Vehicle.Body.Headlights.Mode', 'HazardLights': 'Vehicle.Body.HazardLights.Active',
    'TurnSignalActive': 'Vehicle.Body.TurnSignal.Active',
    'WifiConnected': 'Vehicle.Communication.WiFi.Connected',
    'BluetoothDevices': 'Vehicle.Communication.Bluetooth.Devices',
    'NavigationActive': 'Vehicle.Body.Navigation.Active',
    'TirePressureFL': 'Vehicle.Tires.FL.Pressure', 'TirePressureFR': 'Vehicle.Tires.FR.Pressure',
    'TirePressureRL': 'Vehicle.Tires.RL.Pressure', 'TirePressureRR': 'Vehicle.Tires.RR.Pressure',
    'TireTempMax': 'Vehicle.Tires.MaxTemp',
    'TireTreadFL': 'Vehicle.Tires.FL.TreadDepth', 'TireTreadFR': 'Vehicle.Tires.FR.TreadDepth',
    'TireTreadRL': 'Vehicle.Tires.RL.TreadDepth', 'TireTreadRR': 'Vehicle.Tires.RR.TreadDepth',
    'HarshBraking': 'Vehicle.Performance.HarshBraking',
    'HarshAcceleration': 'Vehicle.Performance.HarshAcceleration',
    'HarshTurn': 'Vehicle.Performance.HarshTurn', 'SpeedViolation': 'Vehicle.Performance.SpeedViolation',
    'AEBActivation': 'Vehicle.ADAS.AEB.Active', 'ABSActivation': 'Vehicle.ADAS.ABS.Active',
    'ESCActivation': 'Vehicle.ADAS.ESC.Active', 'AirbagWarning': 'Vehicle.Driver.AirbagWarning',
    'TractionControl': 'Vehicle.ADAS.TractionControl.Active',
    'StabilityControl': 'Vehicle.Chassis.StabilityControl.Active',
    'PhoneUsage': 'Vehicle.Driver.PhoneUsage', 'SeatbeltViolation': 'Vehicle.Driver.SeatbeltViolation',
    'LateralG': 'Vehicle.Performance.LateralG', 'FollowingDistance': 'Vehicle.Safety.FollowingDistance',
    'BatteryVoltage': 'Vehicle.Powertrain.Battery.Voltage',
    'AlternatorOutput': 'Vehicle.Powertrain.Battery.AlternatorVoltage',
    'FuelLevel': 'Vehicle.Powertrain.FuelLevel',
    'IsEV': 'Vehicle.Powertrain.EV.IsEV', 'StateOfCharge': 'Vehicle.Powertrain.EV.StateOfCharge',
    'HVBatteryVoltage': 'Vehicle.Powertrain.EV.HVBatteryVoltage',
    'RegenPower': 'Vehicle.Powertrain.EV.RegenPower',
    'Heading': 'Vehicle.Navigation.Heading', 'DTCCodesActive': 'Vehicle.Diagnostics.DTC.Active',
    'OilLife': 'Vehicle.Maintenance.OilLife', 'BrakeWear': 'Vehicle.Maintenance.BrakeWear',
    'FilterLife': 'Vehicle.Maintenance.FilterLife', 'IdleHoursTotal': 'Vehicle.Maintenance.IdleHoursTotal',
    'AirPressure': 'Vehicle.Maintenance.AirPressure',
    'HydraulicPressure': 'Vehicle.Maintenance.HydraulicPressure',
    'HVACOn': 'Vehicle.HVAC.On', 'TargetTemp': 'Vehicle.HVAC.TargetTemp',
    'CabinTemp': 'Vehicle.HVAC.CabinTemp', 'SeatHeatDriver': 'Vehicle.HVAC.SeatHeatDriver',
    'HeadlightMode': 'Vehicle.Lighting.Headlights.Mode',
    'HazardActive': 'Vehicle.Lighting.Hazard.Active', 'TurnSignal': 'Vehicle.Lighting.TurnSignal',
    'ACCIsActive': 'Vehicle.ADAS.ACC.IsActive',
    'ACCTargetDistance': 'Vehicle.ADAS.ACC.TargetDistance',
    'AEBIsActive': 'Vehicle.ADAS.AEB.IsActive',
    'AEBIsEngaged': 'Vehicle.ADAS.AEB.IsEngaged',
    'LeftIsWarning': 'Vehicle.ADAS.BSD.Left.IsWarning',
    'RightIsWarning': 'Vehicle.ADAS.BSD.Right.IsWarning',
    'CruiseControlIsActive': 'Vehicle.ADAS.CruiseControl.IsActive',
    'CruiseControlSpeedSet': 'Vehicle.ADAS.CruiseControl.SpeedSet',
    'DriverMonitoringAttentionLevel': 'Vehicle.ADAS.DriverMonitoring.AttentionLevel',
    'DriverMonitoringIsDrowsy': 'Vehicle.ADAS.DriverMonitoring.IsDrowsy',
    'FCWIsWarning': 'Vehicle.ADAS.FCW.IsWarning',
    'ADASFollowingDistance': 'Vehicle.ADAS.FollowingDistance',
    'LaneDepartureDetectionIsActive': 'Vehicle.ADAS.LaneDepartureDetection.IsActive',
    'LaneDepartureDetectionIsWarning': 'Vehicle.ADAS.LaneDepartureDetection.IsWarning',
    'FrontDistance': 'Vehicle.ADAS.ParkingAssist.Front.Distance',
    'ParkingAssistIsActive': 'Vehicle.ADAS.ParkingAssist.IsActive',
    'RearDistance': 'Vehicle.ADAS.ParkingAssist.Rear.Distance',
    'SafetyHarshAcceleration': 'Vehicle.ADAS.Safety.HarshAcceleration',
    'SafetyHarshBraking': 'Vehicle.ADAS.Safety.HarshBraking',
    'SafetyHarshTurn': 'Vehicle.ADAS.Safety.HarshTurn',
    'SafetySpeedViolation': 'Vehicle.ADAS.Safety.SpeedViolation',
    'ADASSpeedLimit': 'Vehicle.ADAS.SpeedLimit',
    'StabilityControlActive': 'Vehicle.ADAS.StabilityControl.Active',
    'TSRRecognizedSign': 'Vehicle.ADAS.TSR.RecognizedSign',
    'TSRSpeedLimit': 'Vehicle.ADAS.TSR.SpeedLimit',
    'Acceleration2': 'Vehicle.Acceleration',
    'AccelerationLateral': 'Vehicle.Acceleration.Lateral',
    'AlarmIsTriggered': 'Vehicle.Body.Alarm.IsTriggered',
    'AlarmPanicMode': 'Vehicle.Body.Alarm.PanicMode',
    'ChargeDoorIsOpen': 'Vehicle.Body.ChargeDoor.IsOpen',
    'FuelDoorIsOpen': 'Vehicle.Body.FuelDoor.IsOpen',
    'HoodIsOpen': 'Vehicle.Body.Hood.IsOpen',
    'HornActive': 'Vehicle.Body.Horn.Active',
    'KeylessEntryProximity': 'Vehicle.Body.KeylessEntry.Proximity',
    'HighIsOn': 'Vehicle.Body.Lights.Beam.High.IsOn',
    'FrontIsOn': 'Vehicle.Body.Lights.Fog.Front.IsOn',
    'RearIsOn': 'Vehicle.Body.Lights.Fog.Rear.IsOn',
    'LightsHazardActive': 'Vehicle.Body.Lights.Hazard.Active',
    'HazardIsSignaling': 'Vehicle.Body.Lights.Hazard.IsSignaling',
    'HeadlightsMode': 'Vehicle.Body.Lights.Headlights.Mode',
    'LightsTurnSignalActive': 'Vehicle.Body.Lights.TurnSignal.Active',
    'MirrorsAllFolded': 'Vehicle.Body.Mirrors.AllFolded',
    'LeftIsFolded': 'Vehicle.Body.Mirrors.Left.IsFolded',
    'LeftIsHeatingOn': 'Vehicle.Body.Mirrors.Left.IsHeatingOn',
    'RightIsFolded': 'Vehicle.Body.Mirrors.Right.IsFolded',
    'RightIsHeatingOn': 'Vehicle.Body.Mirrors.Right.IsHeatingOn',
    'SunroofPosition': 'Vehicle.Body.Sunroof.Position',
    'ShadePosition': 'Vehicle.Body.Sunroof.Shade.Position',
    'BodyTrunkLocked': 'Vehicle.Body.Trunk.Locked',
    'RearIsLocked': 'Vehicle.Body.Trunk.Rear.IsLocked',
    'RearIsOpen': 'Vehicle.Body.Trunk.Rear.IsOpen',
    'WasherFluidLevel': 'Vehicle.Body.Windshield.Front.WasherFluid.Level',
    'WipingIsWiping': 'Vehicle.Body.Windshield.Front.Wiping.IsWiping',
    'WipingMode': 'Vehicle.Body.Windshield.Front.Wiping.Mode',
    'RearWipingIsWiping': 'Vehicle.Body.Windshield.Rear.Wiping.IsWiping',
    'DoorAllLocked': 'Vehicle.Cabin.Door.AllLocked',
    'LeftIsChildLockActive': 'Vehicle.Cabin.Door.Row1.Left.IsChildLockActive',
    'LeftIsLocked': 'Vehicle.Cabin.Door.Row1.Left.IsLocked',
    'LeftIsOpen': 'Vehicle.Cabin.Door.Row1.Left.IsOpen',
    'WindowPosition': 'Vehicle.Cabin.Door.Row1.Left.Window.Position',
    'RightIsChildLockActive': 'Vehicle.Cabin.Door.Row1.Right.IsChildLockActive',
    'RightIsLocked': 'Vehicle.Cabin.Door.Row1.Right.IsLocked',
    'RightIsOpen': 'Vehicle.Cabin.Door.Row1.Right.IsOpen',
    'RightWindowPosition': 'Vehicle.Cabin.Door.Row1.Right.Window.Position',
    'Row2LeftIsChildLockActive': 'Vehicle.Cabin.Door.Row2.Left.IsChildLockActive',
    'Row2LeftIsLocked': 'Vehicle.Cabin.Door.Row2.Left.IsLocked',
    'Row2LeftIsOpen': 'Vehicle.Cabin.Door.Row2.Left.IsOpen',
    'LeftWindowPosition': 'Vehicle.Cabin.Door.Row2.Left.Window.Position',
    'Row2RightIsChildLockActive': 'Vehicle.Cabin.Door.Row2.Right.IsChildLockActive',
    'Row2RightIsLocked': 'Vehicle.Cabin.Door.Row2.Right.IsLocked',
    'Row2RightIsOpen': 'Vehicle.Cabin.Door.Row2.Right.IsOpen',
    'RightWindowPosition2': 'Vehicle.Cabin.Door.Row2.Right.Window.Position',
    'HVACActive': 'Vehicle.Cabin.HVAC.Active',
    'HVACAmbientAirTemperature': 'Vehicle.Cabin.HVAC.AmbientAirTemperature',
    'HVACCabinTemperature': 'Vehicle.Cabin.HVAC.CabinTemperature',
    'HVACIsFrontDefrosterActive': 'Vehicle.Cabin.HVAC.IsFrontDefrosterActive',
    'HVACIsRearDefrosterActive': 'Vehicle.Cabin.HVAC.IsRearDefrosterActive',
    'HVACIsRecirculationActive': 'Vehicle.Cabin.HVAC.IsRecirculationActive',
    'HVACMode': 'Vehicle.Cabin.HVAC.Mode',
    'HVACRemotePreconditioning': 'Vehicle.Cabin.HVAC.RemotePreconditioning',
    'LeftFanSpeed': 'Vehicle.Cabin.HVAC.Station.Row1.Left.FanSpeed',
    'LeftTemperature': 'Vehicle.Cabin.HVAC.Station.Row1.Left.Temperature',
    'RightTemperature': 'Vehicle.Cabin.HVAC.Station.Row1.Right.Temperature',
    'HVACTargetTemperature': 'Vehicle.Cabin.HVAC.TargetTemperature',
    'InfotainmentNavigationActive': 'Vehicle.Cabin.Infotainment.Navigation.Active',
    'InfotainmentPhoneConnected': 'Vehicle.Cabin.Infotainment.PhoneConnected',
    'AmbientLightColor': 'Vehicle.Cabin.Lights.AmbientLight.Color',
    'LightsIsGloveBoxOn': 'Vehicle.Cabin.Lights.IsGloveBoxOn',
    'DriverHeatingLevel': 'Vehicle.Cabin.Seat.Driver.HeatingLevel',
    'LeftHeating': 'Vehicle.Cabin.Seat.Row1.Left.Heating',
    'LeftVentilation': 'Vehicle.Cabin.Seat.Row1.Left.Ventilation',
    'RightHeating': 'Vehicle.Cabin.Seat.Row1.Right.Heating',
    'DriverFastened': 'Vehicle.Cabin.Seatbelt.Driver.Fastened',
    'SteeringWheelHeating': 'Vehicle.Cabin.SteeringWheel.Heating',
    'WindowsAllClosed': 'Vehicle.Cabin.Windows.AllClosed',
    'TirePressure': 'Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure',
    'TireTemperature': 'Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Temperature',
    'TireTreadDepth': 'Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.TreadDepth',
    'RightTirePressure': 'Vehicle.Chassis.Axle.Row1.Wheel.Right.Tire.Pressure',
    'RightTireTemperature': 'Vehicle.Chassis.Axle.Row1.Wheel.Right.Tire.Temperature',
    'RightTireTreadDepth': 'Vehicle.Chassis.Axle.Row1.Wheel.Right.Tire.TreadDepth',
    'LeftTirePressure': 'Vehicle.Chassis.Axle.Row2.Wheel.Left.Tire.Pressure',
    'LeftTireTemperature': 'Vehicle.Chassis.Axle.Row2.Wheel.Left.Tire.Temperature',
    'LeftTireTreadDepth': 'Vehicle.Chassis.Axle.Row2.Wheel.Left.Tire.TreadDepth',
    'RightTirePressure2': 'Vehicle.Chassis.Axle.Row2.Wheel.Right.Tire.Pressure',
    'RightTireTemperature2': 'Vehicle.Chassis.Axle.Row2.Wheel.Right.Tire.Temperature',
    'RightTireTreadDepth2': 'Vehicle.Chassis.Axle.Row2.Wheel.Right.Tire.TreadDepth',
    'BrakeAirPressure': 'Vehicle.Chassis.Brake.AirPressure',
    'BrakeHydraulicPressure': 'Vehicle.Chassis.Brake.HydraulicPressure',
    'ParkingBrakeActive': 'Vehicle.Chassis.ParkingBrake.Active',
    'TireTemperatureMax': 'Vehicle.Chassis.Tire.TemperatureMax',
    'BluetoothPairedDevices': 'Vehicle.Connectivity.Bluetooth.PairedDevices',
    'CellularNetworkType': 'Vehicle.Connectivity.Cellular.NetworkType',
    'CellularSignalStrength': 'Vehicle.Connectivity.Cellular.SignalStrength',
    'OTAIsUpdateAvailable': 'Vehicle.Connectivity.OTA.IsUpdateAvailable',
    'OTAUpdateProgress': 'Vehicle.Connectivity.OTA.UpdateProgress',
    'ConnectivitySoftwareVersion': 'Vehicle.Connectivity.SoftwareVersion',
    'WiFiConnected': 'Vehicle.Connectivity.WiFi.Connected',
    'Deceleration2': 'Vehicle.Deceleration',
    'DiagnosticsDTCActive': 'Vehicle.Diagnostics.DTCActive',
    'ExteriorAirTemperature': 'Vehicle.Exterior.AirTemperature',
    'ExteriorBarometricPressure': 'Vehicle.Exterior.BarometricPressure',
    'ExteriorHumidity': 'Vehicle.Exterior.Humidity',
    'ExteriorLightIntensity': 'Vehicle.Exterior.LightIntensity',
    'ExteriorRainIntensity': 'Vehicle.Exterior.RainIntensity',
    'CurfewEndTime': 'Vehicle.Fleet.Curfew.EndTime',
    'CurfewIsActive': 'Vehicle.Fleet.Curfew.IsActive',
    'CurfewIsViolated': 'Vehicle.Fleet.Curfew.IsViolated',
    'CurfewStartTime': 'Vehicle.Fleet.Curfew.StartTime',
    'FleetFindMyVehicle': 'Vehicle.Fleet.FindMyVehicle',
    'CenterLatitude': 'Vehicle.Fleet.Geofence.Center.Latitude',
    'CenterLongitude': 'Vehicle.Fleet.Geofence.Center.Longitude',
    'Latitude': 'Vehicle.CurrentLocation.Latitude',
    'Longitude': 'Vehicle.CurrentLocation.Longitude',
    'GeofenceIsActive': 'Vehicle.Fleet.Geofence.IsActive',
    'GeofenceIsViolated': 'Vehicle.Fleet.Geofence.IsViolated',
    'GeofenceRadius': 'Vehicle.Fleet.Geofence.Radius',
    'ImmobilizerIsActive': 'Vehicle.Fleet.Immobilizer.IsActive',
    'FleetSpeedLimit': 'Vehicle.Fleet.SpeedLimit',
    'SpeedLimitIsViolated': 'Vehicle.Fleet.SpeedLimit.IsViolated',
    'ValetModeIsActive': 'Vehicle.Fleet.ValetMode.IsActive',
    'ValetModeSpeedLimit': 'Vehicle.Fleet.ValetMode.SpeedLimit',
    'Odometer2': 'Vehicle.Odometer',
    'AlternatorVoltage': 'Vehicle.Powertrain.Alternator.Voltage',
    'BatteryHVVoltage': 'Vehicle.Powertrain.Battery.HVVoltage',
    'BatteryRegenPower': 'Vehicle.Powertrain.Battery.RegenPower',
    'BatteryStateOfCharge': 'Vehicle.Powertrain.Battery.StateOfCharge',
    'CombustionEngineCatalystTemperature': 'Vehicle.Powertrain.CombustionEngine.CatalystTemperature',
    'CombustionEngineExhaustGasTemperature': 'Vehicle.Powertrain.CombustionEngine.ExhaustGasTemperature',
    'CombustionEngineIntakeAirTemperature': 'Vehicle.Powertrain.CombustionEngine.IntakeAirTemperature',
    'CombustionEngineThrottlePosition': 'Vehicle.Powertrain.CombustionEngine.ThrottlePosition',
    'CombustionEngineTurboBoostPressure': 'Vehicle.Powertrain.CombustionEngine.TurboBoostPressure',
    'RegenerativeBrakingLevel': 'Vehicle.Powertrain.ElectricMotor.RegenerativeBraking.Level',
    'ElectricMotorSpeed': 'Vehicle.Powertrain.ElectricMotor.Speed',
    'ElectricMotorTemperature': 'Vehicle.Powertrain.ElectricMotor.Temperature',
    'ElectricMotorTorque': 'Vehicle.Powertrain.ElectricMotor.Torque',
    'EngineCoolantTemperature': 'Vehicle.Powertrain.Engine.CoolantTemperature',
    'PowertrainEngineHoursTotal': 'Vehicle.Powertrain.Engine.HoursTotal',
    'EngineIdleHoursTotal': 'Vehicle.Powertrain.Engine.IdleHoursTotal',
    'EngineIntakeAirTemperature': 'Vehicle.Powertrain.Engine.IntakeAirTemperature',
    'FuelSystemFuelRate': 'Vehicle.Powertrain.FuelSystem.FuelRate',
    'FuelSystemFuelType': 'Vehicle.Powertrain.FuelSystem.FuelType',
    'FuelSystemPressure': 'Vehicle.Powertrain.FuelSystem.Pressure',
    'PowertrainRemoteStart': 'Vehicle.Powertrain.RemoteStart',
    'RemoteStartIsActive': 'Vehicle.Powertrain.RemoteStart.IsActive',
    'ChargingChargeLimit': 'Vehicle.Powertrain.TractionBattery.Charging.ChargeLimit',
    'ChargingChargeRate': 'Vehicle.Powertrain.TractionBattery.Charging.ChargeRate',
    'ChargingChargeType': 'Vehicle.Powertrain.TractionBattery.Charging.ChargeType',
    'ChargingIsCharging': 'Vehicle.Powertrain.TractionBattery.Charging.IsCharging',
    'ChargingScheduledTime': 'Vehicle.Powertrain.TractionBattery.Charging.ScheduledTime',
    'ChargingStartStop': 'Vehicle.Powertrain.TractionBattery.Charging.StartStop',
    'ChargingTimeToComplete': 'Vehicle.Powertrain.TractionBattery.Charging.TimeToComplete',
    'TractionBatteryCurrent': 'Vehicle.Powertrain.TractionBattery.Current',
    'TractionBatteryEnergyConsumed': 'Vehicle.Powertrain.TractionBattery.EnergyConsumed',
    'TractionBatteryRange': 'Vehicle.Powertrain.TractionBattery.Range',
    'StateOfChargeCurrent': 'Vehicle.Powertrain.TractionBattery.StateOfCharge.Current',
    'TractionBatteryStateOfHealth': 'Vehicle.Powertrain.TractionBattery.StateOfHealth',
    'TemperatureAverage': 'Vehicle.Powertrain.TractionBattery.Temperature.Average',
    'TemperatureMax': 'Vehicle.Powertrain.TractionBattery.Temperature.Max',
    'TractionBatteryVoltage': 'Vehicle.Powertrain.TractionBattery.Voltage',
    'TransmissionCurrentGear': 'Vehicle.Powertrain.Transmission.CurrentGear',
    'TransmissionDriveMode': 'Vehicle.Powertrain.Transmission.DriveMode',
    'TransmissionGearPosition': 'Vehicle.Powertrain.Transmission.GearPosition',
    'TypeIsEV': 'Vehicle.Powertrain.Type.IsEV',
    'SafetyAirbagWarning': 'Vehicle.Safety.Airbag.Warning',
    'DriverPhoneUsage': 'Vehicle.Safety.Driver.PhoneUsage',
    'DriverSeatbeltViolation': 'Vehicle.Safety.Driver.SeatbeltViolation',

}

def load_can_signals():
    """Load CAN signals directly from the DBC file — single source of truth."""
    db = cantools.database.load_file(DBC_PATH)
    signals = []
    for msg in db.messages:
        for sig in msg.signals:
            fqn = _SIGNAL_FQN.get(sig.name, f'Vehicle.Unknown.{sig.name}')
            signals.append((fqn, msg.frame_id, sig.start, sig.length, sig.scale, sig.offset, sig.is_signed, False))
    return sorted(signals, key=lambda s: s[0])

CAN_SIGNALS = load_can_signals()


def seed_decoder_manifest():
    """Seed decoder manifest table with CAN signal definitions."""
    table = dynamodb.Table(DECODER_TABLE)
    pk = f"DECODER#{DECODER_NAME}#{DECODER_VERSION}"
    compressor = zstandard.ZstdCompressor()

    # Write manifest metadata
    table.put_item(Item={
        'pk': pk,
        'sk': f'DECODER#{DECODER_NAME}',
        'decoderManifestName': DECODER_NAME,
        'decoderManifestVersion': DECODER_VERSION,
        'status': 'ACTIVE',
        'modelName': 'cms-fleet-model',
        'description': f'CMS Fleet decoder manifest ({len(CAN_SIGNALS)} CAN signals)',
        'createTimestamp': time.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
        'updateTimestamp': time.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
    })

    # Write network interface
    table.put_item(Item={
        'pk': pk,
        'sk': 'NETWORK_INTERFACE#1',
        'decoderManifestName': DECODER_NAME,
        'decoderManifestVersion': DECODER_VERSION,
        'interfaceId': '1',
        'networkInterfaceType': 'CAN_INTERFACE',
        'networkInterfacePayload': json.dumps({
            'canInterfaceName': 'vcan0',
            'protocolName': 'CAN',
            'protocolVersion': '2.0A'
        }),
    })

    # Load signal catalog IDs (authoritative source)
    catalog_table = dynamodb.Table(f'cms-{STAGE}-signal-catalog')
    catalog_resp = catalog_table.scan(ProjectionExpression='vss_path, signal_id')
    catalog_ids = {item['vss_path']: int(item['signal_id']) for item in catalog_resp.get('Items', []) if 'signal_id' in item and 'vss_path' in item}
    print(f'  Signal catalog: {len(catalog_ids)} entries with signal_id')

    # Write signal decoders (signal_id from signal catalog, fallback to alphabetical index)
    sorted_signals = sorted(CAN_SIGNALS)
    for idx, (fqn, msg_id, start_bit, length, factor, offset, is_signed, is_big_endian) in enumerate(sorted_signals, 1):
        sig_id = catalog_ids.get(fqn, idx)
        can_params = {
            'messageId': msg_id,
            'startBit': start_bit,
            'length': length,
            'factor': factor,
            'offset': offset,
            'isSigned': is_signed,
            'isBigEndian': is_big_endian,
        }
        compressed = compressor.compress(json.dumps(can_params).encode())
        payload_b64 = base64.b64encode(compressed).decode()

        table.put_item(Item={
            'pk': pk,
            'sk': f'SIGNAL_DECODER#{fqn}',
            'decoderManifestName': DECODER_NAME,
            'decoderManifestVersion': DECODER_VERSION,
            'fullyQualifiedName': fqn,
            'signalId': sig_id,
            'interfaceId': '1',
            'signalDecoderType': 'CAN_SIGNAL_DECODER',
            'signalDecoderPayloadType': 'COMPRESSED_ZSTD',
            'signalDecoderPayload': payload_b64,
        })

    print(f"✅ Decoder manifest seeded: {len(CAN_SIGNALS)} CAN signals in {DECODER_TABLE}")


def seed_campaign():
    """Seed default campaign collection scheme to S3."""
    # Ensure bucket exists
    try:
        s3.head_bucket(Bucket=CAMPAIGN_BUCKET)
    except Exception:
        print(f"⚠️  Bucket {CAMPAIGN_BUCKET} not found — will be created by data-processing stack")
        return

    scheme = {
        'campaignName': CAMPAIGN_NAME,
        'decoderManifestName': DECODER_NAME,
        'collectionScheme': {
            'timeBasedCollectionScheme': {
                'periodMs': 30000
            }
        },
        'signalsToCollect': [
            {'name': fqn, 'maxSampleCount': 1, 'minimumSamplingIntervalMs': 0}
            for fqn, *_ in sorted(CAN_SIGNALS)
        ],
        'compression': 'SNAPPY',
        'spoolingMode': 'TO_DISK',
    }

    key = f'campaigns/{CAMPAIGN_NAME}/v1/collection-scheme.json'
    s3.put_object(
        Bucket=CAMPAIGN_BUCKET,
        Key=key,
        Body=json.dumps(scheme, indent=2),
        ContentType='application/json'
    )
    print(f"✅ Campaign seeded: {CAMPAIGN_NAME} ({len(CAN_SIGNALS)} signals, 30s period) → s3://{CAMPAIGN_BUCKET}/{key}")


def seed_campaigns_table():
    """Seed the campaigns table with the default broadcast campaign."""
    table = dynamodb.Table(f'cms-{STAGE}-campaigns')
    try:
        table.put_item(Item={
            'campaignId': CAMPAIGN_NAME,
            'targetArn': 'all',
            'status': 'SUSPENDED',
            'decoderManifestId': DECODER_NAME,
            'campaignName': CAMPAIGN_NAME,
            'collectionScheme': {'type': 'TIME_BASED', 'periodMs': 30000},
            'signalCount': len(CAN_SIGNALS),
            'description': f'Default fleet telemetry campaign ({len(CAN_SIGNALS)} CAN signals, 30s)',
            'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
        })
        print(f"✅ Campaigns table seeded: {CAMPAIGN_NAME} (SUSPENDED — activated per-vehicle on simulation start)")
    except Exception as e:
        print(f"⚠️  Campaigns table seed skipped: {e}")


# Additional templates mirrored from original us-east-2 deployment.
# These are copied per-vehicle via POST /api/v1/fleet-campaigns/assign.
# Safety templates trigger on condition (e.g. speeding, harsh braking)
# so periodMs is a fallback/heartbeat value - Flink CampaignSyncProcessor
# interprets collectionScheme appropriately for the campaign type.
#
# Schema: list of dicts with:
#   - name (required): campaignId / campaignName
#   - scheme_type (required): 'TIME_BASED' or 'CONDITION_BASED'
#   - period_ms (required): collection period / heartbeat in milliseconds
#   - desc (required): human-readable description
#   - category (optional): free-form tag (e.g. 'safety', 'telemetry',
#     'diagnostics'). Omit for unscoped templates.
#   - signals_to_collect (optional): list of signal_ids. Defaults to the
#     full CAN_SIGNALS set — override when a template should only emit a
#     specific subset (e.g. UDS DTC_INFO signals only).
#   - signals_to_fetch (optional): list of FetchInformation dicts. Only
#     set for templates that drive UDS-DTC polling or similar FWE-side
#     actions. CampaignSyncProcessor reads this and emits FetchInformation
#     protobuf fields to FWE.
#   - source (optional): provenance tag, carried through to assignments
#     via data_processing_api.assign_campaign.
#
# Keep UDS-DTC-specific constants in this file close to the template
# definition so the three agreements (signal_ids 901-909, ECU target
# addresses 1-9, DTC_QUERY params [ecu, 2, -1]) are visible here too.
# Canonical source of truth for the IDs is
# deployment/scripts/generate_decoder_manifest.py.
_UDS_ECU_SIGNAL_IDS = [900 + n for n in range(1, 10)]  # 901..909
_UDS_ECU_TARGET_ADDRESSES = list(range(1, 10))  # 1..9
_UDS_POLL_FREQUENCY_MS = 30_000
# UDS 0x19 subfunction 0x02 = reportDTCByStatusMask. statusMask = -1 →
# match any DTC status (FWE treats -1 as "don't filter").
_UDS_SIGNALS_TO_FETCH = [
    {
        "signalId": signal_id,
        "functionName": "DTC_QUERY",
        "params": [ecu_num, 2, -1],
        "executionFrequencyMs": _UDS_POLL_FREQUENCY_MS,
        "maxExecutionCount": 0,
    }
    for ecu_num, signal_id in zip(_UDS_ECU_TARGET_ADDRESSES, _UDS_ECU_SIGNAL_IDS)
]

EXTRA_TEMPLATES = [
    {'name': 'cms-fleet-gps-10s', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'High-frequency GPS tracking for route analytics (10s interval)',
     'category': 'telemetry'},
    {'name': 'cms-fleet-telemetry-60s', 'scheme_type': 'TIME_BASED', 'period_ms': 60000,
     'desc': 'Low-frequency telemetry for long-term vehicle monitoring (60s interval)',
     'category': 'telemetry'},
    {'name': 'cms-safety-harsh-braking', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: harsh-braking event collection (deceleration > 0.4g)',
     'category': 'safety'},
    {'name': 'cms-safety-harsh-accel', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: harsh-acceleration event collection (accel > 0.35g)',
     'category': 'safety'},
    {'name': 'cms-safety-harsh-cornering', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: harsh-cornering event collection (lateral-g > 0.4g)',
     'category': 'safety'},
    {'name': 'cms-safety-speeding', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: speeding event collection (speed > 80mph)',
     'category': 'safety'},
    {'name': 'cms-safety-lane-departure', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: lane-departure ADAS event collection',
     'category': 'safety'},
    {'name': 'cms-safety-esc-activation', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: Electronic Stability Control activation event',
     'category': 'safety'},
    {'name': 'cms-safety-aeb-activation', 'scheme_type': 'TIME_BASED', 'period_ms': 10000,
     'desc': 'Safety: Automatic Emergency Braking activation event',
     'category': 'safety'},
    # Authentic UDS-DTC polling. Assigning this to a vehicle makes FWE
    # fire UDS 0x19 readDTCByStatusMask on all 9 ECUs every 30s and emit
    # responses as STRING signals. Lands rows in cms-<stage>-storage-
    # dtc-history with source=fwe-uds-dtc.  Prerequisites: vehicle must
    # have a running FWE agent with exampleUDSInterface config + a UDS
    # responder reachable on its vcan bus.  See docs/FWE_UDS_DTC.md.
    {'name': 'uds-dtc-polling', 'scheme_type': 'TIME_BASED', 'period_ms': 30000,
     'desc': ('Authentic UDS-DTC polling. Fires UDS 0x19 readDTCByStatusMask '
              'on 9 ECUs every 30s; responses land in cms-<stage>-storage-'
              'dtc-history with source=fwe-uds-dtc.'),
     'category': 'diagnostics',
     'source': 'uds-dtc-template',
     'signals_to_collect': _UDS_ECU_SIGNAL_IDS,
     'signals_to_fetch': _UDS_SIGNALS_TO_FETCH},
]


def seed_extra_templates():
    """Seed the additional template campaigns (fleet-GPS, safety triggers,
    UDS-DTC polling).

    These are ACTIVE templates the user can click-to-assign to any vehicle
    via the Vehicle Management > Campaigns tab. The UI calls
    POST /api/v1/fleet-campaigns/assign which copies the template into a
    targetArn=fleet:<id> or vehicle:<id> row with status=RUNNING."""
    table = dynamodb.Table(f'cms-{STAGE}-campaigns')

    # 2026-05-04: before this fix, the `if 'signals_to_collect' in tpl`
    # gate below only wrote signalsToCollect for templates that
    # explicitly specified their own list. Telemetry-wide templates like
    # cms-fleet-gps-10s don't specify it (they're supposed to collect
    # "all signals"), so they got written with signalCount=262 but an
    # empty signalsToCollect field. The UI's fleet-campaigns/assign
    # then copied that empty list into every vehicle-level campaign it
    # fanned out, producing FWE campaigns that collected nothing.
    #
    # Fix: compute a default signal list ONCE by reading the signal
    # catalog (active signals with signal_id < 900 — the <900 cutoff
    # excludes UDS DTC polling signals which live in a separate
    # dedicated template). This mirrors what the healthy campaigns
    # already have in prod (262 active signal IDs as of today).
    default_signals_to_collect: list = []
    try:
        catalog = dynamodb.Table(f'cms-{STAGE}-signal-catalog')
        catalog_items = []
        _r = catalog.scan(
            FilterExpression='#s = :a',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':a': 'active'},
            ProjectionExpression='signal_id',
        )
        catalog_items.extend(_r.get('Items', []))
        while 'LastEvaluatedKey' in _r:
            _r = catalog.scan(
                FilterExpression='#s = :a',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':a': 'active'},
                ProjectionExpression='signal_id',
                ExclusiveStartKey=_r['LastEvaluatedKey'],
            )
            catalog_items.extend(_r.get('Items', []))
        # Filter out UDS DTC polling signals (IDs ≥ 900) — those belong
        # to a separate dedicated template and shouldn't be collected by
        # a telemetry campaign.
        default_signals_to_collect = [
            int(it['signal_id']) for it in catalog_items
            if int(it['signal_id']) < 900
        ]
        default_signals_to_collect.sort()
        print(f"  default signalsToCollect: {len(default_signals_to_collect)} active signals from catalog (excluding UDS DTC IDs ≥ 900)")
    except Exception as e:
        print(f"  ⚠️  could not load signal catalog ({e}); templates without explicit signals_to_collect will be written with an empty list")

    written = 0
    for tpl in EXTRA_TEMPLATES:
        try:
            item = {
                'campaignId': tpl['name'],
                'campaignName': tpl['name'],
                'targetArn': 'template',
                'status': 'ACTIVE',
                'decoderManifestId': DECODER_NAME,
                'collectionScheme': {'type': tpl['scheme_type'], 'periodMs': tpl['period_ms']},
                'description': tpl['desc'],
                'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                # signalCount is informational only — UI table renders it
                # in the Campaigns listing. Overridden below for templates
                # that specify a custom signals_to_collect.
                'signalCount': len(tpl.get('signals_to_collect', default_signals_to_collect)),
            }
            if 'category' in tpl:
                item['category'] = tpl['category']
            if 'source' in tpl:
                item['source'] = tpl['source']
            # Signals: use the template's explicit list if provided,
            # otherwise fall back to the catalog-derived default. This
            # guarantees signalsToCollect is ALWAYS populated so any
            # downstream code that copies the template (fleet assign,
            # vehicle assign, etc.) never silently propagates an empty
            # list. Previously only the first branch was emitted and
            # the field was missing entirely from catch-all templates.
            if 'signals_to_collect' in tpl:
                item['signalsToCollect'] = tpl['signals_to_collect']
            else:
                item['signalsToCollect'] = default_signals_to_collect
            if 'signals_to_fetch' in tpl:
                item['signalsToFetch'] = tpl['signals_to_fetch']
            table.put_item(Item=item)
            written += 1
        except Exception as e:
            print(f"  ⚠️  {tpl['name']}: {e}")
    print(f"✅ Extra template campaigns seeded: {written} of {len(EXTRA_TEMPLATES)}")


if __name__ == '__main__':
    print(f"Seeding decoder manifest and campaign for stage={STAGE}")
    seed_decoder_manifest()
    seed_campaign()
    seed_campaigns_table()
    seed_extra_templates()
    print("✅ Done")

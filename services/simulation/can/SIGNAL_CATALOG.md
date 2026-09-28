# CMS Fleet Signal Catalog

## Overview

- **DBC File**: `services/simulation/can/cms-fleet.dbc`
- **CAN Messages**: 15
- **CAN Signals**: 75
- **GPS Signals**: 2 (separate interface, not on CAN)
- **Metadata Fields**: 6 (vehicleId, tripId, driverId, timestamp, messageType, engineEvent)

## Signal Mapping: DBC ↔ JSON Telemetry ↔ FleetWise VSS Path

### ECM_Engine_1 (0x100, 100ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| VehicleSpeed | `speed` | Vehicle.Speed | mph | 0–655 |
| EngineRPM | `engineRPM` | Vehicle.Powertrain.Engine.RPM | rpm | 0–8000 |
| EngineTemp | `engineTemp` | Vehicle.Powertrain.Engine.Temperature | °F | -40–370 |
| IgnitionOn | `ignitionOn` | Vehicle.Powertrain.IgnitionOn | bool | 0/1 |

### ECM_Engine_2 (0x101, 500ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| OilPressure | `oilPressure` | Vehicle.Powertrain.Engine.OilPressure | PSI | 0–102 |
| EngineLoad | `engineLoad` | Vehicle.Powertrain.Engine.Load | % | 0–128 |
| ThrottlePosition | `throttle` | Vehicle.Powertrain.Engine.ThrottlePosition | % | 0–102 |
| CoolantTemp | `coolant_temp` | Vehicle.Powertrain.Engine.CoolantTemperature | °F | -40–370 |
| IntakeAirTemp | `intakeAirTemp` | Vehicle.Powertrain.Engine.IntakeAirTemperature | °F | -40–62 |
| EngineHoursTotal | `engine_hours_total` | Vehicle.Powertrain.Engine.HoursTotal | hrs | 0–16383 |

### ECM_Engine_3 (0x102, 100ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| Acceleration | `acceleration` | Vehicle.Acceleration | m/s² | -20–21 |
| Deceleration | `deceleration` | Vehicle.Deceleration | m/s² | -20–21 |
| Odometer | `odometer` | Vehicle.Odometer | miles | 0–16M |
| FuelRate | `fuel_rate` | Vehicle.Powertrain.FuelSystem.FuelRate | L/h | 0–51 |

### TCM_Transmission (0x110, 200ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| TransmissionTemp | `transmissionTemp` | Vehicle.Powertrain.Transmission.Temperature | °F | -40–472 |
| GearPosition | `gearPosition` | Vehicle.Powertrain.Transmission.GearPosition | enum | 0–7 |
| CruiseControl | `cruise_control` | Vehicle.ADAS.CruiseControl.Active | bool | 0/1 |
| ParkingBrake | `parking_brake` | Vehicle.Chassis.ParkingBrake.Active | bool | 0/1 |

### BCM_Body_1 (0x120, 500ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| SeatbeltStatus | `seatbeltStatus` | Vehicle.Cabin.Seatbelt.Driver.Fastened | bool | 0/1 |
| PhoneConnected | `phoneConnected` | Vehicle.Cabin.Infotainment.PhoneConnected | bool | 0/1 |
| WindowsUp | `windows_up` | Vehicle.Cabin.Windows.AllClosed | bool | 0/1 |
| TrunkLocked | `trunk_locked` | Vehicle.Body.Trunk.Locked | bool | 0/1 |
| AlarmArmed | `alarm_armed` | Vehicle.Body.Alarm.Armed | bool | 0/1 |
| KeylessEntry | `keyless_entry` | Vehicle.Body.KeylessEntry.Proximity | bool | 0/1 |
| Headlights | `headlights` | Vehicle.Body.Lights.Headlights.Mode | enum | 0–2 |
| HazardLights | `hazard_lights` | Vehicle.Body.Lights.Hazard.Active | bool | 0/1 |
| TurnSignalActive | `turn_signal_active` | Vehicle.Body.Lights.TurnSignal.Active | bool | 0/1 |
| WifiConnected | `wifi_connected` | Vehicle.Connectivity.WiFi.Connected | bool | 0/1 |
| BluetoothDevices | `bluetooth_devices` | Vehicle.Connectivity.Bluetooth.DeviceCount | count | 0–7 |
| NavigationActive | `navigation_active` | Vehicle.Cabin.Infotainment.Navigation.Active | bool | 0/1 |

### TPMS_Pressure (0x130, 1000ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| TirePressureFL | `tire_fl` | Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure | PSI | 0–102 |
| TirePressureFR | `tire_fr` | Vehicle.Chassis.Axle.Row1.Wheel.Right.Tire.Pressure | PSI | 0–102 |
| TirePressureRL | `tire_rl` | Vehicle.Chassis.Axle.Row2.Wheel.Left.Tire.Pressure | PSI | 0–102 |
| TirePressureRR | `tire_rr` | Vehicle.Chassis.Axle.Row2.Wheel.Right.Tire.Pressure | PSI | 0–102 |
| TireTempMax | `tire_temp_max` | Vehicle.Chassis.Tire.TemperatureMax | °F | 0–255 |

### TPMS_Tread (0x131, 5000ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| TireTreadFL | `tire_tread_fl` | Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.TreadDepth | mm | 0–25.5 |
| TireTreadFR | `tire_tread_fr` | Vehicle.Chassis.Axle.Row1.Wheel.Right.Tire.TreadDepth | mm | 0–25.5 |
| TireTreadRL | `tire_tread_rl` | Vehicle.Chassis.Axle.Row2.Wheel.Left.Tire.TreadDepth | mm | 0–25.5 |
| TireTreadRR | `tire_tread_rr` | Vehicle.Chassis.Axle.Row2.Wheel.Right.Tire.TreadDepth | mm | 0–25.5 |

### ADAS_Safety_1 (0x140, 100ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| HarshBraking | `harsh_brk` | Vehicle.ADAS.Safety.HarshBraking | g | 0–1.02 |
| HarshAcceleration | `harsh_acc` | Vehicle.ADAS.Safety.HarshAcceleration | g | 0–1.02 |
| HarshTurn | `harsh_turn` | Vehicle.ADAS.Safety.HarshTurn | deg/s | 0–102 |
| SpeedViolation | `speed_viol` | Vehicle.ADAS.Safety.SpeedViolation | bool | 0/1 |
| AEBActivation | `aeb_act` | Vehicle.ADAS.AEB.Active | bool | 0/1 |
| ABSActivation | `abs_act` | Vehicle.ADAS.ABS.Active | bool | 0/1 |
| ESCActivation | `esc_act` | Vehicle.ADAS.ESC.Active | bool | 0/1 |
| AirbagWarning | `airbag_warn` | Vehicle.Safety.Airbag.Warning | bool | 0/1 |
| TractionControl | `traction_control` | Vehicle.ADAS.TractionControl.Active | bool | 0/1 |
| StabilityControl | `stability_control` | Vehicle.ADAS.StabilityControl.Active | bool | 0/1 |

### ADAS_Safety_2 (0x141, 100ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| PhoneUsage | `phone_use` | Vehicle.Safety.Driver.PhoneUsage | bool | 0/1 |
| SeatbeltViolation | `seatbelt` | Vehicle.Safety.Driver.SeatbeltViolation | bool | 0/1 |
| LateralG | `lateralG` | Vehicle.Acceleration.Lateral | g | -5–5.2 |
| FollowingDistance | `followingDistance` | Vehicle.ADAS.FollowingDistance | m | 0–102 |

### BMS_Battery (0x150, 1000ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| BatteryVoltage | `batteryVoltage` | Vehicle.Powertrain.Battery.Voltage | V | 0–20.5 |
| AlternatorOutput | `alternator_output` | Vehicle.Powertrain.Alternator.Voltage | V | 0–20.5 |
| FuelLevel | `fuelLevel` | Vehicle.Powertrain.FuelSystem.Level | % | 0–128 |

### BMS_EV (0x151, 1000ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| IsEV | `is_ev` | Vehicle.Powertrain.Type.IsEV | bool | 0/1 |
| StateOfCharge | `soc` | Vehicle.Powertrain.Battery.StateOfCharge | % | 0–128 |
| HVBatteryVoltage | `volt` | Vehicle.Powertrain.Battery.HVVoltage | V | 0–410 |
| RegenPower | `regen_pwr` | Vehicle.Powertrain.Battery.RegenPower | kW | -50–155 |

### ICM_Instrument (0x160, 200ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| Heading | `heading` | Vehicle.Navigation.Heading | deg | 0–410 |
| DTCCodesActive | `dtc_codes_active` | Vehicle.Diagnostics.DTCActive | bool | 0/1 |

### HVAC_Climate (0x170, 2000ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| HVACOn | `hvac_on` | Vehicle.Cabin.HVAC.Active | bool | 0/1 |
| TargetTemp | `target_temp` | Vehicle.Cabin.HVAC.TargetTemperature | °F | 40–168 |
| CabinTemp | `cabin_temp` | Vehicle.Cabin.HVAC.CabinTemperature | °F | 40–168 |
| SeatHeatDriver | `seat_heat_driver` | Vehicle.Cabin.Seat.Driver.HeatingLevel | level | 0–3 |

### MAINT_Indicators (0x180, 5000ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| OilLife | `oil_life` | Vehicle.Maintenance.OilLife | % | 0–128 |
| BrakeWear | `brake_wear` | Vehicle.Maintenance.BrakeWear | % | 0–128 |
| FilterLife | `filter_life` | Vehicle.Maintenance.FilterLife | % | 0–128 |
| IdleHoursTotal | `idle_hours_total` | Vehicle.Powertrain.Engine.IdleHoursTotal | hrs | 0–16383 |
| AirPressure | `air_pressure` | Vehicle.Chassis.Brake.AirPressure | PSI | 0–128 |
| HydraulicPressure | `hydraulic_pressure` | Vehicle.Chassis.Brake.HydraulicPressure | PSI | 0–4095 |

### LIGHT_Systems (0x190, 500ms cycle)
| DBC Signal | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| HeadlightMode | `headlights` | Vehicle.Body.Lights.Headlights.Mode | enum | 0–2 |
| HazardActive | `hazard_lights` | Vehicle.Body.Lights.Hazard.Active | bool | 0/1 |
| TurnSignal | `turn_signal_active` | Vehicle.Body.Lights.TurnSignal.Active | bool | 0/1 |

## GPS (Separate Custom Interface — not on CAN)
| Field | JSON Field | VSS Path | Unit | Range |
|---|---|---|---|---|
| Latitude | `lat` | Vehicle.CurrentLocation.Latitude | deg | -90–90 |
| Longitude | `lng` | Vehicle.CurrentLocation.Longitude | deg | -180–180 |

## Metadata (Not CAN signals — added by simulator/bridge)
| Field | JSON Field | Notes |
|---|---|---|
| Message Type | `messageType` | Always `TELEMETRY` |
| Vehicle ID | `vehicleId` | `5YJ3E1EA1PF721240` |
| Timestamp | `timestamp` | Epoch milliseconds |
| Trip ID | `tripId` | `5YJ3E1EA1PF721240-timestamp-uuid` |
| Driver ID | `driverId` | `DRV-XXXXXXXXXX` |
| Engine Event | `engineEvent` | `ENGINE_START` / `ENGINE_STOP` |

## Safety Event Types (derived from signals by SafetyProcessor)
| Event | Trigger Signal | Threshold |
|---|---|---|
| SPEEDING | VehicleSpeed | > 65 mph |
| HARD_BRAKING | HarshBraking | > 0.3g |
| RAPID_ACCELERATION | HarshAcceleration | > 0.3g |
| HARSH_CORNERING | HarshTurn | > 40 deg/s |
| SEATBELT_VIOLATION | SeatbeltViolation | = 0 while driving |
| PHONE_USAGE | PhoneUsage | = 1 while driving |
| LANE_DEPARTURE | LateralG | > 0.5g |
| TAILGATING | FollowingDistance | < 2.0m |
| AEB_ACTIVATION | AEBActivation | = 1 |
| ESC_ACTIVATION | ESCActivation | = 1 |

## Maintenance Alert Types (derived from signals by MaintenanceProcessor)
| Alert | Trigger Signal | Threshold | DTC |
|---|---|---|---|
| LOW_OIL_PRESSURE | OilPressure | < 15 PSI | P0520 |
| HIGH_ENGINE_TEMP | EngineTemp | > 230°F | P0217 |
| LOW_BATTERY | BatteryVoltage | < 11.5V | P0562 |
| ENGINE_OVERSPEED | EngineRPM | > 6000 | P0219 |
| LOW_FUEL | FuelLevel | < 5% | P0461 |
| BRAKE_WEAR | BrakeWear | < 20% | P0301 |
| TIRE_PRESSURE | TirePressureXX | < 25 PSI | C1234 |
| OIL_LIFE_LOW | OilLife | < 10% | P0524 |
| FILTER_REPLACEMENT | FilterLife | < 15% | P0102 |
| TIRE_TREAD_LOW | TireTreadXX | < 3mm | C1235 |

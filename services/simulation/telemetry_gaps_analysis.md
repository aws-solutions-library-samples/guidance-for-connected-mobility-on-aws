# Telemetry Fields Gap Analysis

## Current Fields (✅ Already Implemented)

### Vehicle State
- ✅ tire_fl, tire_fr, tire_rl, tire_rr, tire_temp_max
- ✅ door_drv, door_pass, door_cargo
- ✅ fuel_lvl, eng_temp, oil_press, oil_temp
- ✅ seatbelt, phone_use
- ✅ gear, brk, acc, spd

### Diagnostics  
- ✅ eng_temp, oil_press, oil_temp, coolant_temp, trans_temp
- ✅ oil_life, brake_wear, filter_life

## Missing Critical Vehicle State Fields

### 🔒 Security & Access Control
- ❌ **doors_locked** - Door lock status (critical for security)
- ❌ **windows_up** - Window positions (security/weather)
- ❌ **trunk_locked** - Cargo area security
- ❌ **alarm_armed** - Security system status
- ❌ **remote_start** - Remote engine start status
- ❌ **keyless_entry** - Proximity key detected

### 🚗 Vehicle Control Systems
- ❌ **parking_brake** - Parking brake engaged/disengaged
- ❌ **cruise_control** - Cruise control active/set speed
- ❌ **traction_control** - TC system status
- ❌ **stability_control** - ESC system status
- ❌ **hill_assist** - Hill start assist active
- ❌ **auto_hold** - Auto brake hold feature

### 🌡️ Climate & Comfort
- ❌ **hvac_on** - Climate control system status
- ❌ **target_temp** - Set temperature
- ❌ **cabin_temp** - Actual cabin temperature
- ❌ **defrost_on** - Windshield defrost status
- ❌ **seat_heat_driver** - Heated seat level (0-3)
- ❌ **seat_heat_passenger** - Passenger heated seat

### 💡 Lighting Systems
- ❌ **headlights** - Headlight status (off/auto/on/high)
- ❌ **fog_lights** - Fog light status
- ❌ **hazard_lights** - Emergency flashers
- ❌ **turn_signals** - Left/right turn signal active
- ❌ **interior_lights** - Cabin lighting status

### 🔋 Electrical Systems
- ❌ **battery_voltage** - 12V system voltage
- ❌ **alternator_output** - Charging system status
- ❌ **power_outlets** - 12V outlet usage
- ❌ **usb_ports** - USB port power draw

### 🛞 Advanced Tire Monitoring
- ❌ **tire_tread_fl/fr/rl/rr** - Tread depth remaining
- ❌ **tire_age_months** - Tire age for replacement
- ❌ **tire_rotation_due** - Rotation maintenance flag

### 📡 Connectivity & Infotainment
- ❌ **wifi_connected** - Vehicle WiFi status
- ❌ **bluetooth_devices** - Number of connected devices
- ❌ **radio_on** - Infotainment system status
- ❌ **navigation_active** - GPS navigation running
- ❌ **voice_command** - Voice assistant active

### 🚛 Commercial Vehicle Specific
- ❌ **pto_engaged** - Power take-off status
- ❌ **hydraulic_pressure** - Lift gate hydraulics
- ❌ **compressor_status** - Air brake compressor
- ❌ **air_pressure** - Air brake system pressure
- ❌ **fifth_wheel_locked** - Trailer connection status

### 🔧 Maintenance Predictors
- ❌ **engine_hours_total** - Total engine runtime
- ❌ **idle_hours_total** - Total idle time
- ❌ **hard_braking_count** - Cumulative harsh events
- ❌ **overrev_count** - Engine over-rev events
- ❌ **dtc_codes** - Active diagnostic trouble codes

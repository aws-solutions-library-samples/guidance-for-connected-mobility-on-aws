# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""DTC-to-routine suggestion map.

Maps OBD-II / UDS diagnostic trouble codes to a list of routine IDs from
``routine_catalog.py`` that a technician would typically run when investigating
that fault.

DESIGN NOTES
------------
* Every routine ID in this map MUST be defined in ``routine_catalog.py``.
  ``test_dtc_suggestions.py`` enforces this at test-time — that test is the guard
  that would have caught the spec's own illustrative map (F5), which contained 12
  IDs that do not exist.

* Mappings are powertrain-aware at authoring time.  A DTC that cannot be triggered
  by a given powertrain is not mapped to a routine that only applies to that
  powertrain.  For example, P0420 (catalyst efficiency, lambda-based) is an ICE-only
  fault; mapping it to ``o2_heater_check`` is correct.  Mapping an EV battery-isolation
  fault (U3xxx) to an ICE emissions routine would be wrong even if the routine ID
  happened to exist.

  Profile filtering (intersecting with the vehicle's actual profile at query time) is
  T2.6's job; these suggestions are intended to be *already sensible* before that
  filter is applied, so the output of T2.6 is correct even if the filter is bypassed.

* A mapping of ``[]`` is **explicitly accepted** when no real routine in the catalog
  meaningfully addresses a DTC at the service-routine level — e.g. a pure sensor-read
  fault where the appropriate action is replacement, not an actuator cycle.  An empty
  list here is honest; an invented routine ID is not.

* This map does NOT replace the routine catalog's per-vehicle invocability check.
  It is purely a suggestion surface: "if you are diagnosing fault X, you might want
  to run these routines."  Whether the caller *may* actually invoke them is determined
  by the safety-class and technician-presence checks.

ROUTINE ID VOCABULARY (all 18 valid IDs, from routine_catalog.py):
  Cross-powertrain (all profiles):
    abs_pump_cycle, brake_bleed_sequence, lamp_self_check,
    steering_angle_calibration, throttle_body_adaptation

  ICE_GASOLINE + HYBRID:
    evap_purge, evap_leak_test, o2_heater_check, injector_balance_test

  ICE_DIESEL only:
    glow_plug_test, dpf_regeneration, reductant_dosing_cycle

  EV + HYBRID:
    pack_isolation_test, thermal_prime, charge_port_lock_test, cell_balance_check

  HYBRID only:
    mode_transition_test, generator_output_test

C8 COMPLIANCE:
  No VINs, brand names, or account IDs.
  DTC codes are standard OBD-II / UDS vocabulary (ISO 15765-4, SAE J1979).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# DTC → routine suggestion map
# ---------------------------------------------------------------------------
# Key  : uppercase OBD-II/UDS DTC code (e.g. "P0420")
# Value: list of routine_id strings from routine_catalog.py
#
# Powertrain applicability by DTC category (for authoring sanity):
#   P01xx–P02xx  Fuel/air metering (gasoline/diesel ICE)
#   P03xx        Ignition system (ICE)
#   P04xx        Emissions controls (ICE, mainly gasoline)
#   P11xx–P13xx  Lambda / injection trims (ICE)
#   P0A0x–P0Axx  Hybrid/EV traction battery
#   P0Bxx        Hybrid drive system
#   C0xxx        Chassis (ABS, brakes, steering — all powertrains)
#   B0xxx        Body (lamps, locks — all powertrains)
#   U0xxx        Network / communication (all powertrains)
#   U3xxx        Manufacturer-specific network (incl. EV BMS faults)
# ---------------------------------------------------------------------------

DTC_SUGGESTIONS: dict[str, list[str]] = {

    # -----------------------------------------------------------------------
    # P04xx — Emissions / oxygen-sensor faults (ICE_GASOLINE + HYBRID only)
    # -----------------------------------------------------------------------

    # P0420: Catalyst system efficiency below threshold (Bank 1)
    # Downstream O2 sensor reports poor catalyst conversion.
    # Run o2_heater_check to verify both upstream/downstream sensors are
    # functional before condemning the catalyst.
    "P0420": ["o2_heater_check"],

    # P0430: Catalyst system efficiency below threshold (Bank 2)
    # Same diagnosis path as P0420, opposite bank.
    "P0430": ["o2_heater_check"],

    # P0441: EVAP system incorrect purge flow
    # Purge valve not cycling as commanded — run evap_purge to exercise it.
    "P0441": ["evap_purge", "evap_leak_test"],

    # P0442: EVAP system small leak detected
    # Vapour recovery system has a minor leak path.  Run the leak test
    # to confirm leak magnitude before probing hoses and fittings.
    "P0442": ["evap_leak_test"],

    # P0455: EVAP system large leak / gross leak detected
    # Large-aperture path in the vapour system.  evap_leak_test first;
    # evap_purge to verify canister-side response.
    "P0455": ["evap_leak_test", "evap_purge"],

    # P0136: O2 sensor circuit malfunction (Bank 1, Sensor 2)
    # Downstream lambda sensor unresponsive.  Check heater circuit first
    # because a cold/failed heater is the most common cause.
    "P0136": ["o2_heater_check"],

    # P0141: O2 sensor heater circuit malfunction (Bank 1, Sensor 2)
    # Directly targets the heater element.
    "P0141": ["o2_heater_check"],

    # -----------------------------------------------------------------------
    # P01xx / P03xx — Fuel trim and ignition (ICE_GASOLINE + HYBRID)
    # -----------------------------------------------------------------------

    # P0171: System too lean (Bank 1)
    # Common causes: vacuum leak, low fuel pressure, faulty MAF/O2.
    # Run o2_heater_check (sensor health) and injector_balance_test
    # (verify each injector is contributing equally).
    "P0171": ["o2_heater_check", "injector_balance_test"],

    # P0172: System too rich (Bank 1)
    # Opposite mixture fault. Same investigative set.
    "P0172": ["o2_heater_check", "injector_balance_test"],

    # P0300: Random/multiple misfire detected
    # Misfire across cylinders — fuel delivery balance is a prime suspect.
    "P0300": ["injector_balance_test"],

    # P0301–P0306: Cylinder-specific misfire
    # Single-cylinder misfire — injector balance test isolates delivery.
    "P0301": ["injector_balance_test"],
    "P0302": ["injector_balance_test"],
    "P0303": ["injector_balance_test"],
    "P0304": ["injector_balance_test"],

    # -----------------------------------------------------------------------
    # P0xxx — Throttle / idle (ICE_GASOLINE, ICE_DIESEL, HYBRID)
    # -----------------------------------------------------------------------

    # P0121: Throttle position sensor range/performance
    # TPS out of expected range — throttle body adaptation resets the
    # learned baseline, which can clear adaptive-value drift faults.
    "P0121": ["throttle_body_adaptation"],

    # P0122: Throttle position sensor circuit low
    # Adaptation won't fix a wiring fault, but running it after repair
    # confirms the ECU can re-learn from the new baseline.
    "P0122": ["throttle_body_adaptation"],

    # P1xxx — Manufacturer-defined idle/throttle trims (common on VAG, Ford)
    "P1121": ["throttle_body_adaptation"],

    # -----------------------------------------------------------------------
    # P065xx — Diesel-specific: glow plugs (ICE_DIESEL only)
    # -----------------------------------------------------------------------

    # P0670: Glow plug control module circuit
    # Control module fault — glow_plug_test energises plugs and measures
    # current draw per plug to localise which has failed.
    "P0670": ["glow_plug_test"],

    # P0671–P0674: Glow plug circuit, cylinder 1–4
    "P0671": ["glow_plug_test"],
    "P0672": ["glow_plug_test"],
    "P0673": ["glow_plug_test"],
    "P0674": ["glow_plug_test"],

    # -----------------------------------------------------------------------
    # P02xx — Diesel injection / DPF (ICE_DIESEL only)
    # -----------------------------------------------------------------------

    # P2002: Diesel particulate filter efficiency below threshold
    # DPF is loaded — dpf_regeneration is the corrective action; requires
    # a technician on-site (SERVICE_ONLY) per D25.
    "P2002": ["dpf_regeneration"],

    # P2458: DPF regeneration duration too long
    # Regeneration is running but stalling — same corrective path.
    "P2458": ["dpf_regeneration"],

    # P20EE: SCR NOx catalyst efficiency below threshold
    # DEF/AdBlue dosing issue — reductant_dosing_cycle purges and
    # verifies injector response.  SERVICE_ONLY per D25.
    "P20EE": ["reductant_dosing_cycle"],

    # P203B: Reductant level too low
    # Tank almost empty.  Dosing cycle can verify injector health
    # after the fluid is topped up.
    "P203B": [],   # [] — correct action is fluid top-up, not a routine

    # -----------------------------------------------------------------------
    # C0xxx — Chassis: ABS / brakes / steering (all powertrains)
    # -----------------------------------------------------------------------

    # C0031: Right front wheel speed sensor circuit malfunction
    # Wheel-speed sensor hardware fault — no catalog routine exercises
    # sensors directly; abs_pump_cycle verifies the hydraulic system's
    # response once the sensor is replaced.
    "C0031": ["abs_pump_cycle"],

    # C0034: Left front wheel speed sensor circuit malfunction
    "C0034": ["abs_pump_cycle"],

    # C0040: Right rear wheel speed sensor circuit malfunction
    "C0040": ["abs_pump_cycle"],

    # C0044: Left rear wheel speed sensor circuit malfunction
    "C0044": ["abs_pump_cycle"],

    # C0265: ABS activation relay circuit open
    # Relay-level fault in the ABS modulator — abs_pump_cycle is the
    # standard functional test after relay replacement.
    "C0265": ["abs_pump_cycle"],

    # C0561: ABS system disabled
    # System-level disable fault — abs_pump_cycle after clearing codes.
    "C0561": ["abs_pump_cycle"],

    # C0035: Left front wheel speed sensor — manufacturer variant of C0031
    "C0035": ["abs_pump_cycle"],

    # C1234: Right front wheel speed sensor — another common OBD-II variant
    "C1234": ["abs_pump_cycle"],

    # C1235: Right rear wheel speed sensor
    "C1235": ["abs_pump_cycle"],

    # C1236: Left rear wheel speed sensor
    "C1236": ["abs_pump_cycle"],

    # Brake bleed after caliper/master cylinder replacement:
    # C0131: Brake-pressure sensor range/performance
    "C0131": ["brake_bleed_sequence"],

    # C0110: ABS motor circuit malfunction — hydraulic + pump cycle
    "C0110": ["abs_pump_cycle", "brake_bleed_sequence"],

    # Steering angle calibration needed after alignment or rack replacement
    "C0455": ["steering_angle_calibration"],  # steering angle sensor range
    "C0450": ["steering_angle_calibration"],  # steering angle sensor circuit

    # -----------------------------------------------------------------------
    # B0xxx — Body: lamp faults (all powertrains)
    # -----------------------------------------------------------------------

    # B0330: Lamp circuit — any BCM-reported lamp fault.
    # lamp_self_check runs the ECU's own circuit verification.
    "B0330": ["lamp_self_check"],

    # B1341: Rear stop lamp circuit (common EOBD/SAE body code)
    "B1341": ["lamp_self_check"],

    # B1342: Daytime running lamp circuit
    "B1342": ["lamp_self_check"],

    # -----------------------------------------------------------------------
    # P0Axx — High-voltage battery / EV drive system (EV + HYBRID)
    # -----------------------------------------------------------------------

    # P0A00: Motor electronics coolant temperature too high
    # Thermal management fault — thermal_prime activates the battery
    # thermal system to pre-condition and verify its response.
    "P0A00": ["thermal_prime"],

    # P0A7F: High-voltage battery pack deterioration
    # Battery health fault — cell_balance_check reads per-cell voltages
    # to identify imbalanced cells, and pack_isolation_test verifies
    # there is no insulation leak contributing to the imbalance.
    "P0A7F": ["cell_balance_check", "pack_isolation_test"],

    # P0AFA: High-voltage battery system voltage low
    # pack_isolation_test first (isolation fault could mimic low voltage);
    # cell_balance_check to locate the weak cell(s).
    "P0AFA": ["pack_isolation_test", "cell_balance_check"],

    # P0A80: Replace hybrid battery pack
    # Severe degradation flag — cell_balance_check to confirm, then
    # pack_isolation_test to rule out isolation as a contributing factor.
    "P0A80": ["cell_balance_check", "pack_isolation_test"],

    # P0A1A: Motor A position sensor circuit
    # Sensor fault — no catalog routine directly tests the resolver, but
    # mode_transition_test (HYBRID only) exercises the drive system
    # and would surface abnormal motor response.
    # Mapping only to mode_transition_test here because this applies to
    # HYBRID; for EV the map would be [] (no equivalent routine exists).
    # T2.6 profile filter will correctly drop mode_transition_test for EV.
    "P0A1A": ["mode_transition_test"],

    # P0A9B: High-voltage battery fan circuit
    # Cooling fan fault — thermal_prime exercises the thermal management
    # subsystem and will reveal fan-response anomalies.
    "P0A9B": ["thermal_prime"],

    # -----------------------------------------------------------------------
    # P0Bxx — Hybrid-specific: generator / drive system (HYBRID only)
    # -----------------------------------------------------------------------

    # P0B00: Hybrid battery voltage imbalance
    "P0B00": ["cell_balance_check", "pack_isolation_test"],

    # P0B1A: Generator A control circuit
    # Generator-side fault — generator_output_test activates the MG in
    # generation mode and measures voltage/current response.
    "P0B1A": ["generator_output_test"],

    # P0B24: Generator overvoltage
    "P0B24": ["generator_output_test"],

    # -----------------------------------------------------------------------
    # U0xxx / U3xxx — Network and manufacturer communication faults
    # -----------------------------------------------------------------------

    # U0100: Lost communication with ECM/PCM — no catalog routine restores
    # communication; the appropriate action is wiring diagnosis, not an
    # actuator cycle.  Intentional [].
    "U0100": [],

    # U0121: Lost communication with ABS control module
    # After restoring communication, abs_pump_cycle verifies the module
    # is responding correctly.
    "U0121": ["abs_pump_cycle"],

    # U0126: Lost communication with steering angle sensor module
    "U0126": ["steering_angle_calibration"],

    # U3000 — HV battery management system communication fault (EV / HYBRID)
    # After restoring CAN link, pack_isolation_test is the first functional
    # check for BMS integrity.
    "U3000": ["pack_isolation_test"],

    # -----------------------------------------------------------------------
    # Charge-port / EVSE faults (EV + HYBRID)
    # -----------------------------------------------------------------------

    # P0C31: EV charge port unlock circuit malfunction
    # Lock mechanism failure — charge_port_lock_test actuates the latch
    # and verifies unlock sequence.
    "P0C31": ["charge_port_lock_test"],

    # P0C20: EV battery charging voltage too low
    # Charging circuit fault — charge_port_lock_test rules out a stuck
    # latch preventing a full connection.
    "P0C20": ["charge_port_lock_test", "thermal_prime"],

}

# Ensure consistent key casing at module load time.  The map above is authored
# in uppercase, so this is a no-op in practice; it prevents a stale copy with
# mixed case from causing silent misses if the map is ever edited carelessly.
DTC_SUGGESTIONS = {k.upper(): v for k, v in DTC_SUGGESTIONS.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_routines_for_dtc(code: str) -> list[str]:
    """Return routine IDs suggested for a given DTC code.

    Args:
        code: OBD-II / UDS DTC code string, e.g. ``"P0420"`` or ``"p0420"``.
              Input is uppercase-normalised before lookup.

    Returns:
        A list of routine ID strings from ``routine_catalog.py`` that are
        typically useful when diagnosing the given fault.  Returns ``[]`` if
        the code is not in the map (unknown DTC) or if the map entry explicitly
        carries an empty list (known DTC with no applicable catalog routine).

    Notes:
        - The caller is responsible for intersecting the returned list with the
          vehicle's powertrain profile (via ``get_routines_for_profile`` /
          ``profile_for_fuel_type`` in ``routine_catalog.py``) before presenting
          suggestions to the operator.  That filtering is T2.6's scope; this
          function returns the raw suggestion set.
        - An empty return does **not** mean the DTC is unrecognised; it may mean
          the appropriate action is component replacement rather than an actuator
          routine.  Callers should not treat ``[]`` as "DTC unknown."
    """
    if not isinstance(code, str):
        return []
    return list(DTC_SUGGESTIONS.get(code.upper(), []))

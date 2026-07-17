# CMS Data Processing Framework

## Overview

The CMS Data Processing Framework provides a unified approach to ingesting telemetry data from multiple sources (IoT Core simulator, AWS IoT FleetWise, OEM APIs) and transforming them into a standard format for processing.

## Architecture Concepts

### Signal Catalog
The **Signal Catalog** (`signal-catalog.json`) defines the standard telemetry format used throughout CMS. All data sources must transform their data to match this catalog. This is similar to AWS IoT FleetWise's signal catalog concept.

**Key Features:**
- 70+ standardized signals organized into logical groups
- Imperial units (mph, Fahrenheit, PSI, miles)
- Support for both ICE and EV vehicles
- Extensible without breaking existing consumers

### Transform Manifests
**Transform Manifests** define how to convert external data formats into the CMS Signal Catalog format. This is analogous to:
- **FleetWise Decoder Manifests** - for in-vehicle data (CAN/DBC)
- **Cloud-to-Cloud Transforms** - for OEM API data

**Two Types:**
1. **In-Vehicle Transforms** - Decode vehicle bus data (CAN, FleetWise VSS signals)
2. **Cloud-to-Cloud Transforms** - Transform OEM API responses

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Sources                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  IoT Core Simulator                                         │
│  ├─ Already in CMS Signal Catalog format                   │
│  └─ Publishes to: cms-telemetry (no transformation)        │
│                                                              │
│  AWS IoT FleetWise                                          │
│  ├─ VSS signal format                                       │
│  ├─ Publishes to: fleetwise-raw                            │
│  └─ Transform: fleetwise-transform.json                     │
│                                                              │
│  OEM API                                                     │
│  ├─ Custom OEM format (REST, gRPC, etc.)                   │
│  ├─ Publishes to: cms-telemetry-oem                        │
│  ├─ Example: OEM1 (gRPC streaming feed with protobuf)     │
│  └─ Transform: oem1-transform.json (per-OEM manifest)      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│         OEMTelemetryProcessor (Flink)                        │
│  Loads transform manifest dynamically per oem_source field  │
│  Applies: signal mappings, unit conversions, event routing  │
│  Output: cms-telemetry-preprocessed (canonical format)      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              cms-telemetry-preprocessed                      │
│  All data sources converge to CMS Signal Catalog            │
│  {vehicleId, timestamp, oem_source, signal fields…}         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│         Downstream Flink Apps (Unchanged)                   │
│  - TripProcessor: trip open/close per canonical events     │
│  - SafetyProcessor: collision detection, harsh events      │
│  - MaintenanceProcessor: DTC + diagnostic event fusion     │
│  - EventDrivenTelemetryProcessor: fleet routing + Redis    │
└─────────────────────────────────────────────────────────────┘
```

## Files

### Core Schemas
- **`signal-catalog.json`** - CMS standard telemetry format (70+ signals, extensible)
- **`event-catalog.json`** - Diagnostic events and alerts (80+ events)
- **`transform-manifest-schema.json`** - Schema for transformation manifests (v2.1.0)

### Transform Manifests
- **`manifests/oem1-transform.json`** - OEM1 gRPC feed → CMS format. The shipped reference implementation and the default manifest; copy this as the starting point for a new OEM integration.

## Usage

### For IoT Core Simulator (No Transform Needed)
Your simulator already outputs CMS Signal Catalog format. No transformation required.

```python
# Simulator publishes directly to cms-telemetry topic
telemetry = {
    "vin": "1HGBH41JXMN109186",
    "ts": 1729526400,
    "lat": 40.7128,
    "lon": -74.0060,
    "spd": 55.3,
    # ... matches signal-catalog.json
}
```

### For FleetWise Integration
1. Deploy FleetWise campaign with VSS signals
2. Use `manifests/fleetwise-transform.json` in Flink transformer
3. Flink reads from `fleetwise-raw`, transforms, outputs to `cms-telemetry`

### For OEM API Integration
1. Customer copies `manifests/oem1-transform.json` as a reference
2. Customer customizes:
   - API endpoints
   - Authentication credentials (Secrets Manager)
   - Field mappings (source_path → cms_signal)
   - Unit conversions
3. Upload manifest to S3
4. Flink transformer applies manifest dynamically

## Way B Signal Mappings (2026-06-05 Update)

The OEM telemetry pipeline now uses **raw protobuf-as-JSON** shapes from connectors, with signal extraction and compound-signal splitting handled by the manifest layer via JSONPath `source_path` expressions.

**Key changes:**
- `source_path` values now use JSONPath syntax: `field`, `field[N]`, `field[?key=val]` for array indices and tag predicates
- Compound signals (TIRE_PRESSURE, ACCELERATION) are disambiguated via tag predicates in `source_path`, not pre-split by the connector
- Per-signal extraction from nested protobuf JSON structures (e.g., `typedData.value.speedValue.speed`) is now a manifest responsibility
- `manifest_version` remains `2.1.0` — no schema bump; new semantics are path-expression grammar extensions only

See `.kiro/specs/2026-06-05-cms-oem1-connector-flink-shape-mismatch/signal-mapping-draft.md` for the complete 22-signal OEM1 mapping table with proto-accurate JSONPath expressions.

## Transform Manifest Examples

### Simple Field Mapping (Option I Path Resolver)
```json
{
  "source_signal": "SPEED",
  "cms_field": "speed",
  "source_path": "[?signal.wksSignal=SPEED].speedValue.speed",
  "data_type": "float",
  "unit_conversion": "mps_to_mph"
}
```

### Compound Signal with Tag Predicate
```json
{
  "source_signal": "TIRE_PRESSURE_FL",
  "cms_field": "tire_fl",
  "source_path": "[?signal.wksSignal=TIRE_PRESSURE][?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=FRONT_LEFT].doubleValue",
  "data_type": "float",
  "unit_conversion": "kpa_to_psi"
}
```

### Pre-Way-B Example (Deprecated — Kept for Reference)
```json
{
  "cms_signal": "spd",
  "source_path": "vehicle_data.speed_kmh",
  "data_type": "float",
  "required": true,
  "conversion": {
    "type": "multiply",
    "factor": 0.621371,
    "comment": "Convert km/h to mph"
  }
}
```

### Temperature Conversion (Formula)
```json
{
  "cms_signal": "eng_temp",
  "source_path": "engine.coolant_temp_celsius",
  "data_type": "float",
  "conversion": {
    "type": "formula",
    "formula": "value * 1.8 + 32",
    "comment": "Convert Celsius to Fahrenheit"
  }
}
```

### Conditional Mapping (EV Only)
```json
{
  "cms_signal": "soc",
  "source_path": "battery.state_of_charge_percent",
  "data_type": "float",
  "condition": {
    "field": "vehicle_type",
    "operator": "equals",
    "value": "EV"
  }
}
```

### Lookup Table Mapping
```json
{
  "cms_signal": "vt",
  "source_path": "vehicle_status",
  "data_type": "string",
  "conversion": {
    "type": "lookup",
    "lookup_table": {
      "0": "P",
      "1": "I",
      "2": "D"
    }
  }
}
```

## Validation

Transform manifests include validation rules:

```json
{
  "validation": {
    "required_signals": ["vin", "ts", "lat", "lon", "spd"],
    "range_checks": [
      {
        "signal": "spd",
        "min": 0,
        "max": 200
      }
    ]
  }
}
```

## UI Integration

The UI will provide:
1. **Data Source Selector** - Choose IoT Core, FleetWise, or OEM
2. **Manifest Editor** - Visual editor for transform manifests
3. **Manifest Validator** - Test transformations with sample data
4. **Signal Mapper** - Drag-and-drop field mapping

## Benefits

1. **Standardization** - Single format for all downstream processing
2. **Flexibility** - Support any OEM without code changes
3. **Customer Control** - Customers define their own transformations
4. **Validation** - Built-in validation ensures data quality
5. **Versioning** - Manifests stored in S3 with versioning
6. **Auditability** - Raw data preserved in source-specific topics

## Next Steps

1. **Phase 1**: Document existing simulator format (✅ Complete)
2. **Phase 2**: Build generic Flink transformer (reads manifests from S3) (✅ Complete — FWTelemetryProcessor)
3. **Phase 3**: Create FleetWise integration (✅ Complete — full FWE-to-DDB pipeline with campaign sync)
4. **Phase 4**: Create OEM adapter framework
5. **Phase 5**: Build UI for manifest management

## References

- AWS IoT FleetWise: https://docs.aws.amazon.com/iot-fleetwise/
- Vehicle Signal Specification (VSS): https://covesa.github.io/vehicle_signal_specification/
- JSONPath: https://goessner.net/articles/JsonPath/

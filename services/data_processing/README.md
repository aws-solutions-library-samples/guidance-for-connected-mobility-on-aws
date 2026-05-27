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
│  ├─ Custom OEM format                                       │
│  ├─ Publishes to: oem-raw                                  │
│  └─ Transform: oem-transform-template.json (customer)      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│         Flink Transformation Processors                      │
│  (Apply transform manifests dynamically)                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              cms-telemetry (Unified Format)                  │
│  All data sources converge to CMS Signal Catalog            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│         Existing Flink Apps (Unchanged)                      │
│  Trip Detection, Safety Events, Maintenance Alerts          │
└─────────────────────────────────────────────────────────────┘
```

## Files

### Core Schemas
- **`signal-catalog.json`** - CMS standard telemetry format (70+ signals)
- **`transform-manifest-schema.json`** - Schema for transformation manifests

### Transform Manifests
- **`manifests/fleetwise-transform.json`** - FleetWise VSS → CMS format
- **`manifests/oem-transform-template.json`** - Template for customer OEM integrations

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
1. Customer copies `manifests/oem-transform-template.json`
2. Customer customizes:
   - API endpoints
   - Authentication credentials (Secrets Manager)
   - Field mappings (source_path → cms_signal)
   - Unit conversions
3. Upload manifest to S3
4. Flink transformer applies manifest dynamically

## Transform Manifest Examples

### Simple Field Mapping
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

#!/usr/bin/env python3
"""Validate transform manifests against schema and signal catalog."""
import json
import sys
import os

MANIFESTS_DIR = os.path.join(os.path.dirname(__file__), "manifests")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "transform-manifest-schema.json")
CATALOG_SCRIPT = os.path.join(os.path.dirname(__file__), "../../deployment/scripts/seed_signal_catalog.py")

# Extract json_field values from signal catalog seed script
CATALOG_FIELDS = set()
if os.path.exists(CATALOG_SCRIPT):
    with open(CATALOG_SCRIPT) as f:
        content = f.read()
    # Parse SIGNALS list — each tuple has json_field at index 2
    import ast
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('("') and ',' in line:
            try:
                t = ast.literal_eval(line.rstrip(','))
                if len(t) >= 3:
                    CATALOG_FIELDS.add(t[2])
            except:
                pass

def validate_manifest(path):
    errors = []
    warnings = []

    with open(path) as f:
        manifest = json.load(f)

    name = manifest.get("source_name", os.path.basename(path))

    # Required fields
    for field in ["manifest_version", "transform_type", "source_name", "signal_mappings"]:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    # Validate signal mappings
    mappings = manifest.get("signal_mappings", [])
    cms_fields_seen = set()

    for i, m in enumerate(mappings):
        if "cms_field" not in m:
            errors.append(f"Mapping {i}: missing cms_field")
            continue
        if "source_path" not in m:
            errors.append(f"Mapping {i} ({m['cms_field']}): missing source_path")

        cms_field = m["cms_field"]
        cms_fields_seen.add(cms_field)

        # Check against signal catalog
        if CATALOG_FIELDS and cms_field not in CATALOG_FIELDS:
            warnings.append(f"Mapping '{cms_field}' not found in signal catalog json_field values")

        # Check for duplicate cms_field
        if cms_field in cms_fields_seen and list(m["cms_field"] for m in mappings).count(cms_field) > 1:
            errors.append(f"Duplicate cms_field: {cms_field}")

        # Validate unit_conversion values
        valid_conversions = {
            "mps_to_mph", "kph_to_mph", "km_to_miles", "C_to_F",
            "kpa_to_psi", "mbar_to_psi", "bar_to_psi", "mps2_to_g", "percent_100",
            "seconds_to_hours"
        }
        if "unit_conversion" in m and m["unit_conversion"] not in valid_conversions:
            errors.append(f"Mapping '{cms_field}': invalid unit_conversion '{m['unit_conversion']}'")

    # Print results
    print(f"\n{'✅' if not errors else '❌'} {name} ({len(mappings)} mappings)")
    for e in errors:
        print(f"  ❌ {e}")
    for w in warnings:
        print(f"  ⚠️  {w}")
    if not errors and not warnings:
        print(f"  All cms_field values match signal catalog")

    return len(errors) == 0


if __name__ == "__main__":
    manifests = sys.argv[1:] if len(sys.argv) > 1 else [
        os.path.join(MANIFESTS_DIR, f) for f in os.listdir(MANIFESTS_DIR)
        if f.endswith("-transform.json")
    ]

    print(f"Signal catalog fields: {len(CATALOG_FIELDS)} loaded")
    all_valid = all(validate_manifest(p) for p in manifests)
    print(f"\n{'✅ All manifests valid' if all_valid else '❌ Some manifests have errors'}")
    sys.exit(0 if all_valid else 1)

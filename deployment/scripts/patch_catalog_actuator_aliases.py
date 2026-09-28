#!/usr/bin/env python3
"""Patch signal_catalog_seed.json to add camelCase json_field aliases for
actuator-visible fields the simulator emits.

Simulator emits camelCase (`allDoorsLocked`, `chargeDoorOpen`, `doorLFLocked`,
`doorRFLocked`, `doorLRLocked`, `doorRRLocked`, `remoteStartActive`) but the
catalog's `json_field` values are snake_case for the corresponding rows.
`EventDrivenTelemetryProcessor` does exact-key lookup, so mutated actuator
state is silently dropped before Redis. See
`issues/2026-08-03-sim-actuator-field-mapping/report.md`.

Fix approach: append alias rows with the SAME `signal_id` + `vss_path` as the
canonical row, but distinct `signal_name` (suffix `_JsonAlias`) so DDB
composite-key uniqueness is preserved. No `actuator` block — the alias rows
don't appear in `commands_lambda._get_catalog` (which filters on
`attribute_exists(actuator)`) or `_send_command` (same filter). This means the
UI's Remote Commands catalog is unchanged; only Flink's `fieldToId` map picks
up the camelCase keys, closing the silent-drop gap.

Idempotent: re-running is a no-op after the first apply.

Usage:
    python3 deployment/scripts/patch_catalog_actuator_aliases.py [--dry-run]
"""
import argparse
import json
import os
import sys


# (camelCase json_field, canonical snake_case json_field)
ALIASES = [
    ("allDoorsLocked", "all_doors_locked"),
    ("chargeDoorOpen", "charge_door_open"),
    ("doorLFLocked", "door_frontleft_locked"),
    ("doorRFLocked", "door_frontright_locked"),
    ("doorLRLocked", "door_rearleft_locked"),
    ("doorRRLocked", "door_rearright_locked"),
    ("remoteStartActive", "remote_start_active"),
]


def build_alias_row(canonical: dict, alias_json_field: str) -> dict:
    """Build an alias row from a canonical catalog row.

    - Copies signal_id, vss_path, signal_group, data_type, unit
    - Uses `<CanonicalName>_JsonAlias` as signal_name so DDB composite key
      (signal_group, signal_name) does not collide with the canonical row.
    - Sets `status=active` so `SignalCatalogLoader`'s DDB filter admits it.
    - Sets `alias_of_signal_id` to make the row's intent obvious to human
      readers; not consumed by any code path today.
    - Omits `actuator` — alias rows must not appear in the commands catalog
      or _send_command's actuator lookup.
    """
    return {
        "alias_of_signal_id": canonical["signal_id"],
        "data_type": canonical["data_type"],
        "description": (
            f"CamelCase json_field alias of {canonical['signal_name']} "
            f"(signal_id {canonical['signal_id']}). Emitted by the CMS-native "
            "simulator's realtime_telemetry_simulator.py; the canonical row "
            f"uses snake_case json_field '{canonical['json_field']}'."
        ),
        "json_field": alias_json_field,
        "signal_group": canonical["signal_group"],
        "signal_id": canonical["signal_id"],
        "signal_name": f"{canonical['signal_name']}_JsonAlias",
        "status": "active",
        "unit": canonical.get("unit", ""),
        "vss_path": canonical["vss_path"],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--path",
                   default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "signal_catalog_seed.json"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    with open(args.path) as f:
        rows = json.load(f)

    by_jsonfield = {r.get("json_field"): r for r in rows if r.get("json_field")}

    added = []
    skipped_existing_camel = []
    skipped_no_canonical = []

    for camel, snake in ALIASES:
        if camel in by_jsonfield:
            skipped_existing_camel.append(camel)
            continue
        canonical = by_jsonfield.get(snake)
        if canonical is None:
            skipped_no_canonical.append((camel, snake))
            continue
        row = build_alias_row(canonical, camel)
        added.append(row)

    if skipped_no_canonical:
        print("ERROR: no canonical row found for:", file=sys.stderr)
        for camel, snake in skipped_no_canonical:
            print(f"  - {camel} → {snake}", file=sys.stderr)
        return 2

    if not added:
        print(f"No changes needed — {len(skipped_existing_camel)} camelCase "
              f"aliases already present.")
        return 0

    if args.dry_run:
        print(f"Would add {len(added)} rows:")
        for r in added:
            print(f"  json_field={r['json_field']} signal_id={r['signal_id']} "
                  f"signal_name={r['signal_name']}")
        if skipped_existing_camel:
            print(f"Skipped {len(skipped_existing_camel)} already-present: "
                  f"{skipped_existing_camel}")
        return 0

    # Surgical append: insert the new rows right before the closing `]`
    # of the existing file. This preserves ALL existing rows' original
    # formatting/key-order (json.dump would reformat every row and produce
    # a 4000+ line diff for a 7-row addition).
    with open(args.path) as f:
        text = f.read()

    # Find the last non-whitespace char — must be `]`
    stripped = text.rstrip()
    if not stripped.endswith("]"):
        print(f"ERROR: file does not end with `]`: last chars = "
              f"{stripped[-20:]!r}", file=sys.stderr)
        return 2

    # Everything up to and including the final `}` before the `]`
    up_to_close = stripped[:-1].rstrip()  # drop the `]`
    if not up_to_close.endswith("}"):
        print(f"ERROR: expected `}}` before final `]`: last chars = "
              f"{up_to_close[-20:]!r}", file=sys.stderr)
        return 2

    # Serialize the added rows individually with the same indent as
    # existing rows (top-level 2-space, per-field 4-space).
    added_json_pieces = []
    for r in added:
        row_text = json.dumps(r, indent=2)
        # Re-indent all lines by 2 spaces so array-item start reads `  {`
        row_text = "\n".join(("  " + line) if line else line
                             for line in row_text.splitlines())
        added_json_pieces.append(row_text)

    added_block = ",\n" + ",\n".join(added_json_pieces) + "\n"
    new_text = up_to_close + added_block + "]\n"

    with open(args.path, "w") as f:
        f.write(new_text)

    print(f"✅ Added {len(added)} camelCase alias rows to {args.path}")
    for r in added:
        print(f"  json_field={r['json_field']} signal_id={r['signal_id']} "
              f"signal_name={r['signal_name']}")
    if skipped_existing_camel:
        print(f"⏭  Skipped {len(skipped_existing_camel)} already-present: "
              f"{skipped_existing_camel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

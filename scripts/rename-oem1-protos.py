#!/usr/bin/env python3
"""
Rename OEM1 vendor proto namespaces to oem1.* for public-mirror safety.

Reads from OEM1_PROTOS_SDK_DIR (default ~/Downloads/oem1-sdk-extract/...)
Writes cleansed protos to services/connectors/oem1/proto/ (relative to repo root).

Idempotent: re-running on the same SDK dir produces the same output.
"""

import os
import re
import sys
from pathlib import Path

SDK_DIR_DEFAULT = Path.home() / "Downloads/oem1-sdk-extract/au-external-protos/src/main/proto"
REPO_ROOT = Path(__file__).parent.parent
OUT_DIR = REPO_ROOT / "services/connectors/oem1/proto"


def _apply_substitutions(text: str) -> str:
    """Apply all namespace cleansing rules in deterministic order."""

    # 1. Strip proprietary copyright header: /*- ... */ block before `syntax =`
    text = re.sub(r'/\*-.*?\*/\s*', '', text, count=1, flags=re.DOTALL)

    # 2. Strip "// Copyright ... Autonomic Inc." lines (after header strip, these
    #    appear as standalone Apache-license attribution lines)
    text = re.sub(r'// Copyright \d{4}(?:-\d{4})? Autonomic Inc\.[^\n]*\n?', '', text)

    # 3. Strip lines containing "Autonomic, LLC" or "Autonomic Incorporated"
    text = re.sub(r'[^\n]*\b(Autonomic, LLC|Autonomic Incorporated)\b[^\n]*\n?', '', text)

    # 4. Package declarations (exact keyword context)
    # NOTE: The package directives for the vendor feed/telemetry namespaces are
    # intentionally NOT renamed here. The proto `package` declaration maps directly
    # to the gRPC wire-level service path (/<pkg>.<Svc>/<Method>). The vendor server
    # routes on the original namespace; renaming would cause UNIMPLEMENTED errors.
    # See issues/2026-06-03-oem1-proto-rename-breaks-grpc-wire-path for details.
    # These proto trees are excluded from public-mirror via .publish-exclude instead.
    text = re.sub(r'\bpackage ford\.protobuf\b', 'package oem1.id', text)

    # 5. java_package option
    text = re.sub(r'(option java_package = "com\.)autonomic\.', r'\1oem1.', text)

    # 6. go_package option — full path replacement
    text = re.sub(
        r'option go_package = "github\.com/autonomic-ai/external-protos/src/main/proto/autonomic/',
        'option go_package = "github.com/cms/oem1-feed-protos/oem1/',
        text,
    )
    text = re.sub(
        r'option go_package = "github\.com/autonomic-ai/external-protos/src/main/proto/ford/protobuf/',
        'option go_package = "github.com/cms/oem1-feed-protos/oem1_id/',
        text,
    )
    # Catch bare go_package = "ford/protobuf" (uuid.proto)
    text = re.sub(
        r'option go_package = "ford/protobuf"',
        'option go_package = "github.com/cms/oem1-feed-protos/oem1_id"',
        text,
    )
    # Any remaining autonomic-ai path
    text = re.sub(
        r'option go_package = "github\.com/autonomic-ai/',
        'option go_package = "github.com/cms/oem1-feed-protos/',
        text,
    )

    # 7. csharp_namespace option — two patterns
    text = re.sub(r'(option csharp_namespace = ")Autonomic\.', r'\1OEM1.', text)
    # "Autonomic.Ext.Ford" -> "OEM1.Ext.Id"
    text = re.sub(r'OEM1\.Ext\.Ford\b', 'OEM1.Ext.Id', text)

    # 8. Import path renames
    # NOTE: import "autonomic/..." paths are intentionally NOT renamed to "oem1/...".
    # Since _dest_path keeps proto files under autonomic/ (for wire-path correctness),
    # the import paths must match the actual filesystem layout.
    # ford/ dir renamed to oem1_id/ — keep subpath intact
    text = re.sub(r'import "ford/', 'import "oem1_id/', text)

    # 9. Fully-qualified type references in proto body (dotted paths)
    # NOTE: Body references into the vendor's feed/telemetry namespace are intentionally
    # NOT renamed (they stay as-is in the vendor protos, matching the unrenamed package
    # declarations). Only cloudingest and ford.protobuf body refs are scrubbed below.
    text = re.sub(r'\bautonomic\.cloudingest\.', 'oem1.cloudingest.', text)
    text = re.sub(r'\bford\.protobuf\.', 'oem1.id.', text)
    # ford.tmc.* references in comments
    text = re.sub(r'\bford\.tmc\.', 'oem1.tmc.', text)

    # 10. Inline comment cleanups
    text = text.replace("Ford Consent Platform", "Consent Platform")
    text = re.sub(r'\bTMC group memberships\b', 'OEM group memberships', text)
    # "midway" appears as an English word in vendor proto comments (clock-offset
    # estimation, "midway point between client times"). The Midway-gate canary
    # is a substring match in `.publish-secrets-scan.yml` forbidden_strings, so
    # this English usage is a false positive. Replace with "midpoint" — same
    # meaning, no canary collision. Case-preserving.
    text = re.sub(r'\bmidway point\b', 'midpoint', text)
    text = re.sub(r'\bMidway point\b', 'Midpoint', text)
    text = re.sub(r'\bmidway\b', 'midpoint', text)
    text = re.sub(r'\bMidway\b', 'Midpoint', text)

    # 10b. Vendor-attributing English narrative tokens (added 2026-06-02 per
    # Group A2 security-review Cycle 3 Warning). The vendor's protos use:
    #   - "TMC" / "tmc" — branded platform name (92 refs in cleansed protos)
    #   - "redeef" — vendor-internal Cloud Ingest service codename (4 refs)
    #   - "Au" — vendor abbreviation for Autonomic in narrative comments (~25 refs)
    #   - objc_class_prefix = "AUT" — vendor-class-prefix for ObjC bindings (174 refs)
    # All are word-bounded substitutions. Substitutions run AFTER structural renames
    # (package, import, file path) so they don't double-substitute. Case-preserving.
    # NOTE on 'TMC' regex (security-review Cycle 4): Python \b treats `_` as a word
    # character, so `\bTMC\b` does NOT match underscore-bounded compound enum
    # identifiers like `TMC_TEST_VEHICLE`, `INTERNAL_TMC_ERROR`,
    # `DELIVERY_FROM_TMC_QUEUED`. Use non-letter-number lookbehind plus
    # non-letter lookahead so the pattern matches both bare 'TMC' and underscored
    # compound forms without falling through to camelCase identifiers (XTMCY).
    text = re.sub(r'(?<![A-Za-z0-9])TMC(?![A-Za-z])', 'OEM1', text)
    text = re.sub(r'(?<![A-Za-z0-9])tmc(?![A-Za-z])', 'oem1', text)
    text = re.sub(r'\bredeef\b', 'oem1cloud', text, flags=re.IGNORECASE)
    text = re.sub(r'\bAu\b', 'OEM1', text)  # capital-A lowercase-u; vendor abbrev
    # objc_class_prefix value swap: "AUT" -> "OEM1". Replaces only the literal
    # quoted prefix value, leaves Google-prefixed (GTP/RPC/GAPI) entries alone.
    text = re.sub(r'(option\s+objc_class_prefix\s*=\s*)"AUT"', r'\1"OEM1"', text)

    # 11. Catch-all: word-bounded brand tokens in comments/strings
    # These run AFTER structural renames so they don't double-substitute.
    # IMPORTANT: The vendor's feed/telemetry namespace (package directive and type refs)
    # must stay untouched for wire-path correctness. Use negative lookaheads to
    # skip any "autonomic" token that is immediately followed by "." or "/" (i.e.,
    # the namespace prefix in package lines, type refs, or import paths).
    # See issues/2026-06-03-oem1-proto-rename-breaks-grpc-wire-path.
    # "Autonomic" (capital) in comments -> "OEM1" (skip namespace prefix occurrences)
    text = re.sub(r'\bAutonomic\b(?!\.ext\b|[/.])', 'OEM1', text)
    # "autonomic" (lowercase) in comments -> "oem1" (skip namespace prefix occurrences)
    text = re.sub(r'\bautonomic\b(?!\.ext\b|[/.])', 'oem1', text)
    # "Ford" (capital) in comments -> "OEM1" (word-bounded to avoid FordPro, ForDriver etc.)
    text = re.sub(r'\bFord\b', 'OEM1', text)
    # "ford" (lowercase) in comments -> "oem1"
    text = re.sub(r'\bford\b', 'oem1', text)
    # "lincoln" / "Lincoln" -> "oem1"
    text = re.sub(r'\bLincoln\b', 'OEM1', text)
    text = re.sub(r'\blincoln\b', 'oem1', text)
    # "fcsfleet" / "FCSFleet"
    text = re.sub(r'\bFCSFleet\b', 'OEM1', text)
    text = re.sub(r'\bfcsfleet\b', 'oem1', text)
    # "tmc-external"
    text = re.sub(r'\btmc-external\b', 'oem1-external', text)

    return text


def _dest_path(src_path: Path, sdk_root: Path) -> Path:
    """Map src path under sdk_root to output path under OUT_DIR, renaming top-level dirs."""
    rel = src_path.relative_to(sdk_root)
    parts = list(rel.parts)
    # NOTE: autonomic/ is intentionally NOT renamed to oem1/ here.
    # The generated stubs must live under autonomic/ so Python import paths match
    # the vendor's proto package declarations (gRPC wire-path correctness).
    # The proto/ and _generated/autonomic/ trees are excluded from public-mirror
    # via .publish-exclude. See issues/2026-06-03-oem1-proto-rename-breaks-grpc-wire-path.
    if parts[0] == 'ford':
        parts[0] = 'oem1_id'
    # autonomic/ and google/ stay as-is
    return OUT_DIR / Path(*parts)


def process(sdk_dir: Path) -> int:
    if not sdk_dir.exists():
        print(f"ERROR: SDK dir not found: {sdk_dir}", file=sys.stderr)
        return 1

    proto_files = list(sdk_dir.rglob("*.proto"))
    if not proto_files:
        print(f"ERROR: No .proto files found under {sdk_dir}", file=sys.stderr)
        return 1

    for src in proto_files:
        content = src.read_text(encoding='utf-8')
        cleaned = _apply_substitutions(content)
        dest = _dest_path(src, sdk_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cleaned, encoding='utf-8')

    print(f"Wrote {len(proto_files)} cleansed proto(s) to {OUT_DIR}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sdk_dir = Path(os.environ.get('OEM1_PROTOS_SDK_DIR', str(SDK_DIR_DEFAULT))).expanduser()
    sys.exit(process(sdk_dir))

#!/usr/bin/env bash
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~/.kiro/publish-toolkit/verify-sync.sh
#
# Drift-detection tool for publish-toolkit vendored files.
#
# Usage:
#   verify-sync.sh <consumer-repo-dir> [--check-canonical] [--canonical-root <path>]
#
#   <consumer-repo-dir>         Absolute path to the consumer repo root
#   --check-canonical           Also compare vendored files against the canonical
#                               source. No-ops silently when --canonical-root is absent.
#   --canonical-root <path>     Override the canonical path
#                               (default: ~/.kiro/publish-toolkit/)
#
# Exit codes:
#   0  all checks passed (clean)
#   1  drift detected (file hash mismatch)
#   2  anchor file missing or malformed
#
# Drift output format (on exit 1):
#   DRIFT: <relative-file-path>
#     anchor:   sha256:<hex>
#     actual:   sha256:<hex>
#
# Dependencies: bash (3.2+), sha256sum / shasum, python3 / grep / sed / awk
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
CONSUMER_DIR=""
CHECK_CANONICAL=0
CANONICAL_ROOT="${HOME}/.kiro/publish-toolkit"

_usage() {
  printf "Usage: verify-sync.sh <consumer-dir> [--check-canonical] [--canonical-root <path>]\n" >&2
  exit 2
}

if [ $# -lt 1 ]; then
  _usage
fi
CONSUMER_DIR="$1"
shift

while [ $# -gt 0 ]; do
  case "$1" in
    --check-canonical)
      CHECK_CANONICAL=1; shift ;;
    --canonical-root)
      if [ $# -lt 2 ]; then
        printf "verify-sync.sh: --canonical-root requires an argument\n" >&2; exit 2
      fi
      CANONICAL_ROOT="$2"; shift 2 ;;
    *)
      printf "verify-sync.sh: unknown argument: %s\n" "$1" >&2
      _usage ;;
  esac
done

# --------------------------------------------------------------------------
# Validate consumer dir
# --------------------------------------------------------------------------
if [ -z "$CONSUMER_DIR" ]; then
  printf "verify-sync.sh: consumer repo directory is required\n" >&2
  _usage
fi

if [ ! -d "$CONSUMER_DIR" ]; then
  printf "verify-sync.sh: consumer directory does not exist: %s\n" "$CONSUMER_DIR" >&2
  exit 2
fi

# --------------------------------------------------------------------------
# sha256 portable helper
# --------------------------------------------------------------------------
_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# --------------------------------------------------------------------------
# Locate anchor file
# --------------------------------------------------------------------------
ANCHOR="${CONSUMER_DIR}/scripts/lib/.publish-toolkit-sync"

if [ ! -f "$ANCHOR" ]; then
  printf "verify-sync.sh: anchor file missing: %s\n" "$ANCHOR" >&2
  printf "Run ~/.kiro/publish-toolkit/sync.sh to create it.\n" >&2
  exit 2
fi

# --------------------------------------------------------------------------
# Parse anchor file — extract file_hashes entries
#
# COUPLING NOTE: this is a line-oriented parser over pseudo-YAML, not a YAML
# parser (the drift check must run with only bash + a sha256 binary available,
# including on CI runners with no python/PyYAML). It therefore depends on the
# anchor SHAPE that sync.sh emits: `file_hashes:` on its own line, followed by
# entries indented exactly two spaces as `  <relative/path>: "sha256:<hex>"`.
# Adding files to the sync set needs no change here PROVIDED sync.sh keeps
# emitting that shape. If the anchor format is ever changed (nested keys, flow
# mappings, comments inside the block), this parser must change with it — and
# both sides are exercised by tests/run-tests.sh, so a mismatch fails there.
#
# Anchor format:
#   file_hashes:
#     scripts/publish-to-github.sh: "sha256:<hex>"
#     scripts/lib/secret-scan.py: "sha256:<hex>"
#     ...
#
# We use python3 to parse the YAML safely (pyyaml may not be available,
# so we use a hand-rolled grep approach in pure bash 3.2).
#
# For each line under file_hashes: that matches the pattern
#   <whitespace><key>: "sha256:<hex>"
# we extract key and hex.
#
# Returns lines of the form: <key>|<hex>
# --------------------------------------------------------------------------
_parse_anchor_entries() {
  local in_block=0
  while IFS= read -r line; do
    # Detect start of file_hashes block
    case "$line" in
      file_hashes:*)
        in_block=1
        continue ;;
    esac

    if [ "$in_block" -eq 1 ]; then
      # Check if this is still an indented line (file hash entry)
      case "$line" in
        [[:space:]]*)
          # Looks like an indented entry — extract key and sha256 value
          # Key: trim leading whitespace, strip trailing ": ..."
          local key val
          key=$(printf '%s' "$line" | sed 's/^[[:space:]]*//' | sed 's/:[[:space:]].*//')
          val=$(printf '%s' "$line" | grep -oE 'sha256:[a-f0-9]+' | sed 's/sha256://')
          if [ -n "$key" ] && [ -n "$val" ]; then
            printf '%s|%s\n' "$key" "$val"
          fi
          ;;
        *)
          # Non-indented line — end of file_hashes block
          in_block=0
          ;;
      esac
    fi
  done < "$ANCHOR"
}

# Collect parsed entries into a temp file (bash 3.2 safe alternative to arrays)
PARSED_ENTRIES=$(mktemp)
trap 'rm -f "$PARSED_ENTRIES"' EXIT

_parse_anchor_entries > "$PARSED_ENTRIES"

if [ ! -s "$PARSED_ENTRIES" ]; then
  printf "verify-sync.sh: anchor file malformed (no file_hashes entries found): %s\n" "$ANCHOR" >&2
  exit 2
fi

# --------------------------------------------------------------------------
# Check each vendored file against its anchor hash
# --------------------------------------------------------------------------
DRIFT_COUNT=0

while IFS='|' read -r rel_path expected_sha; do
  # Skip empty lines
  [ -n "$rel_path" ] || continue
  [ -n "$expected_sha" ] || continue

  abs_path="${CONSUMER_DIR}/${rel_path}"

  if [ ! -f "$abs_path" ]; then
    printf "DRIFT: %s\n" "$rel_path"
    printf "  anchor:   sha256:%s\n" "$expected_sha"
    printf "  actual:   (file missing)\n"
    DRIFT_COUNT=$(( DRIFT_COUNT + 1 ))
    continue
  fi

  actual_sha=$(_sha256 "$abs_path")

  if [ "$actual_sha" != "$expected_sha" ]; then
    printf "DRIFT: %s\n" "$rel_path"
    printf "  anchor:   sha256:%s\n" "$expected_sha"
    printf "  actual:   sha256:%s\n" "$actual_sha"
    DRIFT_COUNT=$(( DRIFT_COUNT + 1 ))
  fi
done < "$PARSED_ENTRIES"

# --------------------------------------------------------------------------
# --check-canonical: compare vendored files to canonical source
#
# No-ops silently if CANONICAL_ROOT does not exist (CI-safe).
# The mapping from anchor key to canonical file:
#   scripts/publish-to-github.sh          → <canonical>/publish-to-github.sh
#   scripts/lib/secret-scan.py            → <canonical>/lib/secret-scan.py
#   scripts/lib/test_secret_scan.py       → <canonical>/lib/test_secret_scan.py
#   scripts/lib/verify-publish-toolkit-sync.sh → <canonical>/verify-sync.sh
# --------------------------------------------------------------------------
if [ "$CHECK_CANONICAL" -eq 1 ]; then
  if [ ! -d "$CANONICAL_ROOT" ]; then
    # Canonical absent — no-op silently (CI runner without dotkiro)
    true
  else
    # Walk the same parsed entries and compare to canonical
    while IFS='|' read -r rel_path expected_sha; do
      [ -n "$rel_path" ] || continue

      # Map consumer relative path → canonical file path
      local_canonical_file=""
      case "$rel_path" in
        scripts/publish-to-github.sh)
          local_canonical_file="${CANONICAL_ROOT}/publish-to-github.sh" ;;
        scripts/lib/secret-scan.py)
          local_canonical_file="${CANONICAL_ROOT}/lib/secret-scan.py" ;;
        scripts/lib/test_secret_scan.py)
          local_canonical_file="${CANONICAL_ROOT}/lib/test_secret_scan.py" ;;
        scripts/lib/test_publish_scan_exclude_invariant.py)
          local_canonical_file="${CANONICAL_ROOT}/lib/test_publish_scan_exclude_invariant.py" ;;
        scripts/lib/verify-publish-toolkit-sync.sh)
          local_canonical_file="${CANONICAL_ROOT}/verify-sync.sh" ;;
        *)
          # Unknown mapping — skip
          continue ;;
      esac

      if [ ! -f "$local_canonical_file" ]; then
        # Canonical doesn't have this file — skip silently
        continue
      fi

      consumer_abs="${CONSUMER_DIR}/${rel_path}"

      if [ ! -f "$consumer_abs" ]; then
        printf "CANONICAL-DRIFT: %s (consumer file missing)\n" "$rel_path"
        DRIFT_COUNT=$(( DRIFT_COUNT + 1 ))
        continue
      fi

      canonical_sha=$(_sha256 "$local_canonical_file")
      consumer_sha=$(_sha256 "$consumer_abs")

      if [ "$canonical_sha" != "$consumer_sha" ]; then
        printf "CANONICAL-DRIFT: %s\n" "$rel_path"
        printf "  canonical: sha256:%s\n" "$canonical_sha"
        printf "  consumer:  sha256:%s\n" "$consumer_sha"
        DRIFT_COUNT=$(( DRIFT_COUNT + 1 ))
      fi
    done < "$PARSED_ENTRIES"
  fi
fi

# --------------------------------------------------------------------------
# Final exit
# --------------------------------------------------------------------------
if [ "$DRIFT_COUNT" -gt 0 ]; then
  printf "\nverify-sync.sh: %d drift(s) detected\n" "$DRIFT_COUNT" >&2
  printf "Run ~/.kiro/publish-toolkit/sync.sh to resync.\n" >&2
  exit 1
fi

exit 0

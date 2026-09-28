#!/usr/bin/env bash
# scan-staged-publish.sh — would the STAGED changes leak anything if published?
#
# WHY A STAGED-ONLY VARIANT EXISTS
# --------------------------------
# `verify-publish-safety.sh` is the authoritative gate and scans the whole
# publishable tree. It takes ~87 seconds, which is fine for CI and for a
# deliberate pre-push check and far too slow to run on every commit — a gate
# people disable is not a gate.
#
# This script scans only the files in the index, which is both fast (typically
# <2s) and sufficient for the failure mode it targets: a developer committing a
# NEW or MODIFIED file that ships and contains a finding. That is exactly the
# defect that shipped on 2026-09-20 — a guard test whose positive control
# spelled out a bare 12-digit account id, in a file that ships — and it was
# caught by a hand-run whole-tree scan rather than by anything automatic.
# See issues/2026-09-16-publish-scanner-9-critical-findings-in-guard-test-files/.
#
# WHAT IT DELIBERATELY DOES NOT DO
# --------------------------------
# It cannot catch a leak that arises from the INTERACTION of a staged file with
# an unstaged one, and it does not re-scan files you did not touch. Those remain
# the whole-tree gate's job:
#   - `make verify-publish`                     (local, whole tree, ~87s)
#   - CI job `verify-publish-archive-clean`     (every push)
#   - `publish-to-github.sh`                    (release)
# This is the cheap early net, not a replacement for any of them.
#
# EXCLUSION SEMANTICS
# -------------------
# A staged file only matters if it would actually SHIP. Rather than
# reimplementing that judgement, this builds a tree containing just the staged
# files and applies the SAME `.publish-exclude` strip that
# `publish-to-github.sh` applies, then scans whatever survives. If the strip
# logic there changes, this follows it by construction.
#
# Note the asymmetry that makes this correct: `.publish-exclude` means "does not
# ship" (so a hit is irrelevant), whereas `scan_exclude` in
# `.publish-secrets-scan.yml` means "the scanner does not read it" — a file that
# is scan_excluded but not publish_excluded ships UNEXAMINED. This script honours
# the scanner's own config, so it inherits that blind spot; closing it is
# `test_publish_scan_exclude_invariant.py`'s job, which CI runs separately.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

SCANNER="scripts/lib/secret-scan.py"
CONFIG=".publish-secrets-scan.yml"
EXCLUDE_FILE=".publish-exclude"

# Missing tooling must fail the commit, not silently pass it. A guard that
# disappears when a path changes is worse than no guard, because the absence is
# invisible.
for required in "$SCANNER" "$CONFIG"; do
  if [ ! -f "$required" ]; then
    echo "scan-staged-publish: FAIL — required file missing: $required" >&2
    echo "  Not skipping: a publish-safety check that silently no-ops is not a check." >&2
    exit 1
  fi
done

# Added / Copied / Modified only. Deletions and renames-away cannot introduce a
# finding, and a deleted path has no content to scan.
STAGED="$(git diff --cached --name-only --diff-filter=ACM)"
if [ -z "$STAGED" ]; then
  exit 0
fi

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/scan-staged-publish.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

# Materialise the INDEX content, not the working tree. Those differ whenever
# something is staged and then edited further, and the index is what is about to
# be committed.
COUNT=0
while IFS= read -r path; do
  [ -z "$path" ] && continue
  mkdir -p "$STAGING/$(dirname "$path")"
  if git show ":$path" > "$STAGING/$path" 2>/dev/null; then
    COUNT=$((COUNT + 1))
  else
    # Binary or unreadable from the index — nothing useful to scan.
    rm -f "$STAGING/$path"
  fi
done <<< "$STAGED"

[ "$COUNT" -eq 0 ] && exit 0

# Apply .publish-exclude with the same three strategies as publish-to-github.sh:
# direct path, glob, then basename match.
if [ -f "$EXCLUDE_FILE" ]; then
  while IFS= read -r pattern || [ -n "$pattern" ]; do
    case "$pattern" in \#*) continue ;; esac
    [ -z "${pattern// }" ] && continue
    pc="${pattern%/}"; pc="${pc#/}"
    [ -z "$pc" ] && continue
    [ -e "$STAGING/$pc" ] && rm -rf "$STAGING/$pc"
    case "$pc" in
      *"*"*|*"?"*)
        for match in "$STAGING"/$pc; do [ -e "$match" ] && rm -rf "$match"; done
        ;;
    esac
    case "$pc" in
      */*) ;;
      *) find "$STAGING" -name "$pc" -prune -exec rm -rf {} + 2>/dev/null || true ;;
    esac
  done < "$EXCLUDE_FILE"
fi

SHIPPING="$(find "$STAGING" -type f | wc -l | tr -d ' ')"
if [ "$SHIPPING" -eq 0 ]; then
  # Everything staged is publish-excluded. Nothing to say.
  exit 0
fi

OUTPUT="$(python3 "$SCANNER" --config "$CONFIG" --root "$STAGING" --pretty 2>&1)" || true

if printf '%s' "$OUTPUT" | grep -q '"clean": true'; then
  exit 0
fi

cat >&2 <<BANNER

  ┌────────────────────────────────────────────────────────────────────────┐
  │  COMMIT BLOCKED — a staged file that SHIPS carries a scanner finding.  │
  └────────────────────────────────────────────────────────────────────────┘

$OUTPUT

  Scanned $SHIPPING of $COUNT staged file(s) — the rest are .publish-exclude'd.

  Fix the CONTENT. Specifically do NOT:
    - add a scan_exclude entry — the file would then ship UNEXAMINED, which is
      strictly worse. See ~/.kiro/steering/public-mirror-publish.md, which
      records four separate exposures caused by exactly that.
    - add an allowlist entry in .publish-secrets-scan.yml unless the value is
      genuinely a public placeholder. Every allowlist entry PUBLISHES the value
      it names.

  If the finding is a false positive — a 12-digit run inside a float, a
  placeholder shaped like a real id — reword the content so the run does not
  appear. Two real cases resolved that way rather than by suppression: a float
  literal in a docstring, written truncated as 4.00024414... precisely because
  spelling it in full is itself a finding (this banner tripped on it during
  development, which is how the hook first proved it works on live content);
  and a positive control's planted account id assembled from two shorter
  literals.

  To bypass for a genuine emergency (and then fix it immediately):
    git commit --no-verify
BANNER
exit 1

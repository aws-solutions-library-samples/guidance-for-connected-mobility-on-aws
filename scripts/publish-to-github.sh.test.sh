#!/usr/bin/env bash
# publish-to-github.sh.test.sh — smoke test for publish-to-github.sh
# Usage: bash scripts/publish-to-github.sh.test.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
PUBLISH_SCRIPT="${SCRIPT_DIR}/publish-to-github.sh"

PASS=0
FAIL=0
TEST_TAG="v0.0.0-publish-smoke-test"

pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*" >&2; FAIL=$((FAIL + 1)); }

cleanup_tag() {
  git -C "$REPO_ROOT" tag -d "$TEST_TAG" 2>/dev/null || true
}
trap cleanup_tag EXIT

echo "=== publish-to-github.sh smoke tests ==="
echo ""

# ── Test 1: missing --tag exits 2 ────────────────────────────────────────────
echo "Test 1: missing --tag exits with code 2"
EXIT_CODE=0
bash "$PUBLISH_SCRIPT" 2>/dev/null || EXIT_CODE=$?
if [[ $EXIT_CODE -eq 2 ]]; then pass "exit code 2"; else fail "expected 2, got $EXIT_CODE"; fi

# ── Test 2: non-existent tag exits 2 ─────────────────────────────────────────
echo "Test 2: non-existent tag exits with code 2"
EXIT_CODE=0
bash "$PUBLISH_SCRIPT" --tag "v9.9.9-does-not-exist" 2>/dev/null || EXIT_CODE=$?
if [[ $EXIT_CODE -eq 2 ]]; then pass "exit code 2"; else fail "expected 2, got $EXIT_CODE"; fi

# ── Test 3: dirty working tree exits 2 ───────────────────────────────────────
echo "Test 3: dirty working tree exits with code 2"
# Create a throwaway tag first so tag validation passes
cleanup_tag
git -C "$REPO_ROOT" tag -a "$TEST_TAG" -m "Smoke test tag — delete me" HEAD
TMPFILE="${REPO_ROOT}/.smoke-test-dirty-$$"
echo "dirty" > "$TMPFILE"
git -C "$REPO_ROOT" add "$TMPFILE"  # stage it so it shows as a tracked change
EXIT_CODE=0
bash "$PUBLISH_SCRIPT" --tag "$TEST_TAG" 2>/dev/null || EXIT_CODE=$?
git -C "$REPO_ROOT" rm -f "$TMPFILE" >/dev/null 2>&1 || rm -f "$TMPFILE"
if [[ $EXIT_CODE -eq 2 ]]; then pass "exit code 2 on dirty tree"; else fail "expected 2, got $EXIT_CODE"; fi

# ── Test 4: --dry-run produces expected output sections ──────────────────────
echo "Test 4: --dry-run produces summary output"

# Stash any uncommitted changes so the working tree is clean for this test
STASH_CREATED=false
if ! git -C "$REPO_ROOT" diff --quiet 2>/dev/null || ! git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
  git -C "$REPO_ROOT" stash push -u -m "publish-smoke-test-stash" >/dev/null 2>&1 && STASH_CREATED=true
fi

# Create stub config files if they don't exist yet (Groups 3a/3b may not be done)
# These are created AFTER stashing so they don't get stashed away
STUB_EXCLUDE=false
STUB_SCAN=false
if [[ ! -f "${REPO_ROOT}/.publish-exclude" ]]; then
  echo "# smoke-test stub" > "${REPO_ROOT}/.publish-exclude"
  STUB_EXCLUDE=true
fi
if [[ ! -f "${REPO_ROOT}/.publish-secrets-scan.yml" ]]; then
  cat > "${REPO_ROOT}/.publish-secrets-scan.yml" <<'YAML'
# smoke-test stub
forbidden_patterns: {}
forbidden_strings: []
scan_exclude: []
YAML
  STUB_SCAN=true
fi

cleanup_tag
git -C "$REPO_ROOT" tag -a "$TEST_TAG" -m "Smoke test tag — delete me" HEAD

OUTPUT="$(bash "$PUBLISH_SCRIPT" --tag "$TEST_TAG" --dry-run --yes 2>&1)" && EXIT_CODE=0 || EXIT_CODE=$?

# Remove stub files before restoring stash
$STUB_EXCLUDE && rm -f "${REPO_ROOT}/.publish-exclude"
$STUB_SCAN && rm -f "${REPO_ROOT}/.publish-secrets-scan.yml"

# Restore stash
if $STASH_CREATED; then
  git -C "$REPO_ROOT" stash pop >/dev/null 2>&1 || true
fi

if [[ $EXIT_CODE -eq 0 ]]; then pass "dry-run exits 0"; else fail "dry-run exited $EXIT_CODE"; fi

if echo "$OUTPUT" | grep -q "DRY-RUN SUMMARY"; then
  pass "output contains DRY-RUN SUMMARY"
else
  fail "output missing DRY-RUN SUMMARY"
fi

if echo "$OUTPUT" | grep -q "Files before:"; then
  pass "output contains 'Files before:'"
else
  fail "output missing 'Files before:'"
fi

if echo "$OUTPUT" | grep -q "Files after:"; then
  pass "output contains 'Files after:'"
else
  fail "output missing 'Files after:'"
fi

if echo "$OUTPUT" | grep -q "Scanner:"; then
  pass "output contains 'Scanner:'"
else
  fail "output missing 'Scanner:'"
fi

if echo "$OUTPUT" | grep -q "Staging dir:"; then
  pass "output contains 'Staging dir:'"
else
  fail "output missing 'Staging dir:'"
fi

if echo "$OUTPUT" | grep -q "Dry-run complete"; then
  pass "output contains 'Dry-run complete'"
else
  fail "output missing 'Dry-run complete'"
fi

# ── Results ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || exit 1

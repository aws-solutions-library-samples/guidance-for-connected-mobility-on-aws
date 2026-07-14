#!/usr/bin/env bash
# Inline behavioral test for the orchestrator's skip-teardown-on-PASS gate.
#
# Spec: .kiro/specs/2026-06-17-cms-tokyo-clean-redeploy/spec.md (OQ-1=B)
# Patch site: deployment/scripts/run_clean_deploy_test.sh::teardown_and_audit
#
# This test reproduces the gate logic in isolation and exercises both branches:
# - opt-in env var unset/0 → never skip (legacy default)
# - opt-in=1 + all PASS + exit 0 → skip
# - opt-in=1 + any FAIL → no skip (preserves error-path teardown)
# - opt-in=1 + nonzero exit → no skip
# - empty phases + opt-in + exit 0 → vacuously skip (defensive — only reachable
#   if trap fires before any phase records, which doesn't happen in practice)
#
# Bash 3.2 portable (parallel arrays, no associatives). Run:
#   bash deployment/scripts/test_run_clean_deploy_test.sh

set -euo pipefail

# --- Mock the parallel arrays + helpers from the orchestrator -----------------
PHASE_NAMES=()
PHASE_VERDICTS=()
record_phase() { PHASE_NAMES+=("$1"); PHASE_VERDICTS+=("$2"); }

# --- Reproduce the patched gate logic verbatim (parameterized for testability)
should_skip_teardown() {
  local exit_code=$1
  local skip_teardown=0
  if [ "${exit_code}" -eq 0 ] && [ "${CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS:-0}" = "1" ]; then
    local all_pass=1
    local i
    for i in "${!PHASE_NAMES[@]}"; do
      case "${PHASE_VERDICTS[$i]}" in
        PASS|SKIP) ;;
        *) all_pass=0; break ;;
      esac
    done
    if [ "${all_pass}" -eq 1 ]; then skip_teardown=1; fi
  fi
  echo "${skip_teardown}"
}

reset() { PHASE_NAMES=(); PHASE_VERDICTS=(); }

# --- Test cases ---------------------------------------------------------------
fails=0
assert() {
  local name="$1" expected="$2" actual="$3"
  if [ "${expected}" = "${actual}" ]; then
    echo "PASS ${name}"
  else
    echo "FAIL ${name}: expected=${expected} actual=${actual}"
    fails=$((fails + 1))
  fi
}

# Case 1: opt-in unset → never skip, even on full PASS exit 0
reset; record_phase a PASS; record_phase b PASS
unset CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS
assert "case1 opt-in unset → no skip" "0" "$(should_skip_teardown 0)"

# Case 2: opt-in=1 + all PASS|SKIP + exit 0 → skip
reset; record_phase a PASS; record_phase b PASS; record_phase c SKIP
export CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1
assert "case2 opt-in + all PASS/SKIP → skip" "1" "$(should_skip_teardown 0)"

# Case 3: opt-in=1 + any FAIL + exit 0 → no skip
reset; record_phase a PASS; record_phase b FAIL
export CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1
assert "case3 opt-in + any FAIL → no skip" "0" "$(should_skip_teardown 0)"

# Case 4: opt-in=1 + all PASS + exit_code != 0 → no skip
reset; record_phase a PASS; record_phase b PASS
export CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1
assert "case4 nonzero exit → no skip" "0" "$(should_skip_teardown 1)"

# Case 5: opt-in=0 (explicit) + all PASS → no skip
reset; record_phase a PASS
export CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=0
assert "case5 opt-in=0 explicit → no skip" "0" "$(should_skip_teardown 0)"

# Case 6: empty phases + opt-in + exit 0 → vacuously skip (defensive corner)
reset
export CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1
assert "case6 empty phases + opt-in + exit 0 → skip (vacuous)" "1" "$(should_skip_teardown 0)"

# --- Summary -----------------------------------------------------------------
echo ""
if [ "${fails}" -eq 0 ]; then
  echo "ALL 6 TEST CASES PASS"
  exit 0
else
  echo "${fails} TEST CASE(S) FAILED"
  exit 1
fi

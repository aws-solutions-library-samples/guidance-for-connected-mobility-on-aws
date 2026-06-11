#!/usr/bin/env bash
#
# test_run_clean_deploy_test.sh — regression tests for `run_phase()`
# and related verdict-tracking helpers in `run_clean_deploy_test.sh`.
#
# Guards against the phase-detection bug documented in
# `issues/2026-06-03-clean-deploy-orchestrator-phase-detection-bug/`:
# the failure branch of `run_phase()` was capturing `$?` AFTER an
# `if "$@"; then …; fi`, which per the bash manual returns 0 when no
# consequence-branch executes — silently masking the wrapped
# command's real exit code and turning every `run_phase … || exit 1`
# safety net into dead code.
#
# All tests run locally with no AWS access; AWS-touching helpers are
# stubbed. The tests source the real `run_phase()` implementation
# from `run_clean_deploy_test.sh` via an awk extract so the
# orchestrator and the regression suite stay in sync.
#
# Runs on stock macOS bash 3.2 — no associative arrays, no `mapfile`,
# no `<<<` here-strings.
#
# Usage:
#   bash deployment/scripts/test_run_clean_deploy_test.sh
#   echo $?   # 0 = all tests passed; 1 = at least one failed
#
# Exit codes:
#   0    all test cases pass
#   1    at least one test case failed an assertion
#   2    test infrastructure error (e.g. orchestrator file missing)
#
# Non-interactive per ~/.kiro/steering/non-interactive.md.

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ORCHESTRATOR="${SCRIPT_DIR}/run_clean_deploy_test.sh"

if [ ! -f "${ORCHESTRATOR}" ]; then
  echo "FATAL: orchestrator missing at ${ORCHESTRATOR}" >&2
  exit 2
fi

FAIL_COUNT=0
PASS_COUNT=0
TOTAL_COUNT=0

red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
# shellcheck disable=SC2329  # currently unused — kept for future warn-level cases
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
blue()   { printf '\033[0;34m%s\033[0m\n' "$*"; }

assert_eq() {
  local desc="$1"
  local expected="$2"
  local actual="$3"
  TOTAL_COUNT=$((TOTAL_COUNT + 1))
  if [ "${expected}" = "${actual}" ]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    green "  ✓ ${desc}"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    red "  ✗ ${desc}"
    red "      expected: ${expected}"
    red "      actual:   ${actual}"
  fi
}

# Extract just the `run_phase` function definition from the
# orchestrator. Uses an awk range from the opening line `run_phase() {`
# to the matching closing `^}` line. Sourcing the whole orchestrator
# is not viable: it has top-level side effects (sources clean-deploy.env,
# calls AWS CLI, sets traps).
extract_run_phase() {
  awk '/^run_phase\(\) \{/{flag=1} flag{print} flag && /^\}/{flag=0; exit}' \
    "${ORCHESTRATOR}"
}

RUN_PHASE_SRC="$(extract_run_phase)"
if [ -z "${RUN_PHASE_SRC}" ] || ! printf '%s\n' "${RUN_PHASE_SRC}" \
     | grep -q '^run_phase() {'; then
  echo "FATAL: could not extract run_phase() from ${ORCHESTRATOR}" >&2
  exit 2
fi

# Extract just the `validate_required_env_vars` function definition from
# the orchestrator. Same awk-range pattern as extract_run_phase — we
# cannot source the orchestrator wholesale because of its top-level
# side effects.
extract_validate_required_env_vars() {
  awk '/^validate_required_env_vars\(\) \{/{flag=1} flag{print} flag && /^\}/{flag=0; exit}' \
    "${ORCHESTRATOR}"
}

VALIDATE_ENV_SRC="$(extract_validate_required_env_vars)"
if [ -z "${VALIDATE_ENV_SRC}" ] || ! printf '%s\n' "${VALIDATE_ENV_SRC}" \
     | grep -q '^validate_required_env_vars() {'; then
  echo "FATAL: could not extract validate_required_env_vars() from ${ORCHESTRATOR}" >&2
  exit 2
fi

# Extract validate_expected_account_id (account-pin guard added 2026-06-07
# per backlog "Account pinning"). Same awk-range pattern as above.
extract_validate_expected_account_id() {
  awk '/^validate_expected_account_id\(\) \{/{flag=1} flag{print} flag && /^\}/{flag=0; exit}' \
    "${ORCHESTRATOR}"
}

VALIDATE_ACCOUNT_SRC="$(extract_validate_expected_account_id)"
if [ -z "${VALIDATE_ACCOUNT_SRC}" ] || ! printf '%s\n' "${VALIDATE_ACCOUNT_SRC}" \
     | grep -q '^validate_expected_account_id() {'; then
  echo "FATAL: could not extract validate_expected_account_id() from ${ORCHESTRATOR}" >&2
  exit 2
fi

# Define the test surface that run_phase depends on:
#   - log / err           — writers (stub to /dev/null to keep test output clean)
#   - record_phase        — append to PHASE_NAMES / PHASE_VERDICTS arrays
#   - RUN_DIR             — per-test scratch dir for log files
#   - PHASE_NAMES /
#     PHASE_VERDICTS      — parallel arrays the orchestrator uses
#
# The test surface here matches the orchestrator's contract exactly.
# Eval the extracted run_phase definition into the current shell.
setup_phase_env() {
  RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/run-phase-test.XXXXXX")"
  PHASE_NAMES=()
  PHASE_VERDICTS=()
  # log/err/record_phase are invoked indirectly from the eval'd run_phase.
  # shellcheck disable=SC2329
  log() { :; }
  # shellcheck disable=SC2329
  err() { :; }
  # shellcheck disable=SC2329
  record_phase() {
    PHASE_NAMES+=("$1")
    PHASE_VERDICTS+=("$2")
  }
  # shellcheck disable=SC1090,SC2086
  eval "${RUN_PHASE_SRC}"
}

teardown_phase_env() {
  if [ -n "${RUN_DIR:-}" ] && [ -d "${RUN_DIR}" ]; then
    rm -rf "${RUN_DIR}"
  fi
  unset RUN_DIR PHASE_NAMES PHASE_VERDICTS
  unset -f log err record_phase run_phase 2>/dev/null || true
}

# Test surface for validate_required_env_vars (`preflight_env` phase
# helper). Stubs `err` to discard stderr so test output stays clean
# (the function emits ~10 lines of operator-actionable diagnostics on
# failure that we don't want to render in the test runner).
setup_env_validator() {
  # shellcheck disable=SC2329
  err() { :; }
  # shellcheck disable=SC1090,SC2086
  eval "${VALIDATE_ENV_SRC}"
  # The function references AWS_REGION in its diagnostic message —
  # set a placeholder so unbound-variable behavior under `set -u`
  # in the orchestrator never trips a test (the test harness itself
  # does not run `set -u`, but we mirror orchestrator semantics).
  AWS_REGION="${AWS_REGION:-test-region-1}"
}

teardown_env_validator() {
  unset -f err validate_required_env_vars 2>/dev/null || true
  unset CMS_DEMO_DEFAULT_PASSWORD
}

# Test surface for validate_expected_account_id (account-pin guard).
# Stubs `err` and `log` to /dev/null. Stubs `aws` so we can simulate
# both the success path (sts returns the matching account) and the
# mismatch path (sts returns a different account) without hitting
# real AWS.
setup_account_validator() {
  # shellcheck disable=SC2329
  err() { :; }
  # shellcheck disable=SC2329
  log() { :; }
  # shellcheck disable=SC1090,SC2086
  eval "${VALIDATE_ACCOUNT_SRC}"
}

teardown_account_validator() {
  unset -f err log validate_expected_account_id aws 2>/dev/null || true
  unset EXPECTED_ACCOUNT_ID MOCK_ACCOUNT_ID MOCK_STS_FAIL
}

# Stub for `aws sts get-caller-identity --query Account --output text`.
# Honors two test-only env vars:
#   MOCK_ACCOUNT_ID = '123456789012'  → stub prints this and exits 0
#   MOCK_STS_FAIL   = '1'             → stub exits non-zero (sts failure)
mock_aws_sts() {
  if [ "${MOCK_STS_FAIL:-0}" = "1" ]; then
    return 1
  fi
  if [ "$1" = "sts" ] && [ "$2" = "get-caller-identity" ]; then
    printf '%s\n' "${MOCK_ACCOUNT_ID:-}"
    return 0
  fi
  return 0
}

# Extract isolate_cdk_context() and restore_cdk_context() from the
# orchestrator. Same awk-range idiom as run_phase / validate_required_env_vars.
# Spec: 2026-06-03-cms-clean-deploy-context-isolation
extract_isolate_cdk_context() {
  awk '/^isolate_cdk_context\(\) \{/{flag=1} flag{print} flag && /^\}/{flag=0; exit}' \
    "${ORCHESTRATOR}"
}

extract_restore_cdk_context() {
  awk '/^restore_cdk_context\(\) \{/{flag=1} flag{print} flag && /^\}/{flag=0; exit}' \
    "${ORCHESTRATOR}"
}

ISOLATE_SRC="$(extract_isolate_cdk_context)"
RESTORE_SRC="$(extract_restore_cdk_context)"
if [ -z "${ISOLATE_SRC}" ] || ! printf '%s\n' "${ISOLATE_SRC}" \
     | grep -q '^isolate_cdk_context() {'; then
  echo "FATAL: could not extract isolate_cdk_context() from ${ORCHESTRATOR}" >&2
  exit 2
fi
if [ -z "${RESTORE_SRC}" ] || ! printf '%s\n' "${RESTORE_SRC}" \
     | grep -q '^restore_cdk_context() {'; then
  echo "FATAL: could not extract restore_cdk_context() from ${ORCHESTRATOR}" >&2
  exit 2
fi

# Test surface for isolate_cdk_context / restore_cdk_context. Both
# functions read RUN_DIR (preserved-file destination) and DEPLOYMENT_DIR
# (live-file source); restore_cdk_context additionally reads the
# script-scope PRESERVED_CONTEXT and LIVE_CONTEXT variables that
# isolate_cdk_context populates. Tests use mktemp dirs so the real
# repo's deployment/cdk.context.json is never touched.
setup_context_isolation_env() {
  RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/isolate-test-run.XXXXXX")"
  DEPLOYMENT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/isolate-test-dep.XXXXXX")"
  PRESERVED_CONTEXT=""
  LIVE_CONTEXT=""
  # shellcheck disable=SC2329
  log() { :; }
  # shellcheck disable=SC2329
  err() { :; }
  # shellcheck disable=SC1090,SC2086
  eval "${ISOLATE_SRC}"
  # shellcheck disable=SC1090,SC2086
  eval "${RESTORE_SRC}"
}

teardown_context_isolation_env() {
  if [ -n "${RUN_DIR:-}" ] && [ -d "${RUN_DIR}" ]; then
    rm -rf "${RUN_DIR}"
  fi
  if [ -n "${DEPLOYMENT_DIR:-}" ] && [ -d "${DEPLOYMENT_DIR}" ]; then
    rm -rf "${DEPLOYMENT_DIR}"
  fi
  unset RUN_DIR DEPLOYMENT_DIR PRESERVED_CONTEXT LIVE_CONTEXT
  unset -f log err isolate_cdk_context restore_cdk_context 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

blue "═══════════════════════════════════════════════════════════════"
blue "  Regression tests: run_phase() — phase-detection bug guard"
blue "═══════════════════════════════════════════════════════════════"

# ---------------------------------------------------------------------------
# Case 1: success path — wrapped command exits 0
#   Expect: run_phase returns 0, PASS recorded, no error log written.
# ---------------------------------------------------------------------------
blue "Case 1: wrapped command exits 0 → PASS"
setup_phase_env
rc=0
run_phase "case1_pass" bash -c "exit 0" || rc=$?
assert_eq "run_phase return code"            "0"          "${rc}"
assert_eq "PHASE_NAMES[0]"                   "case1_pass" "${PHASE_NAMES[0]:-<unset>}"
assert_eq "PHASE_VERDICTS[0]"                "PASS"       "${PHASE_VERDICTS[0]:-<unset>}"
assert_eq "phases recorded count"            "1"          "${#PHASE_NAMES[@]}"
teardown_phase_env

# ---------------------------------------------------------------------------
# Case 2: REGRESSION GUARD — wrapped command exits non-zero (7)
#   Expect: run_phase returns 7, FAIL recorded.
#   Pre-fix bug: would return 0 (because `local rc=$?` after `if … fi`
#   captures the if-statement's own exit code, not "$@"'s).
# ---------------------------------------------------------------------------
blue "Case 2: wrapped command exits 7 → FAIL, propagates rc=7"
setup_phase_env
rc=0
run_phase "case2_fail7" bash -c "exit 7" || rc=$?
assert_eq "run_phase return code (REGRESSION GUARD)" "7"          "${rc}"
assert_eq "PHASE_NAMES[0]"                            "case2_fail7" "${PHASE_NAMES[0]:-<unset>}"
assert_eq "PHASE_VERDICTS[0]"                         "FAIL"       "${PHASE_VERDICTS[0]:-<unset>}"
teardown_phase_env

# ---------------------------------------------------------------------------
# Case 3: REGRESSION GUARD — wrapped command exits 1 (the common case)
#   Expect: run_phase returns 1, FAIL recorded.
# ---------------------------------------------------------------------------
blue "Case 3: wrapped command exits 1 → FAIL, propagates rc=1"
setup_phase_env
rc=0
run_phase "case3_fail1" bash -c "exit 1" || rc=$?
assert_eq "run_phase return code (REGRESSION GUARD)" "1"          "${rc}"
assert_eq "PHASE_VERDICTS[0]"                         "FAIL"       "${PHASE_VERDICTS[0]:-<unset>}"
teardown_phase_env

# ---------------------------------------------------------------------------
# Case 4: REGRESSION GUARD — caller-side `|| exit N` short-circuits
#   This is the failure mode that bit ap-northeast-1 first-run: the
#   orchestrator chains `run_phase … || exit 1` after every phase to
#   stop on the first failure. Pre-fix, run_phase always returned 0, so
#   the `|| exit 1` was dead code and every subsequent phase ran
#   anyway. Test it here.
# ---------------------------------------------------------------------------
blue "Case 4: 'run_phase … || exit 99' actually short-circuits on FAIL"
setup_phase_env
# Run in a subshell so `exit 99` doesn't kill the test runner.
subshell_rc=0
( run_phase "case4_chain" bash -c "exit 5" || exit 99
  echo "REACHED" >>"${RUN_DIR}/case4.marker"
) >/dev/null 2>&1 || subshell_rc=$?
assert_eq "subshell exit code (caller's || exit 99 fired)" "99" "${subshell_rc}"
reached_marker="absent"
if [ -e "${RUN_DIR}/case4.marker" ]; then
  reached_marker="present"
fi
assert_eq "code AFTER the failed phase did NOT execute" "absent" "${reached_marker}"
teardown_phase_env

# ---------------------------------------------------------------------------
# Case 5: success path — caller's `|| exit N` does NOT fire
# ---------------------------------------------------------------------------
blue "Case 5: 'run_phase … || exit 99' does NOT short-circuit on PASS"
setup_phase_env
subshell_rc=0
( run_phase "case5_chain_ok" bash -c "exit 0" || exit 99
  echo "REACHED" >>"${RUN_DIR}/case5.marker"
) >/dev/null 2>&1 || subshell_rc=$?
assert_eq "subshell exit code (no short-circuit)" "0"       "${subshell_rc}"
reached_marker="absent"
if [ -e "${RUN_DIR}/case5.marker" ]; then
  reached_marker="present"
fi
assert_eq "code AFTER passing phase DID execute"  "present" "${reached_marker}"
teardown_phase_env

# ---------------------------------------------------------------------------
# Case 6: stdout + stderr captured to per-phase log file
# ---------------------------------------------------------------------------
blue "Case 6: combined stdout+stderr captured to per-phase log"
setup_phase_env
rc=0
run_phase "case6_log" \
  bash -c 'echo "out-line"; echo "err-line" >&2; exit 3' \
  || rc=$?
assert_eq "run_phase return code"             "3"   "${rc}"
log_path="${RUN_DIR}/case6_log.log"
log_present="absent"
if [ -e "${log_path}" ]; then
  log_present="present"
fi
assert_eq "per-phase log file exists"         "present" "${log_present}"
captured_out="$(grep -c '^out-line$' "${log_path}" 2>/dev/null || echo 0)"
captured_err="$(grep -c '^err-line$' "${log_path}" 2>/dev/null || echo 0)"
assert_eq "stdout captured to log"            "1" "${captured_out}"
assert_eq "stderr captured to log"            "1" "${captured_err}"
teardown_phase_env

# ---------------------------------------------------------------------------
# Case 7: validate_required_env_vars — CMS_DEMO_DEFAULT_PASSWORD set → PASS
#   Guards the `preflight_env` phase added in
#   issues/2026-06-03-clean-deploy-env-var-gate/. Direct test that
#   exporting the var lets the gate pass and the harness proceed to
#   bootstrap_region.
# ---------------------------------------------------------------------------
blue "Case 7: validate_required_env_vars with CMS_DEMO_DEFAULT_PASSWORD set → 0"
setup_env_validator
export CMS_DEMO_DEFAULT_PASSWORD='unit-test-placeholder-not-real'
rc=0
validate_required_env_vars || rc=$?
assert_eq "validate_required_env_vars return code (set)" "0" "${rc}"
teardown_env_validator

# ---------------------------------------------------------------------------
# Case 8: validate_required_env_vars — CMS_DEMO_DEFAULT_PASSWORD unset → FAIL
#   REGRESSION GUARD against the env-var gate documented in
#   issues/2026-06-03-clean-deploy-env-var-gate/. The pre-fix harness
#   had no env-var preflight; `cdk bootstrap` walked app.py, raised
#   ValueError from ui_stack.UIStack, and the harness halted with an
#   opaque "Subprocess exited with error 1". This case asserts the
#   gate fails fast (rc=1) before any AWS round-trip, with the var
#   unset.
# ---------------------------------------------------------------------------
blue "Case 8: validate_required_env_vars with CMS_DEMO_DEFAULT_PASSWORD unset → 1"
setup_env_validator
unset CMS_DEMO_DEFAULT_PASSWORD
rc=0
validate_required_env_vars || rc=$?
assert_eq "validate_required_env_vars return code (unset)" "1" "${rc}"
teardown_env_validator

# ---------------------------------------------------------------------------
# Case 9: validate_required_env_vars — CMS_DEMO_DEFAULT_PASSWORD empty → FAIL
#   `[ -z "${VAR:-}" ]` matches both unset and empty-string. Confirms
#   `export CMS_DEMO_DEFAULT_PASSWORD=''` (operator typo / accidental
#   placeholder line in clean-deploy.env evaluated without override)
#   is treated as "missing" rather than "set to empty string".
# ---------------------------------------------------------------------------
blue "Case 9: validate_required_env_vars with CMS_DEMO_DEFAULT_PASSWORD='' → 1"
setup_env_validator
export CMS_DEMO_DEFAULT_PASSWORD=''
rc=0
validate_required_env_vars || rc=$?
assert_eq "validate_required_env_vars return code (empty)" "1" "${rc}"
teardown_env_validator

# ---------------------------------------------------------------------------
# Cases 10-13: REGRESSION GUARD — audit/teardown category-coverage parity
#
# These cases guard against the coverage-gap bug class documented in
# issues/2026-06-03-clean-deploy-teardown-audit-coverage-gap/. The
# pre-fix harness had:
#   - teardown_region_force.delete_orphaned_ddb_tables sweeping ONLY
#     `cms-{stage}-storage-` tables, missing 6 RETAIN-policy tables
#     in data-processing.
#   - audit_region_orphans not enumerating DynamoDB at all.
#
# Both components silently reported success despite stranded resources.
# This invariant test asserts that audit_region_orphans's category set
# is a strict SUPERSET of teardown_region_force's category set. Future
# PRs that add a teardown category without a matching audit category
# will fail this gate.
#
# Each Python script exposes a `--list-categories` flag that prints
# its declared TEARDOWN_CATEGORIES / AUDIT_CATEGORIES one-per-line.
# These tests don't make AWS calls; pure metadata.
# ---------------------------------------------------------------------------

TEARDOWN_SCRIPT="${SCRIPT_DIR}/teardown_region_force.py"
AUDIT_SCRIPT="${SCRIPT_DIR}/audit_region_orphans.py"

# Helper: capture sorted category list from a Python script.
list_categories() {
  python3 "$1" --list-categories 2>/dev/null | LC_ALL=C sort -u
}

blue "Case 10: teardown --list-categories returns ≥1 non-empty category"
TEARDOWN_CATS="$(list_categories "${TEARDOWN_SCRIPT}")"
TEARDOWN_COUNT=0
if [ -n "${TEARDOWN_CATS}" ]; then
  TEARDOWN_COUNT=$(printf '%s\n' "${TEARDOWN_CATS}" | grep -c .)
fi
nonempty="no"
if [ "${TEARDOWN_COUNT}" -gt 0 ]; then nonempty="yes"; fi
assert_eq "teardown categories list is non-empty" "yes" "${nonempty}"

blue "Case 11: audit --list-categories returns ≥1 non-empty category"
AUDIT_CATS="$(list_categories "${AUDIT_SCRIPT}")"
AUDIT_COUNT=0
if [ -n "${AUDIT_CATS}" ]; then
  AUDIT_COUNT=$(printf '%s\n' "${AUDIT_CATS}" | grep -c .)
fi
nonempty="no"
if [ "${AUDIT_COUNT}" -gt 0 ]; then nonempty="yes"; fi
assert_eq "audit categories list is non-empty" "yes" "${nonempty}"

# Case 12: every teardown category appears in the audit list.
# Implementation: write each list to a tmp file, use `comm -23
# teardown audit` to surface elements present in teardown but not in
# audit. A non-empty diff = parity violation = process bug.
blue "Case 12: REGRESSION GUARD — audit categories ⊇ teardown categories"
PARITY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/parity-test.XXXXXX")"
printf '%s\n' "${TEARDOWN_CATS}" >"${PARITY_TMP}/teardown.txt"
printf '%s\n' "${AUDIT_CATS}" >"${PARITY_TMP}/audit.txt"
# `comm -23` outputs lines in file1 NOT in file2 (i.e. teardown-only).
# Both inputs must be sorted; list_categories() sorts.
TEARDOWN_ONLY="$(comm -23 "${PARITY_TMP}/teardown.txt" "${PARITY_TMP}/audit.txt")"
TEARDOWN_ONLY_COUNT=0
if [ -n "${TEARDOWN_ONLY}" ]; then
  TEARDOWN_ONLY_COUNT=$(printf '%s\n' "${TEARDOWN_ONLY}" | grep -c .)
fi
assert_eq "teardown categories missing from audit" "0" "${TEARDOWN_ONLY_COUNT}"
if [ "${TEARDOWN_ONLY_COUNT}" -gt 0 ]; then
  red "      Categories present in teardown but missing from audit:"
  printf '%s\n' "${TEARDOWN_ONLY}" | while IFS= read -r cat; do
    [ -z "${cat}" ] || red "        - ${cat}"
  done
  red "      Fix: add the missing category to AUDIT_CATEGORIES in"
  red "           deployment/scripts/audit_region_orphans.py and add an"
  red "           auditor function for it."
fi
rm -rf "${PARITY_TMP}"

# Case 13: audit must enumerate strictly more categories than teardown.
# Otherwise the audit is just a teardown-status check, not an
# independent verification. Audit's superset role catches CFN
# stack-delete leakage that teardown doesn't sweep directly (e.g.,
# Cognito user pools, KMS keys, CloudFront).
blue "Case 13: audit set is a strict superset of teardown set"
strict_superset="no"
if [ "${AUDIT_COUNT}" -gt "${TEARDOWN_COUNT}" ]; then
  strict_superset="yes"
fi
assert_eq "audit category count > teardown category count" "yes" "${strict_superset}"

# ---------------------------------------------------------------------------
# Cases 14-15: REGRESSION GUARDS — run-5 step-back fixes
#
# These cases guard against regression of the two distinct correctness
# bugs surfaced by clean-deploy run 5 (2026-06-03T15-17-33Z) and
# documented in `issues/2026-06-03-clean-deploy-coverage-step-back/`:
#
#   - Finding A: preflight S3 check filtered to `loc == region`,
#     missing globally-named buckets in OTHER regions that would
#     collide with deploys whose stack uses non-region-suffixed
#     bucket names (e.g. cms-staging-storage's
#     ServiceInvoiceBucket). Fix: surface as warning in
#     `check_s3_buckets`.
#
#   - Finding B: teardown's `delete_orphaned_ddb_tables` was
#     fire-and-forget, returning before async DDB delete actually
#     completed. Audit (run within seconds) saw still-DELETING
#     tables. Fix: poll `describe_table` until ResourceNotFound
#     before returning.
#
# Each test runs as a standalone Python script using only stdlib
# (unittest + unittest.mock), so no new pytest target is introduced.
# Both exit 0 on PASS, non-zero on FAIL. We assert on exit code.
# ---------------------------------------------------------------------------

TEARDOWN_DDB_WAIT_TEST="${SCRIPT_DIR}/test_teardown_ddb_wait.py"
PREFLIGHT_S3_TEST="${SCRIPT_DIR}/test_preflight_global_namespace.py"

blue "Case 14: REGRESSION GUARD — teardown waits for async DDB delete"
ddb_test_status="fail"
if python3 "${TEARDOWN_DDB_WAIT_TEST}" >/dev/null 2>&1; then
  ddb_test_status="pass"
fi
assert_eq "teardown DDB wait-for-completion regression test" "pass" "${ddb_test_status}"
if [ "${ddb_test_status}" != "pass" ]; then
  red "      Run for diagnostics: python3 ${TEARDOWN_DDB_WAIT_TEST}"
  red "      Likely cause: delete_orphaned_ddb_tables reverted to "
  red "      fire-and-forget. Restore the wait-loop on describe_table."
fi

blue "Case 15: REGRESSION GUARD — preflight surfaces global-namespace S3 warnings"
s3_test_status="fail"
if python3 "${PREFLIGHT_S3_TEST}" >/dev/null 2>&1; then
  s3_test_status="pass"
fi
assert_eq "preflight global-namespace S3 regression test" "pass" "${s3_test_status}"
if [ "${s3_test_status}" != "pass" ]; then
  red "      Run for diagnostics: python3 ${PREFLIGHT_S3_TEST}"
  red "      Likely cause: check_s3_buckets reverted to regional-only "
  red "      filter, dropping cross-region warning surface."
fi

# ---------------------------------------------------------------------------
# Case 16: REGRESSION GUARD — globally-named CMS S3 buckets ≤63 chars
#
# Guards against the bug surfaced by clean-deploy run 7
# (2026-06-03T17-18-46Z) and documented in
# `issues/2026-06-03-storage-bucket-name-too-long-ap-northeast-1/`:
# the morning's storage-bucket-suffix fix (commits e9e4653 / ce1040e)
# expanded the bucket-name f-string with a stack-name infix that
# overshot S3's 63-char DNS-compliant limit by 1 in 14-char regions
# (ap-northeast-1 etc.). CDK rejected the stack at app-instantiation
# time before any AWS round-trip; synth never ran.
#
# Fix:
# `cms-{stage}-storage-invoices-{region}-{account}` (56 chars worst
# case). The math table is in the issue report; the property
# assertion is the test wired in here.
# ---------------------------------------------------------------------------

BUCKET_NAME_LENGTH_TEST="${SCRIPT_DIR}/test_bucket_name_lengths.py"

blue "Case 16: REGRESSION GUARD — globally-named CMS S3 buckets ≤63 chars across all regions"
bucket_len_test_status="fail"
if python3 "${BUCKET_NAME_LENGTH_TEST}" >/dev/null 2>&1; then
  bucket_len_test_status="pass"
fi
assert_eq "bucket-name length regression test" "pass" "${bucket_len_test_status}"
if [ "${bucket_len_test_status}" != "pass" ]; then
  red "      Run for diagnostics: python3 ${BUCKET_NAME_LENGTH_TEST}"
  red "      Likely cause: someone added a data-shape qualifier to"
  red "      the bucket f-string in storage_stack.py without"
  red "      math-checking the resulting name across 14-char regions"
  red "      (ap-northeast-*, ap-southeast-*, eusc-de-east-1)."
fi

# ---------------------------------------------------------------------------
# Cases 17-22: REGRESSION GUARDS — clean-deploy cdk.context.json isolation
# Spec: 2026-06-03-cms-clean-deploy-context-isolation
# Issue: 2026-06-03-clean-deploy-cf-alias-cross-region-collision
# ---------------------------------------------------------------------------

blue "═══════════════════════════════════════════════════════════════"
blue "  Regression tests: isolate_cdk_context / restore_cdk_context"
blue "═══════════════════════════════════════════════════════════════"

# ---------------------------------------------------------------------------
# Case 17: isolate moves a present cdk.context.json to the preserved path.
# ---------------------------------------------------------------------------
blue "Case 17: isolate moves a present cdk.context.json"
setup_context_isolation_env
printf '{"some":"context"}\n' >"${DEPLOYMENT_DIR}/cdk.context.json"
rc=0
isolate_cdk_context || rc=$?
assert_eq "isolate return code" "0" "${rc}"
[ -f "${DEPLOYMENT_DIR}/cdk.context.json" ] && live_state="present" || live_state="absent"
assert_eq "live cdk.context.json after isolate" "absent" "${live_state}"
[ -f "${RUN_DIR}/cdk.context.json.preserved" ] && pres_state="present" || pres_state="absent"
assert_eq "preserved file after isolate" "present" "${pres_state}"
assert_eq "PRESERVED_CONTEXT script var set" "${RUN_DIR}/cdk.context.json.preserved" "${PRESERVED_CONTEXT}"
teardown_context_isolation_env

# ---------------------------------------------------------------------------
# Case 18: isolate is a no-op when no cdk.context.json exists.
# ---------------------------------------------------------------------------
blue "Case 18: isolate is a no-op when cdk.context.json is absent"
setup_context_isolation_env
rc=0
isolate_cdk_context || rc=$?
assert_eq "isolate return code (no live file)" "0" "${rc}"
[ -f "${RUN_DIR}/cdk.context.json.preserved" ] && pres_state="present" || pres_state="absent"
assert_eq "preserved file (should not exist)" "absent" "${pres_state}"
teardown_context_isolation_env

# ---------------------------------------------------------------------------
# Case 19: isolate refuses to overwrite an existing preserved file.
#   This is the kill -9 recovery guard: a prior run was killed before
#   restore ran, leaving a stranded preserved file. Re-running must
#   fail loudly rather than silently clobber.
# ---------------------------------------------------------------------------
blue "Case 19: isolate refuses to overwrite an existing preserved file"
setup_context_isolation_env
printf '{"prior-run":"unrecovered"}\n' >"${RUN_DIR}/cdk.context.json.preserved"
printf '{"current":"context"}\n' >"${DEPLOYMENT_DIR}/cdk.context.json"
rc=0
isolate_cdk_context || rc=$?
assert_eq "isolate return code (preserved file blocks)" "1" "${rc}"
# The current live file MUST NOT be moved/overwritten when isolate refuses.
[ -f "${DEPLOYMENT_DIR}/cdk.context.json" ] && live_state="present" || live_state="absent"
assert_eq "live cdk.context.json after refused isolate" "present" "${live_state}"
prior=$(cat "${RUN_DIR}/cdk.context.json.preserved")
assert_eq "prior preserved file unchanged" '{"prior-run":"unrecovered"}' "${prior}"
teardown_context_isolation_env

# ---------------------------------------------------------------------------
# Case 20: restore puts the preserved file back at the live path.
# ---------------------------------------------------------------------------
blue "Case 20: restore restores cdk.context.json from preserved"
setup_context_isolation_env
PRESERVED_CONTEXT="${RUN_DIR}/cdk.context.json.preserved"
LIVE_CONTEXT="${DEPLOYMENT_DIR}/cdk.context.json"
printf '{"operator":"original"}\n' >"${PRESERVED_CONTEXT}"
restore_cdk_context
[ -f "${LIVE_CONTEXT}" ] && live_state="present" || live_state="absent"
assert_eq "live cdk.context.json after restore" "present" "${live_state}"
[ -f "${PRESERVED_CONTEXT}" ] && pres_state="present" || pres_state="absent"
assert_eq "preserved file after restore (consumed)" "absent" "${pres_state}"
restored=$(cat "${LIVE_CONTEXT}")
assert_eq "restored content matches original" '{"operator":"original"}' "${restored}"
teardown_context_isolation_env

# ---------------------------------------------------------------------------
# Case 21: restore is a no-op if PRESERVED_CONTEXT is empty.
#   Trap fires before the isolate phase ran (e.g. preflight_env failed).
#   Restore must not error or touch anything.
# ---------------------------------------------------------------------------
blue "Case 21: restore is a no-op when PRESERVED_CONTEXT empty"
setup_context_isolation_env
# Vars left empty by setup; do not set them.
rc=0
restore_cdk_context || rc=$?
assert_eq "restore return code (vars empty)" "0" "${rc}"
teardown_context_isolation_env

# ---------------------------------------------------------------------------
# Case 22: restore preserves a mid-run-recreated context for forensics.
#   Pathological case: something else recreated cdk.context.json during
#   the harness run. Restore must NOT silently clobber the
#   harness-touched file; it preserves a forensic copy and restores the
#   operator's original on top.
# ---------------------------------------------------------------------------
blue "Case 22: restore preserves a mid-run-recreated context for forensics"
setup_context_isolation_env
PRESERVED_CONTEXT="${RUN_DIR}/cdk.context.json.preserved"
LIVE_CONTEXT="${DEPLOYMENT_DIR}/cdk.context.json"
printf '{"operator":"original"}\n' >"${PRESERVED_CONTEXT}"
printf '{"harness":"side-effect"}\n' >"${LIVE_CONTEXT}"
restore_cdk_context
[ -f "${RUN_DIR}/cdk.context.json.harness-mid-run" ] && forensic="present" || forensic="absent"
assert_eq "forensic copy of harness-touched file present" "present" "${forensic}"
restored=$(cat "${LIVE_CONTEXT}")
assert_eq "live file is the operator's original" '{"operator":"original"}' "${restored}"
forensic_content=$(cat "${RUN_DIR}/cdk.context.json.harness-mid-run")
assert_eq "forensic file is the harness side-effect" '{"harness":"side-effect"}' "${forensic_content}"
teardown_context_isolation_env

# ---------------------------------------------------------------------------
# Cases 23-25: validate_expected_account_id — account-pinning guard
#
# Backlog row "Account pinning" (closed 2026-06-07). EXPECTED_ACCOUNT_ID
# is opt-in: when set, preflight_env asserts the resolved AWS account
# (via `aws sts get-caller-identity`) matches exactly. When unset, the
# guard is a no-op (preserves prior behavior — no account check).
# ---------------------------------------------------------------------------
blue "Case 23: validate_expected_account_id — opt-in unset → 0 (no-op)"
setup_account_validator
unset EXPECTED_ACCOUNT_ID
# Stub aws to ensure the function never calls it when EXPECTED_ACCOUNT_ID is unset.
aws() { echo "FAIL: aws CLI invoked despite EXPECTED_ACCOUNT_ID unset" >&2; return 99; }
rc=0
validate_expected_account_id || rc=$?
assert_eq "validate_expected_account_id (unset → 0)" "0" "${rc}"
teardown_account_validator

blue "Case 24: validate_expected_account_id — set + matching → 0"
setup_account_validator
export EXPECTED_ACCOUNT_ID='123456789012'
export MOCK_ACCOUNT_ID='123456789012'
# shellcheck disable=SC2317
aws() { mock_aws_sts "$@"; }
rc=0
validate_expected_account_id || rc=$?
assert_eq "validate_expected_account_id (matching → 0)" "0" "${rc}"
teardown_account_validator

blue "Case 25: validate_expected_account_id — set + mismatch → 1"
setup_account_validator
export EXPECTED_ACCOUNT_ID='123456789012'
export MOCK_ACCOUNT_ID='999999999999'  # WRONG account
# shellcheck disable=SC2317
aws() { mock_aws_sts "$@"; }
rc=0
validate_expected_account_id || rc=$?
assert_eq "validate_expected_account_id (mismatch → 1)" "1" "${rc}"
teardown_account_validator

# ---------------------------------------------------------------------------
# Case 26: REGRESSION GUARD — orchestrator bash-c env-var sealing
#
# Issues:
#   - 2026-06-04-clean-deploy-harness-env-var-hygiene
#   - 2026-06-04-cms-app-py-region-precedence-inversion
#   - 2026-06-08-region-hygiene-fix (combined fix)
#
# Pre-fix bug: the 4 phase-level `bash -c` invocations (deploy_all,
# deploy_bedrock_agents, seed, tests_e2e) prefixed only `AWS_REGION` and
# `DEPLOYMENT_STAGE`, inheriting whatever else was in the operator's
# shell. A leaked `CDK_DEFAULT_REGION=us-west-2` from prior cross-region
# work polluted the harness subshell, and (due to the paired
# `app.py:25` precedence inversion) overrode the explicit
# `AWS_REGION=ap-northeast-1` at synth time. Run 12 produced
# `cms-staging-ui-frontend-...-us-west-2` resource names while
# deploying to Tokyo — CFN early-validation collision, 41 min wasted.
#
# This test has two parts:
#   Part A (static): every one of the 4 phase invocations must contain
#     the 3 sealing exports CDK_DEFAULT_REGION='${AWS_REGION}',
#     AWS_DEFAULT_REGION='${AWS_REGION}', CDK_DEFAULT_ACCOUNT='${ACCOUNT_ID}'.
#   Part B (behavioral): given a leaked operator env
#     (CDK_DEFAULT_REGION=us-west-2 + AWS_DEFAULT_REGION=us-west-2)
#     and harness intent (AWS_REGION=ap-northeast-1), the bash-c
#     pattern used by the orchestrator must produce a subshell where
#     `CDK_DEFAULT_REGION=ap-northeast-1` (the seal won), NOT
#     `CDK_DEFAULT_REGION=us-west-2` (the leak survived).
# ---------------------------------------------------------------------------

blue "═══════════════════════════════════════════════════════════════"
blue "  Regression test: orchestrator bash-c env-var sealing"
blue "═══════════════════════════════════════════════════════════════"

# Part A — static text assertions.
blue "Case 26A: every phase invocation contains all 3 sealing exports"
# Phases listed one-per-line because top-of-file IFS=$'\n\t' disables
# space-splitting in `for ... in $var`.
PHASES_TO_SEAL="$(printf '%s\n' deploy_all deploy_bedrock_agents seed tests_e2e)"
for phase in ${PHASES_TO_SEAL}; do
  # Capture from `run_phase "<phase>" \` through the next `|| exit 1` or 20
  # lines, whichever comes first. Multi-line capture covers tests_e2e's
  # one-token-per-line layout.
  invocation="$(awk -v p="\"${phase}\"" '
    $0 ~ ("run_phase " p)         {capture=1}
    capture                        {print}
    capture && /\|\| exit 1/       {exit}
  ' "${ORCHESTRATOR}")"

  for token in \
      "AWS_DEFAULT_REGION='\${AWS_REGION}'" \
      "CDK_DEFAULT_REGION='\${AWS_REGION}'" \
      "CDK_DEFAULT_ACCOUNT='\${ACCOUNT_ID}'"; do
    if printf '%s\n' "${invocation}" | grep -qF -- "${token}"; then
      sealed="present"
    else
      sealed="absent"
    fi
    assert_eq "phase '${phase}' contains ${token}" "present" "${sealed}"
  done
done

# Part B — behavioral assertion: confirm the bash-c env-prefix pattern
# overrides operator-shell leaks. Build the bash-c command in a temp file
# and run it under a controlled environment, capturing the subshell's
# `env` output to a second temp file. Avoids nested-quote / nested-$() parse
# hazards.
blue "Case 26B: bash-c env-prefix overrides operator-shell CDK/AWS leaks"
expected_region='ap-northeast-1'
expected_account='123456789012'

case26b_dir="$(mktemp -d "${TMPDIR:-/tmp}/case26b.XXXXXX")"
case26b_env_out="${case26b_dir}/env.out"
case26b_runner="${case26b_dir}/runner.sh"

# Runner mirrors the orchestrator's sealed bash-c pattern. The variables
# on the env-prefix line are what the orchestrator now sets; the inner
# `env` output is what `make` (or pytest, or boto3) would see.
cat > "${case26b_runner}" <<'RUNNER_EOF'
#!/usr/bin/env bash
# Inputs (set by caller via env): AWS_REGION, ACCOUNT_ID, OUT_FILE.
# Operator-shell leaks (CDK_DEFAULT_REGION, AWS_DEFAULT_REGION,
# CDK_DEFAULT_ACCOUNT) are presumed to already be set in the caller's env
# and inherited here.
bash -c "AWS_REGION='${AWS_REGION}' \
         AWS_DEFAULT_REGION='${AWS_REGION}' \
         CDK_DEFAULT_REGION='${AWS_REGION}' \
         CDK_DEFAULT_ACCOUNT='${ACCOUNT_ID}' \
         DEPLOYMENT_STAGE='staging' \
         env" \
  | grep -E '^(AWS_REGION|AWS_DEFAULT_REGION|CDK_DEFAULT_REGION|CDK_DEFAULT_ACCOUNT)=' \
  | LC_ALL=C sort > "${OUT_FILE}"
RUNNER_EOF
chmod +x "${case26b_runner}"

# Invoke the runner with operator-shell leaks set (simulating the
# documented bug). Subshell isolates the leak from the test runner.
(
  export CDK_DEFAULT_REGION='us-west-2'        # operator-shell leak
  export AWS_DEFAULT_REGION='us-west-2'        # operator-shell leak
  export CDK_DEFAULT_ACCOUNT='000000000000'    # operator-shell leak
  export AWS_REGION="${expected_region}"       # harness intent
  export ACCOUNT_ID="${expected_account}"      # harness-resolved sts caller-identity
  export OUT_FILE="${case26b_env_out}"
  bash "${case26b_runner}"
)

if [ ! -s "${case26b_env_out}" ]; then
  red "  ✗ Case 26B setup error: empty env capture at ${case26b_env_out}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  TOTAL_COUNT=$((TOTAL_COUNT + 1))
else
  # Each var must resolve to harness intent, NOT the leak.
  for var in AWS_REGION AWS_DEFAULT_REGION CDK_DEFAULT_REGION; do
    if grep -qF -- "${var}=${expected_region}" "${case26b_env_out}"; then
      seal_won="yes"
    else
      seal_won="no"
    fi
    assert_eq "${var} in subshell == harness intent (${expected_region})" "yes" "${seal_won}"

    # And explicitly: the leak value must NOT be present on the var line.
    if grep -qE "^${var}=us-west-2\$" "${case26b_env_out}"; then
      leak_survived="yes"
    else
      leak_survived="no"
    fi
    assert_eq "${var}=us-west-2 (leak) did NOT survive" "no" "${leak_survived}"
  done

  # Account ID seal — operator's leaked '000000000000' must not survive.
  if grep -qF -- "CDK_DEFAULT_ACCOUNT=${expected_account}" "${case26b_env_out}"; then
    account_seal_won="yes"
  else
    account_seal_won="no"
  fi
  assert_eq "CDK_DEFAULT_ACCOUNT in subshell == harness ACCOUNT_ID" "yes" "${account_seal_won}"

  if grep -qE "^CDK_DEFAULT_ACCOUNT=000000000000\$" "${case26b_env_out}"; then
    account_leak="yes"
  else
    account_leak="no"
  fi
  assert_eq "CDK_DEFAULT_ACCOUNT=000000000000 (leak) did NOT survive" "no" "${account_leak}"
fi

rm -rf "${case26b_dir}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
blue "═══════════════════════════════════════════════════════════════"
blue "  Results: ${PASS_COUNT}/${TOTAL_COUNT} assertions passed"
blue "═══════════════════════════════════════════════════════════════"

if [ "${FAIL_COUNT}" -gt 0 ]; then
  red "  ${FAIL_COUNT} failure(s)"
  exit 1
fi

green "  All assertions passed"
exit 0

#!/usr/bin/env bash
# CMS Clean-Deploy Integration Test Orchestrator (Group 3.1).
#
# Spec: .kiro/specs/2026-06-01-clean-deploy-integration-tests/spec.md
# PRD:  ~/.kiro/portfolio/initiatives/2026-06-01-clean-deploy-integration-tests/prd.md
#
# Runs the full first-time-deployment harness against a target region:
#
#   1. Per-region pre-flight (resolves BEDROCK_INFERENCE_PROFILE_ID
#      via `preflight_per_region.py --emit-env`).
#   2. CDK bootstrap of <region>                  (idempotent).
#   3. CDK bootstrap of us-east-1                 (no-op in v1 default
#      per docs/tech.md §5; recorded as SKIP).
#   4. Account-global + region pre-flight         (--strict).
#   5. `make deploy-all`.
#   6. `make deploy-bedrock-agents`               (uses the resolved
#      inference-profile env var).
#   7. `make seed-all-demo-data`                  (KB content
#      side-effect of seed-service-warranty per docs/tech.md).
#   8. `pytest tests/e2e/test_clean_deploy.py -m e2e` (S1–S14 +
#      `test_trip_materializes` telemetry assertion).
#  TRAP. `audit_region_orphans.py` — runs unconditionally on EXIT and
#       emits report.json. `teardown_region_force.py` runs by DEFAULT,
#       but is SKIPPED when `CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1`
#       opt-in is set AND every recorded phase is PASS/SKIP AND the
#       trap fires with exit_code 0 (i.e. successful run end-to-end).
#       That opt-in lets the harness double as a real first-time
#       deploy that leaves the environment up — see
#       `.kiro/specs/2026-06-17-cms-tokyo-clean-redeploy/spec.md`
#       (OQ-1=B). Default behavior is unchanged: teardown always
#       runs (including on any phase failure or set -e short-circuit).
#
# Phase ordering rationale: bootstrap precedes the `--strict` gate
# so that strict-mode reports real residual `cms-staging-*` blockers
# instead of false-flagging the missing CDK bootstrap. See
# `.kiro/specs/2026-06-01-clean-deploy-integration-tests/decisions.md`
# entry "2026-06-02 — Group 3.1 phase ordering" for the full rationale.
#
# Non-interactive (per ~/.kiro/steering/non-interactive.md):
#   - `cdk` runs with --require-approval never.
#   - AWS CLI runs with --no-cli-pager.
#   - No `read` prompts, no editor pop-ups.
#
# Compatible with bash 3.2+ (macOS default). Uses parallel arrays
# instead of associative arrays for portability.

set -euo pipefail

# --- Script-scope state for cdk.context.json isolation -----------------------
# Set by the isolate_cdk_context phase below; read by restore_cdk_context
# in the EXIT trap. Declared at script scope (not local) so the trap
# function can see them under bash 3.2 semantics even if the trap fires
# before the isolate phase runs (in which case both stay empty and
# restore_cdk_context is a no-op).
#
# Spec: .kiro/specs/2026-06-03-cms-clean-deploy-context-isolation/spec.md
# Issue: issues/2026-06-03-clean-deploy-cf-alias-cross-region-collision/
PRESERVED_CONTEXT=""
LIVE_CONTEXT=""

# --- Script-scope state for cdk lock (concurrent-deploy guard) ---------------
# Set by the cdk_lock_acquire phase below; read by release_cdk_lock in the
# EXIT trap. The lock file under DEPLOYMENT_DIR signals to ANY subsequent
# cdk invocation in this directory that a harness run owns it. Stale lock
# files (PID dead) are auto-recovered.
#
# Issue: cdk concurrency contention surfaced by clean-deploy run 16 (2026-06-05)
LOCK_FILE=""

# --- Resolve script location + sourcable env ----------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEPLOYMENT_DIR}/.." && pwd)"
ENV_FILE="${DEPLOYMENT_DIR}/config/clean-deploy.env"

if [ ! -f "${ENV_FILE}" ]; then
  echo "FATAL: ${ENV_FILE} missing — Group 2.1 should have created it." >&2
  exit 2
fi

# shellcheck source=../config/clean-deploy.env disable=SC1091
. "${ENV_FILE}"

# --- macOS App-Nap / sleep guard (added 2026-06-17) --------------------------
# Tokyo redeploy run 2026-06-17 hit `@smithy/node-http-handler 300s
# requestTimeout` mid-deploy_all on UI asset publish. Network was
# healthy (160ms RTT to ap-northeast-1 S3, 0% packet loss); failure
# mode was consistent with macOS App Nap / sleep / power-management
# silently suspending the Node process and leaving the SDK socket
# in a half-open state until smithy's 300s wall fires.
#
# `caffeinate -dimsu` keeps the system + display + idle awake and
# the process un-napped for the duration of the harness:
#   -d: prevent display sleep (some tunnels die when display sleeps)
#   -i: prevent idle system sleep
#   -m: prevent disk sleep
#   -s: prevent system sleep when on AC power
#   -u: simulate user activity (defeats App Nap)
#
# Idempotent self-reexec: a marker env var prevents loops on the
# already-caffeinated invocation. No-op on systems without
# `caffeinate` (Linux, CI runners) — those don't have the App-Nap
# class so nothing to mitigate.
#
# Spec: .kiro/specs/2026-06-17-cms-tokyo-clean-redeploy/spec.md
if [ "${_CAFFEINATE_REEXEC:-0}" != "1" ] && command -v caffeinate >/dev/null 2>&1; then
  export _CAFFEINATE_REEXEC=1
  exec caffeinate -dimsu "$0" "$@"
fi

# --- CLI flags ----------------------------------------------------------------
RUN_ID_DEFAULT="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
RUN_ID="${RUN_ID_DEFAULT}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--region <code>] [--run-id <id>] [-h|--help]

  --region <code>   AWS region to target (default: \$AWS_REGION = ${AWS_REGION})
  --run-id <id>     Run identifier (default: ISO timestamp = ${RUN_ID_DEFAULT})
  -h, --help        Show this help

Defaults sourced from: ${ENV_FILE}
Per-run artefacts:     \${RUN_LOG_ROOT}/<run-id>/
                       (currently: ${RUN_LOG_ROOT}/<run-id>/)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --region)
      [ $# -ge 2 ] || { echo "ERROR: --region requires a value" >&2; usage >&2; exit 2; }
      AWS_REGION="$2"
      shift 2
      ;;
    --run-id)
      [ $# -ge 2 ] || { echo "ERROR: --run-id requires a value" >&2; usage >&2; exit 2; }
      RUN_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

export AWS_REGION
export DEPLOYMENT_STAGE

RUN_DIR="${RUN_LOG_ROOT}/${RUN_ID}"
mkdir -p "${RUN_DIR}"

START_EPOCH="$(date +%s)"

# --- Phase tracking (parallel arrays, bash 3.2-portable) ----------------------
PHASE_NAMES=()
PHASE_VERDICTS=()

record_phase() {
  PHASE_NAMES+=("$1")
  PHASE_VERDICTS+=("$2")
}

# --- Logging helpers ----------------------------------------------------------
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
err() { printf '[%s] ERROR: %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }

# Run a phase, capturing combined stdout+stderr to a per-phase log file.
# Records PASS/FAIL into the parallel arrays. Returns the wrapped
# command's exit code so the caller can decide whether to short-circuit.
#
# Implementation note (see issues/2026-06-03-clean-deploy-orchestrator-
# phase-detection-bug/): the exit code MUST be captured directly via
# `|| rc=$?` rather than via `$?` after an `if "$@"; then …; fi` block.
# Per the bash manual, an `if` statement returns 0 when no consequence-
# branch executes (i.e. when the test failed and there is no `else`),
# so `local rc=$?` after the `fi` captures 0 — silently masking the
# wrapped command's real exit code and causing `run_phase … || exit 1`
# safety nets to become dead code.
run_phase() {
  local phase="$1"
  shift
  local logfile="${RUN_DIR}/${phase}.log"
  local rc=0
  log "▶ Phase: ${phase}"
  "$@" >"${logfile}" 2>&1 || rc=$?
  if [ "${rc}" -eq 0 ]; then
    record_phase "${phase}" "PASS"
    log "  ✓ ${phase} PASS (log: ${logfile})"
    return 0
  fi
  record_phase "${phase}" "FAIL"
  err "  ✗ ${phase} FAIL (exit=${rc}, log: ${logfile})"
  # Surface known-signature lines from the failed phase log for fast
  # operator triage. Backlog row "Failure-grep surfacing" (closed
  # 2026-06-07): the smoking-gun `requestTimeout` warning was buried
  # at line 359 of a 390-line log on issue 2026-06-03-clean-deploy-
  # deploy-all-fail-ap-northeast-1; future ops investigating a
  # similar failure may not grep for these patterns. Patterns cover
  # SDK/asset-publish timeouts (requestTimeout, RequestTimeTooSkewed
  # → matches "Skewed", RequestExpired), throttling
  # (Throttling/Throttled), CDK explicit fail: prefix, and
  # asset-too-large surfacing. `head -10` caps spew on phases with
  # many matches; full log is always at ${logfile}.
  local matches
  matches=$(grep -E 'fail:|requestTimeout|Skewed|RequestExpired|Throttling|Throttled|too large' "${logfile}" 2>/dev/null | head -10 || true)
  if [ -n "${matches}" ]; then
    err "  ▼ Known-signature matches from ${phase}.log (first 10; full log at path above):"
    while IFS= read -r line; do
      err "    | ${line}"
    done <<< "${matches}"
  fi
  return "${rc}"
}

# Validate required environment variables for the harness. Returns 0
# if every required var is set; 1 otherwise, after printing an
# operator-actionable error to stderr that lists EVERY missing var
# in a single message (rather than failing on each separately).
#
# Required vars are those that `cdk bootstrap` / `cdk synth` read
# unconditionally from `deployment/app.py` and its imported stack
# constructors at synthesis time — failure to set them raises
# ValueError mid-synth, which surfaces through the cdk CLI as an
# opaque "Subprocess exited with error 1".
#
# As of 2026-06-03 the only synth-time-required env var is
# CMS_DEMO_DEFAULT_PASSWORD (read by ui_stack.py:~1239 to seed the
# demo Cognito user). Every other env var read by app.py either has
# a default (DEPLOYMENT_STAGE, AWS_REGION) or guards an optional
# code path (DEPLOY_*, FEDERATE_*, MSK_*, CONNECTOR_NAME).
#
# If a future stack adds a new unconditional fail-closed env var,
# extend the `missing` array below; the gate stays cohesive (single
# diagnostic listing every missing var) without scope-creeping into
# `app.py` feature flags.
#
# Issue: issues/2026-06-03-clean-deploy-env-var-gate/
validate_required_env_vars() {
  local missing=()
  if [ -z "${CMS_DEMO_DEFAULT_PASSWORD:-}" ]; then
    missing+=("CMS_DEMO_DEFAULT_PASSWORD")
  fi

  if [ "${#missing[@]}" -eq 0 ]; then
    return 0
  fi

  err "Required environment variable(s) not set: ${missing[*]}"
  err "  These are read at synth/bootstrap time by deployment/app.py and"
  err "  its imported stacks. Without them, 'cdk bootstrap' walks app.py,"
  err "  raises ValueError from ui_stack.UIStack, and exits with an opaque"
  err "  'Subprocess exited with error 1' — short-circuiting every"
  err "  downstream phase."
  err ""
  err "  CMS_DEMO_DEFAULT_PASSWORD seeds the demo Cognito user"
  err "  (FleetManager@example.com). Stack reference:"
  err "    deployment/stacks/ui_stack.py:~1239"
  err "  Runbook reference:"
  err "    docs/DEPLOYMENT.md § Clean-deploy integration test → Prereqs"
  err ""
  err "  Set ad-hoc and re-run:"
  err "    export CMS_DEMO_DEFAULT_PASSWORD='<your-staging-password>'"
  err "    make -C deployment clean-deploy-test REGION=${AWS_REGION}"
  return 1
}

# Validate the resolved AWS account ID against EXPECTED_ACCOUNT_ID
# when that knob is set. Returns 0 if EXPECTED_ACCOUNT_ID is unset
# (opt-in, no-op default), 0 if STS resolves to a matching account
# ID, and 1 (with operator-actionable diagnostic) on mismatch or
# STS failure.
#
# Why: clean-deploy.env does not pin AWS_PROFILE; an operator with
# the wrong default profile silently targets the wrong account,
# preflight passes, deploy succeeds, resources land in the wrong
# account. This guard closes the gap by failing in ~3s on mismatch
# rather than silently mis-deploying.
#
# Backlog: "Account pinning" (closed 2026-06-07).
validate_expected_account_id() {
  if [ -z "${EXPECTED_ACCOUNT_ID:-}" ]; then
    return 0  # Opt-in; not configured → silently pass.
  fi

  local resolved
  if ! resolved=$(aws sts get-caller-identity --query Account --output text 2>/dev/null); then
    err "EXPECTED_ACCOUNT_ID is set (${EXPECTED_ACCOUNT_ID}) but"
    err "  'aws sts get-caller-identity' failed — credentials are"
    err "  invalid, expired, or no AWS_PROFILE is set."
    err ""
    err "  Resolution:"
    err "    1. Verify your AWS_PROFILE is set and current:"
    err "       aws sts get-caller-identity"
    err "    2. If the profile is stale, refresh credentials:"
    err "       aws sso login --profile <your-profile>"
    err "    3. Re-run the harness."
    return 1
  fi

  if [ "${resolved}" = "${EXPECTED_ACCOUNT_ID}" ]; then
    log "  Account-pin OK: ${resolved} matches EXPECTED_ACCOUNT_ID"
    return 0
  fi

  err "AWS account mismatch — refusing to proceed."
  err "  EXPECTED_ACCOUNT_ID = ${EXPECTED_ACCOUNT_ID}"
  err "  Resolved (sts)      = ${resolved}"
  err ""
  err "  Your shell's AWS_PROFILE points at the wrong account. The"
  err "  harness would otherwise silently deploy CMS into ${resolved}."
  err ""
  err "  Resolution:"
  err "    1. Identify the correct profile for ${EXPECTED_ACCOUNT_ID}:"
  err "       aws configure list-profiles"
  err "       aws sts get-caller-identity --profile <candidate>"
  err "    2. Export it before re-running:"
  err "       export AWS_PROFILE=<correct-profile>"
  err "    3. Re-run the harness."
  return 1
}

# --- Trap: teardown + audit + report.json — ALWAYS runs ------------------------
# shellcheck disable=SC2329  # invoked indirectly via teardown_and_audit
emit_report() {
  # Compute aggregate verdict: PASS only if every recorded phase is PASS.
  # SKIP is treated as PASS (no-op steps don't fail the run).
  local verdict="PASS"
  local i
  for i in "${!PHASE_NAMES[@]}"; do
    case "${PHASE_VERDICTS[$i]}" in
      PASS|SKIP) ;;
      *) verdict="FAIL" ;;
    esac
  done

  local end_epoch duration phases_json sep
  end_epoch="$(date +%s)"
  duration=$((end_epoch - START_EPOCH))

  phases_json=""
  sep=""
  for i in "${!PHASE_NAMES[@]}"; do
    phases_json="${phases_json}${sep}\"${PHASE_NAMES[$i]}\":\"${PHASE_VERDICTS[$i]}\""
    sep=","
  done

  # Collect artefact filenames for the report (logs + JSON files in RUN_DIR).
  local artefacts="" art_sep=""
  local f
  for f in "${RUN_DIR}"/*.log "${RUN_DIR}"/*.json "${RUN_DIR}"/*.txt; do
    [ -e "${f}" ] || continue
    artefacts="${artefacts}${art_sep}\"$(basename "${f}")\""
    art_sep=","
  done

  cat >"${RUN_DIR}/report.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "region": "${AWS_REGION}",
  "stage": "${DEPLOYMENT_STAGE}",
  "resolved_inference_profile": "${BEDROCK_INFERENCE_PROFILE_ID:-}",
  "phases": {${phases_json}},
  "verdict": "${verdict}",
  "duration_secs": ${duration},
  "artifacts": [${artefacts}]
}
EOF
  log "Report: ${RUN_DIR}/report.json (verdict=${verdict}, duration=${duration}s)"
}

# shellcheck disable=SC2329  # invoked from the inline phase block below
isolate_cdk_context() {
  # Move deployment/cdk.context.json out of CDK's lookup path for the
  # duration of the harness run. Sets the script-scope PRESERVED_CONTEXT
  # and LIVE_CONTEXT variables so restore_cdk_context (in the EXIT trap)
  # can put it back. Refuses to overwrite an existing preserved file —
  # that indicates a prior run was killed before its restore ran, and
  # the operator must manually recover before re-running the harness.
  #
  # Returns 0 on success (whether or not a file was actually moved);
  # returns 1 if a prior preserved file blocks the move.
  #
  # Spec: .kiro/specs/2026-06-03-cms-clean-deploy-context-isolation/spec.md
  PRESERVED_CONTEXT="${RUN_DIR}/cdk.context.json.preserved"
  LIVE_CONTEXT="${DEPLOYMENT_DIR}/cdk.context.json"
  if [ -f "${PRESERVED_CONTEXT}" ]; then
    err "  ✗ isolate_cdk_context FAIL — refusing to overwrite existing preserved context at ${PRESERVED_CONTEXT}"
    err "    This indicates a prior orchestrator run was killed before its restore step ran."
    err "    Manually restore: mv '${PRESERVED_CONTEXT}' '${LIVE_CONTEXT}' && rm -rf '${RUN_DIR}'"
    return 1
  fi
  if [ -f "${LIVE_CONTEXT}" ]; then
    mv "${LIVE_CONTEXT}" "${PRESERVED_CONTEXT}"
    log "  ✓ isolate_cdk_context PASS — relocated cdk.context.json to ${PRESERVED_CONTEXT}"
  else
    log "  ✓ isolate_cdk_context PASS — no cdk.context.json to relocate (already absent)"
  fi
  return 0
}

# shellcheck disable=SC2329  # invoked indirectly via the EXIT trap
restore_cdk_context() {
  # Restore the operator's deployment/cdk.context.json that was
  # relocated by the isolate_cdk_context phase. Idempotent: if the
  # phase never ran (early-exit before the phase, or no live context
  # at phase entry), PRESERVED_CONTEXT is empty and this is a no-op.
  #
  # Runs as the FIRST step of teardown_and_audit so the operator's
  # file is back where they expect it BEFORE any teardown sub-process
  # observes its absence.
  #
  # Spec: .kiro/specs/2026-06-03-cms-clean-deploy-context-isolation/spec.md
  if [ -z "${PRESERVED_CONTEXT:-}" ]; then return 0; fi
  if [ ! -f "${PRESERVED_CONTEXT}" ]; then return 0; fi
  if [ -f "${LIVE_CONTEXT}" ]; then
    # Pathological: something else recreated cdk.context.json mid-run.
    # Preserve the harness-touched copy for forensics; restore the
    # operator's original on top.
    mv "${LIVE_CONTEXT}" "${RUN_DIR}/cdk.context.json.harness-mid-run"
    err "  ⚠ cdk.context.json was recreated mid-run; preserved at ${RUN_DIR}/cdk.context.json.harness-mid-run"
  fi
  mv "${PRESERVED_CONTEXT}" "${LIVE_CONTEXT}"
  log "  ✓ restore_cdk_context — restored ${LIVE_CONTEXT}"
}

# shellcheck disable=SC2329  # invoked indirectly via the EXIT trap
release_cdk_lock() {
  # Remove the harness's cdk-lock sentinel. Idempotent: the phase may
  # not have run (early-exit before cdk_lock_acquire) — in which case
  # LOCK_FILE is empty and this is a no-op.
  #
  # Issue: cdk concurrency contention surfaced by clean-deploy run 16.
  if [ -z "${LOCK_FILE:-}" ]; then return 0; fi
  if [ -f "${LOCK_FILE}" ]; then
    rm -f "${LOCK_FILE}" 2>/dev/null
    log "  ✓ release_cdk_lock — removed ${LOCK_FILE}"
  fi
}

# shellcheck disable=SC2329  # invoked indirectly via the EXIT trap
teardown_and_audit() {
  # Capture the exit code BEFORE we disable the trap, so we propagate
  # the original phase's failure to the caller.
  local exit_code=$?
  trap - EXIT INT TERM

  # Restore operator's cdk.context.json BEFORE any teardown sub-process
  # runs (teardown does not read it today, but ordering is correct
  # for early-exit semantics — the operator's persisted state is back
  # before they see the orchestrator return).
  restore_cdk_context

  # Release the cdk lock so subsequent cdk invocations in this directory
  # are not blocked. Runs after restore_cdk_context so any teardown
  # sub-process can use the operator's cdk.context.json freely. Idempotent.
  release_cdk_lock

  log "═══════════════════════════════════════════════════════════════"
  log "Trap fired (exit=${exit_code}); running teardown + audit"
  log "═══════════════════════════════════════════════════════════════"

  # Decide whether to skip the destructive teardown step. Audit always runs.
  #
  # Opt-in via CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1: when every recorded phase
  # verdict is PASS (or SKIP) AND the trap fired with exit_code 0, skip the
  # teardown_region_force.py invocation so the freshly-deployed environment
  # remains live for smoke / UAT / ongoing manual validation. Default
  # behavior is unchanged (teardown always runs) — preserves the
  # self-cleaning test-cycle contract for the 23 prior runs + any CI
  # consumers.
  #
  # Restore_cdk_context + release_cdk_lock have already run unconditionally
  # above (and audit runs unconditionally below) — only the destructive
  # teardown is skipped.
  #
  # Spec: .kiro/specs/2026-06-17-cms-tokyo-clean-redeploy/spec.md (OQ-1=B).
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
    if [ "${all_pass}" -eq 1 ]; then
      skip_teardown=1
    fi
  fi

  if [ "${skip_teardown}" -eq 1 ]; then
    log "  ⚠ All phases PASS + CLEAN_DEPLOY_SKIP_TEARDOWN_ON_PASS=1 set"
    log "    → SKIPPING teardown_region_force.py (deployment remains live)"
    log "    → operator owns subsequent cleanup of region=${AWS_REGION} stage=${DEPLOYMENT_STAGE}"
    record_phase "teardown" "SKIP"
  else
    # Teardown — runs by default. Continue even on failure so audit still runs.
    local td_log="${RUN_DIR}/teardown.log"
    if python3 "${SCRIPT_DIR}/teardown_region_force.py" \
          --region "${AWS_REGION}" --stage "${DEPLOYMENT_STAGE}" \
          >"${td_log}" 2>&1; then
      record_phase "teardown" "PASS"
      log "  ✓ teardown PASS (log: ${td_log})"
    else
      record_phase "teardown" "FAIL"
      err "  ✗ teardown FAIL (log: ${td_log})"
    fi
  fi

  # Audit — always runs after teardown.
  local audit_log="${RUN_DIR}/audit.log"
  local audit_json="${RUN_DIR}/audit.json"
  if python3 "${SCRIPT_DIR}/audit_region_orphans.py" \
        --region "${AWS_REGION}" --stage "${DEPLOYMENT_STAGE}" \
        --report-path "${audit_json}" \
        >"${audit_log}" 2>&1; then
    record_phase "audit" "PASS"
    log "  ✓ audit PASS (log: ${audit_log}, report: ${audit_json})"
  else
    record_phase "audit" "FAIL"
    err "  ✗ audit FAIL (log: ${audit_log}; see ${audit_json} for orphan details)"
  fi

  emit_report
  exit "${exit_code}"
}

trap teardown_and_audit EXIT INT TERM

# --- Phase execution ----------------------------------------------------------
log "═══════════════════════════════════════════════════════════════"
log "Clean-deploy harness:"
log "  run_id=${RUN_ID}"
log "  region=${AWS_REGION}"
log "  stage=${DEPLOYMENT_STAGE}"
log "  run_dir=${RUN_DIR}"
log "═══════════════════════════════════════════════════════════════"

# -----------------------------------------------------------------------------
# Phase: preflight_region — resolves BEDROCK_INFERENCE_PROFILE_ID.
# Runs FIRST because no bootstrap dependency, and downstream phases need
# the resolved env var.
# -----------------------------------------------------------------------------
log "▶ Phase: preflight_region"
PREFLIGHT_LOG="${RUN_DIR}/preflight_region.log"
if EVAL_OUT="$(python3 "${SCRIPT_DIR}/preflight_per_region.py" \
       --region "${AWS_REGION}" --stage "${DEPLOYMENT_STAGE}" --emit-env \
       2>"${PREFLIGHT_LOG}")"; then
  # Capture the human-readable preflight output to the same log so the
  # operator can see exactly what was checked (the --emit-env mode
  # writes diagnostics to stderr, KEY=VALUE lines to stdout).
  printf '%s\n' "${EVAL_OUT}" >>"${PREFLIGHT_LOG}"
  # shellcheck disable=SC2086
  eval "${EVAL_OUT}"
  export BEDROCK_INFERENCE_PROFILE_ID
  record_phase "preflight_region" "PASS"
  log "  ✓ preflight_region PASS — BEDROCK_INFERENCE_PROFILE_ID=${BEDROCK_INFERENCE_PROFILE_ID:-<unset>}"
else
  record_phase "preflight_region" "FAIL"
  err "  ✗ preflight_region FAIL (log: ${PREFLIGHT_LOG})"
  exit 1
fi

if [ -z "${BEDROCK_INFERENCE_PROFILE_ID:-}" ]; then
  err "BEDROCK_INFERENCE_PROFILE_ID empty after preflight — cannot continue."
  exit 1
fi

# -----------------------------------------------------------------------------
# Phase: preflight_env — required env-var gate.
# Runs BEFORE bootstrap_region because `cdk bootstrap` walks app.py during
# context discovery, and app.py instantiates UIStack which raises ValueError
# if CMS_DEMO_DEFAULT_PASSWORD is unset (ui_stack.py:~1239). Without this
# gate, the bootstrap (and every downstream phase, now correctly
# short-circuited by the run_phase fix in commits 54bd77e/c2e9b56/7c46ebc)
# fails with an opaque "Subprocess exited with error 1" instead of a clear
# actionable error.
#
# Issue: issues/2026-06-03-clean-deploy-env-var-gate/
# -----------------------------------------------------------------------------
log "▶ Phase: preflight_env"
preflight_env_ok=true
if ! validate_required_env_vars; then
  preflight_env_ok=false
fi
# Account-pin check: opt-in via EXPECTED_ACCOUNT_ID; no-op if unset.
# Backlog row "Account pinning" (closed 2026-06-07).
if ! validate_expected_account_id; then
  preflight_env_ok=false
fi
if "${preflight_env_ok}"; then
  record_phase "preflight_env" "PASS"
  log "  ✓ preflight_env PASS"
else
  record_phase "preflight_env" "FAIL"
  err "  ✗ preflight_env FAIL — see diagnostics above"
  exit 1
fi

# -----------------------------------------------------------------------------
# Phase: cdk_lock_acquire — exclusive lock against concurrent cdk invocations.
#
# Surfaced by clean-deploy run 16 (2026-06-05): a separate `cdk deploy
# ConnectorStack` invocation from another architect's session running in the
# same DEPLOYMENT_DIR raced on cdk.out/manifest.json mid-synth, and one of
# the cdk processes was killed (SIGKILL via make[1]). 3.5h cycle wasted.
#
# Three checks:
#   1. No OTHER cdk process is currently running (deploy/synth/destroy/diff/
#      bootstrap). pgrep-based — would catch any concurrent OEM1, prod, or
#      ad-hoc deploy that's about to corrupt cdk.out.
#   2. No existing lock file is alive (i.e., another harness in flight).
#   3. Stale lock files (PID dead) are auto-recovered (removed + proceed).
#
# Issue: backlog row "harness cdk-lock sentinel" (filed post-run-16).
# -----------------------------------------------------------------------------
log "▶ Phase: cdk_lock_acquire"
LOCK_FILE="${DEPLOYMENT_DIR}/.cdk-harness-lock"

# 1. Check for OTHER cdk activity in any directory (cdk.out + AWS creds shared).
OTHER_CDK_PIDS=$(pgrep -f 'cdk[[:space:]]+(deploy|synth|destroy|diff|bootstrap|ls)' 2>/dev/null | grep -v "^$$\$" || true)
if [ -n "${OTHER_CDK_PIDS}" ]; then
  err "  ✗ cdk_lock_acquire FAIL — another cdk process is running:"
  pgrep -fl 'cdk[[:space:]]+(deploy|synth|destroy|diff|bootstrap|ls)' 2>/dev/null | grep -v "^$$ " | sed 's/^/      /' || true
  err "    Wait for it to finish (or kill it) before re-running clean-deploy-test."
  err "    Two cdk processes share cdk.out/ + bootstrap state and corrupt each other."
  record_phase "cdk_lock_acquire" "FAIL"
  exit 1
fi

# 2. Check existing lock file (active vs. stale).
if [ -f "${LOCK_FILE}" ]; then
  EXISTING_PID=$(awk -F= '/^pid=/ {print $2; exit}' "${LOCK_FILE}" 2>/dev/null || true)
  if [ -n "${EXISTING_PID}" ] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    err "  ✗ cdk_lock_acquire FAIL — another harness run is in progress:"
    err "      lock=${LOCK_FILE}"
    sed 's/^/      /' "${LOCK_FILE}" | head -10
    err "    Wait for that run to finish (or kill PID ${EXISTING_PID})."
    record_phase "cdk_lock_acquire" "FAIL"
    exit 1
  else
    log "  ⚠ stale lock found (PID ${EXISTING_PID:-unknown} not alive); recovering"
    rm -f "${LOCK_FILE}"
  fi
fi

# 3. Write our lock.
cat > "${LOCK_FILE}" <<EOF
pid=$$
run_id=${RUN_ID}
region=${AWS_REGION}
stage=${DEPLOYMENT_STAGE}
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
log "  ✓ cdk_lock_acquire PASS — lock=${LOCK_FILE}"
record_phase "cdk_lock_acquire" "PASS"

# -----------------------------------------------------------------------------
# Phase: isolate_cdk_context — relocate the operator's persistent
# deployment/cdk.context.json out of CDK's lookup path for the duration
# of the harness run. Restored by restore_cdk_context in the EXIT trap.
#
# Closes the cross-region CloudFront alias collision surfaced by run 8
# (issues/2026-06-03-clean-deploy-cf-alias-cross-region-collision/):
# operator's cdk.context.json persists 5 domain-shaped keys
# (uiCustomDomain, uiCustomDomainCertArn, uiCustomDomainManageDns,
# stagingGateKeyGroupId, frontendBucketName + prod-namespaced variants)
# from prior auth-gate work, which override clean-deploy.env's no-domain
# intent and attach the operator's primary-region alias to a non-default
# region's distribution. CloudFront enforces global-namespace uniqueness
# on aliases → 409 InvalidRequest → CFN rollback after ~50min wall-time.
#
# Spec: .kiro/specs/2026-06-03-cms-clean-deploy-context-isolation/spec.md
# -----------------------------------------------------------------------------
log "▶ Phase: isolate_cdk_context"
if isolate_cdk_context; then
  record_phase "isolate_cdk_context" "PASS"
else
  record_phase "isolate_cdk_context" "FAIL"
  exit 1
fi

# -----------------------------------------------------------------------------
# Phase: bootstrap_region — CDK bootstrap of the target region.
# Runs BEFORE the strict pre-flight so the strict gate sees a real region
# state, not a "missing bootstrap" false-positive.
# -----------------------------------------------------------------------------
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --no-cli-pager)"
if [ -z "${ACCOUNT_ID}" ] || [ "${ACCOUNT_ID}" = "None" ]; then
  err "Could not resolve AWS account ID via sts get-caller-identity."
  exit 1
fi

run_phase "bootstrap_region" \
  bash -c "cd '${DEPLOYMENT_DIR}' && cdk bootstrap 'aws://${ACCOUNT_ID}/${AWS_REGION}' --require-approval never" \
  || exit 1

# -----------------------------------------------------------------------------
# Phase: bootstrap_us_east_1 — no-op in v1 default mode.
# docs/tech.md §5: clean-deploy.env does not set uiCustomDomain /
# uiCustomDomainCertArn, so no us-east-1 ACM cert is needed and no
# us-east-1 bootstrap is required.
# -----------------------------------------------------------------------------
log "▶ Phase: bootstrap_us_east_1"
log "  ⚠ Skipped — clean-deploy.env does not set uiCustomDomain (no us-east-1 cert dep)"
log "    See docs/tech.md §5 'CloudFront cert / us-east-1 bootstrap requirement'"
record_phase "bootstrap_us_east_1" "SKIP"

# -----------------------------------------------------------------------------
# Phase: preflight_account — strict pre-flight gate.
# Now runs against a bootstrapped region; exits non-zero if any
# residual cms-{stage}-* resources exist.
# -----------------------------------------------------------------------------
run_phase "preflight_account" \
  python3 "${SCRIPT_DIR}/preflight_region_clean.py" \
    --region "${AWS_REGION}" --stage "${DEPLOYMENT_STAGE}" --strict \
  || exit 1

# -----------------------------------------------------------------------------
# Phase: deploy_all — full CMS stack deploy.
# Routed through `make` (not raw `cdk deploy`) per tasks.md constraint
# so credentials, profile, and stack ordering match `make staging-deploy`.
# -----------------------------------------------------------------------------
run_phase "deploy_all" \
  bash -c "cd '${DEPLOYMENT_DIR}' && AWS_REGION='${AWS_REGION}' AWS_DEFAULT_REGION='${AWS_REGION}' CDK_DEFAULT_REGION='${AWS_REGION}' CDK_DEFAULT_ACCOUNT='${ACCOUNT_ID}' DEPLOYMENT_STAGE='${DEPLOYMENT_STAGE}' make deploy-all" \
  || exit 1

# -----------------------------------------------------------------------------
# Phase: deploy_bedrock_agents — Bedrock agents stack with resolved profile.
# BEDROCK_AGENT_MODEL is the existing Makefile knob; setting it here
# overrides the default `us.anthropic.claude-sonnet-4-6` with the
# region-resolved profile (e.g. `jp.anthropic.claude-sonnet-4-6` in
# ap-northeast-1). The Group 3.3 stack changes use the same env var
# at synth time to regenerate the IAM resource ARNs.
# -----------------------------------------------------------------------------
run_phase "deploy_bedrock_agents" \
  bash -c "cd '${DEPLOYMENT_DIR}' && AWS_REGION='${AWS_REGION}' AWS_DEFAULT_REGION='${AWS_REGION}' CDK_DEFAULT_REGION='${AWS_REGION}' CDK_DEFAULT_ACCOUNT='${ACCOUNT_ID}' DEPLOYMENT_STAGE='${DEPLOYMENT_STAGE}' BEDROCK_INFERENCE_PROFILE_ID='${BEDROCK_INFERENCE_PROFILE_ID}' BEDROCK_AGENT_MODEL='${BEDROCK_INFERENCE_PROFILE_ID}' make deploy-bedrock-agents" \
  || exit 1

# -----------------------------------------------------------------------------
# Phase: seed — demo data + KB content.
# Per docs/tech.md "Bedrock-agents KB seed sequence", the KB content is
# materialized as a side-effect of `seed-service-warranty` (run inside
# `seed-all-demo-data`). No separate KB seed step needed in v1.
# -----------------------------------------------------------------------------
run_phase "seed" \
  bash -c "cd '${DEPLOYMENT_DIR}' && AWS_REGION='${AWS_REGION}' AWS_DEFAULT_REGION='${AWS_REGION}' CDK_DEFAULT_REGION='${AWS_REGION}' CDK_DEFAULT_ACCOUNT='${ACCOUNT_ID}' DEPLOYMENT_STAGE='${DEPLOYMENT_STAGE}' make seed-all-demo-data" \
  || exit 1

# Implicit: KB seed completed inside `seed-all-demo-data`. Record as
# PASS so the report.json reflects the spec's `kb_seed` phase key.
record_phase "kb_seed" "PASS"

# -----------------------------------------------------------------------------
# Phase: resolve_ui_outputs — read CloudFront + API URLs from cms-{stage}-ui.
# Group 4.2 extension: the test suite reads CMS_CLEAN_DEPLOY_CF_URL and
# CMS_E2E_ENDPOINT (the latter unblocks tests/e2e/conftest.py's module-level
# skip). Resolved here so the values feed the tests_e2e phase below.
# -----------------------------------------------------------------------------
log "▶ Phase: resolve_ui_outputs"
UI_OUTPUTS_LOG="${RUN_DIR}/resolve_ui_outputs.log"
{
  # JMESPath query strings deliberately use single-quoted backticks for
  # literals; they are not shell expressions. Disable SC2016 here.
  # shellcheck disable=SC2016
  CMS_CLEAN_DEPLOY_CF_URL="$(aws cloudformation describe-stacks \
    --stack-name "cms-${DEPLOYMENT_STAGE}-ui" --region "${AWS_REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' \
    --output text --no-cli-pager 2>>"${UI_OUTPUTS_LOG}")"
  # shellcheck disable=SC2016
  CMS_E2E_ENDPOINT="$(aws cloudformation describe-stacks \
    --stack-name "cms-${DEPLOYMENT_STAGE}-ui" --region "${AWS_REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
    --output text --no-cli-pager 2>>"${UI_OUTPUTS_LOG}")"
} || {
  record_phase "resolve_ui_outputs" "FAIL"
  err "  ✗ resolve_ui_outputs FAIL (log: ${UI_OUTPUTS_LOG})"
  exit 1
}
if [ -z "${CMS_CLEAN_DEPLOY_CF_URL}" ] || [ "${CMS_CLEAN_DEPLOY_CF_URL}" = "None" ] \
   || [ -z "${CMS_E2E_ENDPOINT}" ] || [ "${CMS_E2E_ENDPOINT}" = "None" ]; then
  record_phase "resolve_ui_outputs" "FAIL"
  err "  ✗ resolve_ui_outputs FAIL — empty CloudFrontURL or APIEndpoint output"
  err "    CMS_CLEAN_DEPLOY_CF_URL=${CMS_CLEAN_DEPLOY_CF_URL:-<empty>}"
  err "    CMS_E2E_ENDPOINT=${CMS_E2E_ENDPOINT:-<empty>}"
  exit 1
fi
{
  printf 'CMS_CLEAN_DEPLOY_CF_URL=%s\n' "${CMS_CLEAN_DEPLOY_CF_URL}"
  printf 'CMS_E2E_ENDPOINT=%s\n'        "${CMS_E2E_ENDPOINT}"
} >>"${UI_OUTPUTS_LOG}"
record_phase "resolve_ui_outputs" "PASS"
log "  ✓ resolve_ui_outputs PASS (log: ${UI_OUTPUTS_LOG})"

export CMS_CLEAN_DEPLOY_CF_URL CMS_E2E_ENDPOINT

# -----------------------------------------------------------------------------
# Phase: tests_e2e — pytest setup-layer + telemetry assertions.
#
# Env vars consumed by tests/e2e/test_clean_deploy.py fixtures
# (Group 4.1 + 4.2):
#   CMS_CLEAN_DEPLOY_REGION       — target region
#   CMS_CLEAN_DEPLOY_STAGE        — deployment stage (always 'staging' v1)
#   CMS_CLEAN_DEPLOY_ACCOUNT      — AWS account ID (resolved earlier)
#   CMS_CLEAN_DEPLOY_CF_URL       — CloudFront distribution URL
#   CMS_CLEAN_DEPLOY_RUN_ID       — per-run identifier (for VIN
#                                   uniqueness + flink-logs path)
#   BEDROCK_INFERENCE_PROFILE_ID  — resolved by preflight_per_region.py
#   CMS_E2E_ENDPOINT              — bypasses the existing
#                                   tests/e2e/conftest.py module-level
#                                   skip (it requires this var)
#   RUN_LOG_ROOT                  — flink-logs.txt destination root for
#                                   test_trip_materializes (4.2)
# -----------------------------------------------------------------------------
run_phase "tests_e2e" \
  bash -c "cd '${REPO_ROOT}' && \
    AWS_REGION='${AWS_REGION}' \
    AWS_DEFAULT_REGION='${AWS_REGION}' \
    CDK_DEFAULT_REGION='${AWS_REGION}' \
    CDK_DEFAULT_ACCOUNT='${ACCOUNT_ID}' \
    DEPLOYMENT_STAGE='${DEPLOYMENT_STAGE}' \
    BEDROCK_INFERENCE_PROFILE_ID='${BEDROCK_INFERENCE_PROFILE_ID}' \
    CMS_CLEAN_DEPLOY_REGION='${AWS_REGION}' \
    CMS_CLEAN_DEPLOY_STAGE='${DEPLOYMENT_STAGE}' \
    CMS_CLEAN_DEPLOY_ACCOUNT='${ACCOUNT_ID}' \
    CMS_CLEAN_DEPLOY_CF_URL='${CMS_CLEAN_DEPLOY_CF_URL}' \
    CMS_CLEAN_DEPLOY_RUN_ID='${RUN_ID}' \
    CMS_E2E_ENDPOINT='${CMS_E2E_ENDPOINT}' \
    RUN_LOG_ROOT='${RUN_LOG_ROOT}' \
    tests/e2e/.venv/bin/python3 -m pytest tests/e2e/test_clean_deploy.py --tb=short -m e2e" \
  || exit 1

log "═══════════════════════════════════════════════════════════════"
log "All in-scope phases PASS — letting trap fire teardown + audit"
log "═══════════════════════════════════════════════════════════════"

# Trap fires on normal exit and runs teardown + audit + report.
exit 0

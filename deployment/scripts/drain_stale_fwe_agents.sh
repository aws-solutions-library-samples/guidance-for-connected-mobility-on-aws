#!/usr/bin/env bash
#
# drain_stale_fwe_agents.sh — stop every RUNNING `cms-${STAGE}-fwe-agent`
# task whose `taskDefinitionArn` revision is below the family's latest
# active revision. Idempotent — running on a fully-current cluster
# exits 0 with no side effect.
#
# Implements Component 1 of spec
# `.kiro/specs/2026-05-30-cms-sim-lifecycle-hardening/`.
#
# Why this exists:
#   When `cdk deploy` bumps the `cms-{stage}-fwe-agent` task definition
#   revision, ECS does NOT auto-replace previously-launched task
#   instances (one-shot `run_task`, not service-managed). The old
#   container persists `Up (unhealthy)`, holding every ISO-TP socket
#   binding on its assigned vcan. The new task launches but
#   `ExampleUDSInterface::openCANChannelPort()` fails with
#   `Cannot allocate memory (ENOMEM)` because the kernel `can_isotp`
#   socket pool is starved. This script reaps the old containers so
#   the new revision can claim the kernel sockets.
#
# Inputs (env):
#   STAGE        required — e.g. `staging`
#   AWS_REGION   required — e.g. `us-west-2`
#   AWS_PROFILE  optional — passed through to all `aws` calls
#
# Flags:
#   --dry-run                Print the plan; do NOT call stop-task
#   --timeout-seconds N      Wait up to N seconds for each stopped task
#                            to reach lastStatus=STOPPED (default 60).
#                            Use 0 to skip the wait phase entirely.
#   --poll-interval N        Poll interval for the STOPPED wait (default 5).
#   -h | --help              Print usage and exit 0.
#
# Exits:
#   0   success (drain complete; or no stale tasks; or no tasks at all)
#   1   one or more stop-task calls failed (excluding the benign
#       InvalidParameterException race)
#   2   missing/invalid required env or flag
#
# Per ~/.kiro/steering/non-interactive.md: no prompts, no interactive
# confirmations, no TTY assumptions.

set -euo pipefail
IFS=$'\n\t'
export AWS_PAGER=""

# ---------------------------------------------------------------------------
# Logging helpers (stderr for diagnostics; stdout for progress / summary)
# ---------------------------------------------------------------------------

log()  { printf '[drain] %s\n' "$*"; }
warn() { printf '[drain][WARN] %s\n' "$*" >&2; }
err()  { printf '[drain][ERROR] %s\n' "$*" >&2; }

usage() {
    sed -n '2,/^# Per/p' "$0" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
# Argument parsing (mac bash 3.2 compatible — no associative arrays)
# ---------------------------------------------------------------------------

DRY_RUN=0
TIMEOUT_SECONDS=60
POLL_INTERVAL=5

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --timeout-seconds)
            shift
            if [ $# -eq 0 ] || ! [[ "$1" =~ ^[0-9]+$ ]]; then
                err "--timeout-seconds requires a non-negative integer"
                exit 2
            fi
            TIMEOUT_SECONDS="$1"
            shift
            ;;
        --poll-interval)
            shift
            if [ $# -eq 0 ] || ! [[ "$1" =~ ^[1-9][0-9]*$ ]]; then
                err "--poll-interval requires a positive integer"
                exit 2
            fi
            POLL_INTERVAL="$1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "unknown argument: $1"
            usage >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Required env validation
# ---------------------------------------------------------------------------

: "${STAGE:?STAGE env var is required (e.g. STAGE=staging)}"
: "${AWS_REGION:?AWS_REGION env var is required (e.g. AWS_REGION=us-west-2)}"

CLUSTER="cms-${STAGE}-simulation"
FAMILY="cms-${STAGE}-fwe-agent"

# Wrap aws so AWS_PROFILE is forwarded only when set, and --region is
# always pinned to AWS_REGION (never the operator's default profile).
aws_cli() {
    if [ -n "${AWS_PROFILE:-}" ]; then
        command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        command aws --region "$AWS_REGION" "$@"
    fi
}

# ---------------------------------------------------------------------------
# Step 1 — resolve latest active revision
# ---------------------------------------------------------------------------

log "resolving latest revision for family=${FAMILY} cluster=${CLUSTER}"
TD_JSON=$(aws_cli ecs describe-task-definition \
    --task-definition "$FAMILY" \
    --output json)

LATEST_REV=$(printf '%s' "$TD_JSON" \
    | python3 -c 'import json,sys; td=json.load(sys.stdin).get("taskDefinition", {}); print(td.get("revision", 0))')

if ! [[ "$LATEST_REV" =~ ^[0-9]+$ ]]; then
    err "could not parse revision from describe-task-definition output: $TD_JSON"
    exit 1
fi
log "latest active revision: ${LATEST_REV}"

# ---------------------------------------------------------------------------
# Step 2 — list RUNNING tasks for the family (paginated)
# ---------------------------------------------------------------------------
#
# `aws ecs list-tasks` returns at most 100 task ARNs per page. The fwe-agent
# cluster is well under 100 today (baseline.md), but pagination is a forward-
# safety hardening per review.md Cycle 1 Warning 2026-06-22 ("if the cluster
# ever exceeds 100 RUNNING fwe-agent tasks, stale tasks beyond page 1 will
# not be drained"). We accumulate ARNs across pages by following the
# `nextToken` echo until it's null.

TASK_ARNS=""
NEXT_TOKEN=""
PAGE_COUNT=0
while :; do
    PAGE_COUNT=$((PAGE_COUNT + 1))
    if [ -z "$NEXT_TOKEN" ]; then
        LIST_JSON=$(aws_cli ecs list-tasks \
            --cluster "$CLUSTER" \
            --family "$FAMILY" \
            --desired-status RUNNING)
    else
        LIST_JSON=$(aws_cli ecs list-tasks \
            --cluster "$CLUSTER" \
            --family "$FAMILY" \
            --desired-status RUNNING \
            --next-token "$NEXT_TOKEN")
    fi

    # Parse this page's taskArns + the nextToken (if any).
    PAGE_PARSE=$(printf '%s' "$LIST_JSON" | python3 -c '
import json, sys
data = json.load(sys.stdin)
arns = data.get("taskArns", []) or []
tok = data.get("nextToken") or ""
# Output: nextToken on first line, then one ARN per remaining line.
print(tok)
for a in arns:
    print(a)
')
    NEXT_TOKEN=$(printf '%s' "$PAGE_PARSE" | head -n 1)
    PAGE_ARNS=$(printf '%s' "$PAGE_PARSE" | tail -n +2)
    if [ -n "$PAGE_ARNS" ]; then
        if [ -n "$TASK_ARNS" ]; then
            TASK_ARNS="${TASK_ARNS}"$'\n'"${PAGE_ARNS}"
        else
            TASK_ARNS="${PAGE_ARNS}"
        fi
    fi

    if [ -z "$NEXT_TOKEN" ]; then
        break
    fi
    # Defensive cap: 100 pages = 10,000 tasks; the cluster is nowhere near
    # this and an unbounded loop is a worse failure mode than a cap.
    if [ "$PAGE_COUNT" -ge 100 ]; then
        warn "list-tasks pagination cap (100 pages) hit; some tasks may be unenumerated"
        break
    fi
done

if [ -z "$TASK_ARNS" ]; then
    log "no fwe-agent tasks to drain"
    exit 0
fi

# Count tasks (mac bash 3.2: avoid mapfile/<<<; use line count)
TOTAL_COUNT=$(printf '%s\n' "$TASK_ARNS" | grep -c .)
log "found ${TOTAL_COUNT} RUNNING fwe-agent task(s)"

# ---------------------------------------------------------------------------
# Step 3 — describe each task; classify by revision
# ---------------------------------------------------------------------------

# Build a positional-args array of ARNs for `--tasks` (max 100 per
# call; fwe-agent cluster scale is well under 100). We intentionally
# avoid relying on word-splitting an unquoted variable: this script's
# IFS is set to $'\n\t' (no space), so a space-separated string would
# arrive at the AWS CLI as a single argument with embedded spaces and
# trip `taskId length should be one of [32,36]`. Mac bash 3.2 supports
# indexed arrays, so this is portable.
TASK_ARN_ARRAY=()
while IFS= read -r _arn; do
    [ -z "$_arn" ] && continue
    TASK_ARN_ARRAY[${#TASK_ARN_ARRAY[@]}]="$_arn"
done <<EOF
$TASK_ARNS
EOF

# describe-tasks
DESC_JSON=$(aws_cli ecs describe-tasks \
    --cluster "$CLUSTER" \
    --tasks "${TASK_ARN_ARRAY[@]}")

# Build tab-separated list "<taskArn>\t<revision>" for each task. Mac
# bash 3.2 lacks associative arrays, so we use parallel arrays.
CLASSIFY=$(printf '%s' "$DESC_JSON" | python3 -c '
import json, sys
data = json.load(sys.stdin)
for t in data.get("tasks", []):
    arn = t.get("taskArn", "")
    td = t.get("taskDefinitionArn", "")
    # td is .../family:N — split on last ":" to extract N
    rev = td.rsplit(":", 1)[-1] if ":" in td else "0"
    try:
        rev = int(rev)
    except ValueError:
        rev = 0
    if arn:
        print(f"{arn}\t{rev}")
')

# Build the list of stale ARNs and a "rev=N" mapping for logging.
STALE_ARNS=""
STALE_COUNT=0
while IFS=$'\t' read -r ARN REV; do
    [ -z "$ARN" ] && continue
    if [ "$REV" -lt "$LATEST_REV" ]; then
        STALE_ARNS="${STALE_ARNS}${ARN}"$'\n'
        STALE_COUNT=$((STALE_COUNT + 1))
        log "stale: ${ARN} (rev=${REV})"
    else
        log "current: ${ARN} (rev=${REV})"
    fi
done <<EOF
$CLASSIFY
EOF

if [ "$STALE_COUNT" -eq 0 ]; then
    log "all ${TOTAL_COUNT} tasks on latest rev=${LATEST_REV} (no drain needed)"
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 4 — stop stale tasks
# ---------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY RUN: would stop ${STALE_COUNT} stale task(s); skipping stop-task and wait"
    log "drained 0 of ${TOTAL_COUNT} fwe-agent tasks (dry-run; latest rev=${LATEST_REV})"
    exit 0
fi

STOPPED_OK=0
STOPPED_FAILED=0
STOP_FAIL_DETAIL=""

while IFS= read -r ARN; do
    [ -z "$ARN" ] && continue
    REASON="drain stale fwe-agent before launching latest revision ${LATEST_REV}"
    # We capture both stdout and stderr because the AWS CLI writes
    # error responses to stderr. We need the exit code and the
    # stderr text to classify the failure.
    STOP_OUT=""
    STOP_ERR=""
    STOP_RC=0
    set +e
    STOP_TMP_ERR=$(mktemp -t drain-stop-stderr-XXXXXX)
    STOP_OUT=$(aws_cli ecs stop-task \
        --cluster "$CLUSTER" \
        --task "$ARN" \
        --reason "$REASON" 2>"$STOP_TMP_ERR")
    STOP_RC=$?
    STOP_ERR=$(cat "$STOP_TMP_ERR")
    rm -f "$STOP_TMP_ERR"
    set -e

    if [ "$STOP_RC" -eq 0 ]; then
        log "stopped: ${ARN}"
        STOPPED_OK=$((STOPPED_OK + 1))
        continue
    fi

    # Race: the task transitioned to STOPPED on its own between list and
    # stop. The CLI returns InvalidParameterException; treat as benign.
    case "$STOP_ERR" in
        *InvalidParameterException*)
            log "stopped: ${ARN} (already stopping — race, treated as benign)"
            STOPPED_OK=$((STOPPED_OK + 1))
            ;;
        *)
            err "stop-task failed for ${ARN}: ${STOP_ERR}"
            STOPPED_FAILED=$((STOPPED_FAILED + 1))
            STOP_FAIL_DETAIL="${STOP_FAIL_DETAIL}${ARN}: ${STOP_ERR}"$'\n'
            ;;
    esac
done <<EOF
$STALE_ARNS
EOF

# ---------------------------------------------------------------------------
# Step 5 — wait for STOPPED (skip if --timeout-seconds 0)
# ---------------------------------------------------------------------------

if [ "$TIMEOUT_SECONDS" -gt 0 ] && [ "$STOPPED_OK" -gt 0 ]; then
    log "waiting up to ${TIMEOUT_SECONDS}s for stopped tasks to transition (poll every ${POLL_INTERVAL}s)"
    DEADLINE=$(( $(date +%s) + TIMEOUT_SECONDS ))
    # Build the originally-stale ARN array once for the wait loop. Same
    # IFS-safety reason as the describe-tasks call above.
    STALE_ARN_ARRAY=()
    while IFS= read -r _arn; do
        [ -z "$_arn" ] && continue
        STALE_ARN_ARRAY[${#STALE_ARN_ARRAY[@]}]="$_arn"
    done <<EOF
$STALE_ARNS
EOF
    while :; do
        if [ "${#STALE_ARN_ARRAY[@]}" -eq 0 ]; then
            break
        fi
        WAIT_DESC=$(aws_cli ecs describe-tasks \
            --cluster "$CLUSTER" \
            --tasks "${STALE_ARN_ARRAY[@]}" 2>/dev/null || echo '{"tasks":[]}')
        REMAINING=$(printf '%s' "$WAIT_DESC" | python3 -c '
import json, sys
data = json.load(sys.stdin)
remaining = sum(1 for t in data.get("tasks", []) if t.get("lastStatus") != "STOPPED")
print(remaining)
')
        if [ "$REMAINING" -eq 0 ]; then
            log "all stale tasks reached lastStatus=STOPPED"
            break
        fi
        NOW=$(date +%s)
        if [ "$NOW" -ge "$DEADLINE" ]; then
            warn "timeout: ${REMAINING} task(s) still not STOPPED after ${TIMEOUT_SECONDS}s — proceeding anyway (check ECS console)"
            break
        fi
        sleep "$POLL_INTERVAL"
    done
fi

# ---------------------------------------------------------------------------
# Step 6 — summary + exit
# ---------------------------------------------------------------------------

log "drained ${STOPPED_OK} of ${TOTAL_COUNT} fwe-agent tasks (latest rev=${LATEST_REV})"

if [ "$STOPPED_FAILED" -gt 0 ]; then
    err "${STOPPED_FAILED} stop-task call(s) failed:"
    printf '%s' "$STOP_FAIL_DETAIL" >&2
    exit 1
fi

exit 0

#!/usr/bin/env bash
#
# test_drain_stale_fwe_agents.sh — green-phase tests for
# `deployment/scripts/drain_stale_fwe_agents.sh`.
#
# Group 2 Task 2.1 (Tests started in red phase as Group 1 Task 1.3.)
#
# All AWS calls are stubbed via a fake `aws` shim placed first on
# PATH. Per-case fixture JSON lives in
# `deployment/scripts/test_fixtures/drain/case<N>_<desc>/`.
#
# Tests pass `--timeout-seconds 0` so the wait-for-STOPPED phase is
# skipped — the wait path itself is exercised by the timeout warn
# branch in case 6 (added if needed; see Constraints in spec.md).
#
# Runs on stock macOS bash 3.2 — no associative arrays, no `mapfile`,
# no `<<<` here-strings.
#
# Usage:
#   bash deployment/scripts/test_drain_stale_fwe_agents.sh
#   echo $?   # 0 = all tests passed; 1 = at least one failed
#
# Exit codes:
#   0    all 5 test cases pass
#   1    at least one test case failed an assertion
#   2    test infrastructure error (e.g., fixture dir missing)

set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRAIN_SCRIPT="${SCRIPT_DIR}/drain_stale_fwe_agents.sh"
FIXTURE_DIR="${SCRIPT_DIR}/test_fixtures/drain"

FAIL_COUNT=0
PASS_COUNT=0
TOTAL_COUNT=0

red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
blue()   { printf '\033[0;34m%s\033[0m\n' "$*"; }

assert_eq() {
    local desc="$1"
    local expected="$2"
    local actual="$3"
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    if [ "$expected" = "$actual" ]; then
        PASS_COUNT=$((PASS_COUNT + 1))
        green "  ✓ ${desc}"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        red "  ✗ ${desc}"
        red "      expected: ${expected}"
        red "      actual:   ${actual}"
    fi
}

assert_contains() {
    local desc="$1"
    local haystack="$2"
    local needle="$3"
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    case "$haystack" in
        *"$needle"*)
            PASS_COUNT=$((PASS_COUNT + 1))
            green "  ✓ ${desc}"
            ;;
        *)
            FAIL_COUNT=$((FAIL_COUNT + 1))
            red "  ✗ ${desc}"
            red "      expected to contain: ${needle}"
            red "      actual:              ${haystack}"
            ;;
    esac
}

assert_not_contains() {
    local desc="$1"
    local haystack="$2"
    local needle="$3"
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    case "$haystack" in
        *"$needle"*)
            FAIL_COUNT=$((FAIL_COUNT + 1))
            red "  ✗ ${desc}"
            red "      did NOT expect to contain: ${needle}"
            red "      actual:                    ${haystack}"
            ;;
        *)
            PASS_COUNT=$((PASS_COUNT + 1))
            green "  ✓ ${desc}"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Fake AWS CLI shim
# ---------------------------------------------------------------------------

setup_fake_aws() {
    local fixture_subdir="$1"
    local tmp_path
    tmp_path="$(mktemp -d -t drain-test-XXXXXX)"

    # Capture invocations to a per-case log so tests can assert which
    # subcommands were called and with what args.
    export _DRAIN_TEST_INVOCATIONS="${tmp_path}/invocations.log"
    : > "$_DRAIN_TEST_INVOCATIONS"

    cat > "${tmp_path}/aws" <<'SHIM_EOF'
#!/usr/bin/env bash
# Fake AWS CLI shim. Reads canned JSON from $DRAIN_TEST_FIXTURE/<key>.json.
# Logs every invocation to $_DRAIN_TEST_INVOCATIONS.
set -e
fixture_dir="${DRAIN_TEST_FIXTURE:-/tmp/drain-fixture-missing}"

# Strip leading --profile / --region (the real CLI accepts them anywhere)
# so the service / subcommand stay at $1 / $2.
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --profile|--region)
            shift; shift
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done
set -- "${args[@]}"

service="${1:-noservice}"
subcmd="${2:-nosubcmd}"

# Log full argv (post-strip) for assertions.
{
    printf 'aws'
    for a in "$@"; do printf ' %q' "$a"; done
    printf '\n'
} >> "${_DRAIN_TEST_INVOCATIONS:-/dev/null}"

fixture_file="${fixture_dir}/${service}_${subcmd}.json"

# Per-call counter — multiple invocations of the same subcommand return
# different responses if .1 / .2 / ... fixtures exist.
counter_file="${fixture_dir}/.${service}_${subcmd}.counter"
if [ -f "${fixture_file}.1" ]; then
    n=1
    if [ -f "$counter_file" ]; then
        n=$(($(cat "$counter_file") + 1))
    fi
    echo "$n" > "$counter_file"
    if [ -f "${fixture_file}.${n}" ]; then
        cat "${fixture_file}.${n}"
        exit 0
    fi
    cat "${fixture_file}.1"
    exit 0
fi

# Error injection: fixture_dir/<service>_<subcmd>.exit overrides the rc;
# .stderr is emitted to stderr.
if [ -f "${fixture_dir}/${service}_${subcmd}.exit" ]; then
    rc=$(cat "${fixture_dir}/${service}_${subcmd}.exit")
    if [ -f "${fixture_dir}/${service}_${subcmd}.stderr" ]; then
        cat "${fixture_dir}/${service}_${subcmd}.stderr" >&2
    fi
    exit "$rc"
fi

if [ ! -f "$fixture_file" ]; then
    echo "fake-aws: no fixture for ${service} ${subcmd} (looked at ${fixture_file})" >&2
    exit 254
fi

cat "$fixture_file"
SHIM_EOF
    chmod +x "${tmp_path}/aws"
    export DRAIN_TEST_FIXTURE="${FIXTURE_DIR}/${fixture_subdir}"
    export PATH="${tmp_path}:${PATH}"
    export _DRAIN_TEST_TMP_PATH="${tmp_path}"
}

teardown_fake_aws() {
    if [ -n "${_DRAIN_TEST_TMP_PATH:-}" ] && [ -d "$_DRAIN_TEST_TMP_PATH" ]; then
        rm -rf "$_DRAIN_TEST_TMP_PATH"
    fi
    # Reset PATH to what we had before setup (drop the leading tmp dir).
    PATH="${PATH#*:}"
    export PATH
    unset _DRAIN_TEST_TMP_PATH
    unset _DRAIN_TEST_INVOCATIONS
    unset DRAIN_TEST_FIXTURE
    # Reset per-case fixture counter files so cases don't leak state.
    if [ -d "$FIXTURE_DIR" ]; then
        find "$FIXTURE_DIR" -maxdepth 2 -name '.*.counter' -delete 2>/dev/null || true
    fi
}

# Run the drain script with required env, capturing stdout / stderr / rc.
run_drain() {
    LAST_STDOUT=""
    LAST_STDERR=""
    LAST_RC=0
    local tmp_out tmp_err
    tmp_out="$(mktemp -t drain-stdout-XXXXXX)"
    tmp_err="$(mktemp -t drain-stderr-XXXXXX)"
    set +e
    # Tests use --timeout-seconds 0 to skip the wait-for-STOPPED poll
    # (the wait path's behavior is incidental to the drain decision
    # logic, which is the actual subject under test).
    STAGE=staging AWS_REGION=us-west-2 \
        bash "$DRAIN_SCRIPT" --timeout-seconds 0 "$@" \
        >"$tmp_out" 2>"$tmp_err"
    LAST_RC=$?
    set -e
    LAST_STDOUT="$(cat "$tmp_out")"
    LAST_STDERR="$(cat "$tmp_err")"
    rm -f "$tmp_out" "$tmp_err"
}

invocations() {
    cat "${_DRAIN_TEST_INVOCATIONS:-/dev/null}"
}

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

test_case_1_no_tasks() {
    blue "Case 1: no tasks running → exits 0, prints 'no fwe-agent tasks to drain'"
    setup_fake_aws case1_no_tasks
    run_drain
    assert_eq      "case1: exit code 0"               "0" "$LAST_RC"
    assert_contains "case1: 'no fwe-agent tasks to drain' on stdout" \
                   "$LAST_STDOUT" "no fwe-agent tasks to drain"
    assert_not_contains "case1: stop-task NOT invoked" \
                       "$(invocations)" "ecs stop-task"
    teardown_fake_aws
}

test_case_2_all_on_latest() {
    blue "Case 2: all tasks already on latest revision → exits 0, prints 'all N tasks on latest rev=X'"
    setup_fake_aws case2_all_on_latest
    run_drain
    assert_eq      "case2: exit code 0"                                 "0" "$LAST_RC"
    assert_contains "case2: 'all 1 tasks on latest rev=2' on stdout" \
                   "$LAST_STDOUT" "all 1 tasks on latest rev=2"
    assert_not_contains "case2: stop-task NOT invoked" \
                       "$(invocations)" "ecs stop-task"
    teardown_fake_aws
}

test_case_3_mixed_revisions() {
    blue "Case 3: mixed revisions → drain stops only stale tasks, exits 0"
    setup_fake_aws case3_mixed_revisions
    run_drain
    assert_eq       "case3: exit code 0" "0" "$LAST_RC"
    assert_contains "case3: 'drained 1 of 2 fwe-agent tasks (latest rev=3)' on stdout" \
                    "$LAST_STDOUT" "drained 1 of 2 fwe-agent tasks (latest rev=3)"
    # Stop-task invoked exactly once, against the stale ARN (suffix /stale)
    INV="$(invocations)"
    assert_contains "case3: stop-task called for the stale task" \
                    "$INV" "stop-task"
    assert_contains "case3: stop-task target = the stale ARN" \
                    "$INV" "/stale"
    assert_not_contains "case3: stop-task NOT called against the fresh ARN" \
                        "$INV" "ecs stop-task --cluster cms-staging-simulation --task arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/fresh"
    teardown_fake_aws
}

test_case_4_race_invalid_param() {
    blue "Case 4: stop-task returns InvalidParameterException (race) → benign, exits 0"
    setup_fake_aws case4_race_invalid_param
    run_drain
    assert_eq       "case4: exit code 0 (race treated as benign)" "0" "$LAST_RC"
    assert_contains "case4: 'race, treated as benign' or 'already stopping' in output" \
                    "$LAST_STDOUT" "treated as benign"
    teardown_fake_aws
}

test_case_5_generic_stop_error() {
    blue "Case 5: stop-task returns generic error → exits non-zero with stderr diagnostic"
    setup_fake_aws case5_generic_stop_error
    run_drain
    if [ "$LAST_RC" -eq 0 ]; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
        TOTAL_COUNT=$((TOTAL_COUNT + 1))
        red "  ✗ case5: expected non-zero exit code, got 0"
    else
        PASS_COUNT=$((PASS_COUNT + 1))
        TOTAL_COUNT=$((TOTAL_COUNT + 1))
        green "  ✓ case5: exit code non-zero (rc=${LAST_RC})"
    fi
    assert_contains "case5: stderr contains 'AccessDenied' diagnostic" \
                    "$LAST_STDERR" "AccessDenied"
    assert_contains "case5: stderr contains 'stop-task failed for' diagnostic" \
                    "$LAST_STDERR" "stop-task failed for"
    teardown_fake_aws
}

# ---------------------------------------------------------------------------
# Fixture skeleton — created on first run if missing
# ---------------------------------------------------------------------------

ensure_fixture_skeletons() {
    if [ -d "$FIXTURE_DIR" ]; then
        return 0
    fi
    blue "Creating fixture skeletons in ${FIXTURE_DIR}"
    mkdir -p "$FIXTURE_DIR/case1_no_tasks"
    mkdir -p "$FIXTURE_DIR/case2_all_on_latest"
    mkdir -p "$FIXTURE_DIR/case3_mixed_revisions"
    mkdir -p "$FIXTURE_DIR/case4_race_invalid_param"
    mkdir -p "$FIXTURE_DIR/case5_generic_stop_error"

    cat > "$FIXTURE_DIR/case1_no_tasks/ecs_describe-task-definition.json" <<'EOF'
{
  "taskDefinition": {
    "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:2",
    "revision": 2,
    "family": "cms-staging-fwe-agent",
    "status": "ACTIVE"
  }
}
EOF
    cat > "$FIXTURE_DIR/case1_no_tasks/ecs_list-tasks.json" <<'EOF'
{ "taskArns": [] }
EOF

    cat > "$FIXTURE_DIR/case2_all_on_latest/ecs_describe-task-definition.json" <<'EOF'
{
  "taskDefinition": {
    "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:2",
    "revision": 2,
    "family": "cms-staging-fwe-agent",
    "status": "ACTIVE"
  }
}
EOF
    cat > "$FIXTURE_DIR/case2_all_on_latest/ecs_list-tasks.json" <<'EOF'
{ "taskArns": ["arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/aaaa"] }
EOF
    cat > "$FIXTURE_DIR/case2_all_on_latest/ecs_describe-tasks.json" <<'EOF'
{
  "tasks": [
    {
      "taskArn": "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/aaaa",
      "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:2",
      "lastStatus": "RUNNING",
      "desiredStatus": "RUNNING"
    }
  ],
  "failures": []
}
EOF

    cat > "$FIXTURE_DIR/case3_mixed_revisions/ecs_describe-task-definition.json" <<'EOF'
{
  "taskDefinition": {
    "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:3",
    "revision": 3,
    "family": "cms-staging-fwe-agent",
    "status": "ACTIVE"
  }
}
EOF
    cat > "$FIXTURE_DIR/case3_mixed_revisions/ecs_list-tasks.json" <<'EOF'
{ "taskArns": [
    "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/stale",
    "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/fresh"
] }
EOF
    cat > "$FIXTURE_DIR/case3_mixed_revisions/ecs_describe-tasks.json" <<'EOF'
{
  "tasks": [
    {
      "taskArn": "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/stale",
      "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:2",
      "lastStatus": "RUNNING",
      "desiredStatus": "RUNNING"
    },
    {
      "taskArn": "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/fresh",
      "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:3",
      "lastStatus": "RUNNING",
      "desiredStatus": "RUNNING"
    }
  ],
  "failures": []
}
EOF
    cat > "$FIXTURE_DIR/case3_mixed_revisions/ecs_stop-task.json" <<'EOF'
{ "task": { "taskArn": "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/stale", "lastStatus": "DEACTIVATING", "desiredStatus": "STOPPED" } }
EOF

    cp "$FIXTURE_DIR/case3_mixed_revisions/ecs_describe-task-definition.json" \
       "$FIXTURE_DIR/case4_race_invalid_param/ecs_describe-task-definition.json"
    cp "$FIXTURE_DIR/case3_mixed_revisions/ecs_list-tasks.json" \
       "$FIXTURE_DIR/case4_race_invalid_param/ecs_list-tasks.json"
    cp "$FIXTURE_DIR/case3_mixed_revisions/ecs_describe-tasks.json" \
       "$FIXTURE_DIR/case4_race_invalid_param/ecs_describe-tasks.json"
    echo "254" > "$FIXTURE_DIR/case4_race_invalid_param/ecs_stop-task.exit"
    cat > "$FIXTURE_DIR/case4_race_invalid_param/ecs_stop-task.stderr" <<'EOF'
An error occurred (InvalidParameterException) when calling the StopTask operation: The referenced task was already stopped.
EOF
    echo '{}' > "$FIXTURE_DIR/case4_race_invalid_param/ecs_stop-task.json"

    cp "$FIXTURE_DIR/case3_mixed_revisions/ecs_describe-task-definition.json" \
       "$FIXTURE_DIR/case5_generic_stop_error/ecs_describe-task-definition.json"
    cp "$FIXTURE_DIR/case3_mixed_revisions/ecs_list-tasks.json" \
       "$FIXTURE_DIR/case5_generic_stop_error/ecs_list-tasks.json"
    cp "$FIXTURE_DIR/case3_mixed_revisions/ecs_describe-tasks.json" \
       "$FIXTURE_DIR/case5_generic_stop_error/ecs_describe-tasks.json"
    echo "255" > "$FIXTURE_DIR/case5_generic_stop_error/ecs_stop-task.exit"
    cat > "$FIXTURE_DIR/case5_generic_stop_error/ecs_stop-task.stderr" <<'EOF'
An error occurred (AccessDeniedException) when calling the StopTask operation: User: arn:... is not authorized to perform: ecs:StopTask
EOF
    echo '{}' > "$FIXTURE_DIR/case5_generic_stop_error/ecs_stop-task.json"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    blue "test_drain_stale_fwe_agents.sh — GREEN phase"
    blue "================================================================"
    if [ ! -x "$DRAIN_SCRIPT" ]; then
        if [ -f "$DRAIN_SCRIPT" ]; then
            yellow "  drain script exists but is not executable; chmod +x to fix"
            chmod +x "$DRAIN_SCRIPT" || true
        else
            red "  drain script not found at ${DRAIN_SCRIPT}"
            exit 2
        fi
    fi
    ensure_fixture_skeletons

    test_case_1_no_tasks
    test_case_2_all_on_latest
    test_case_3_mixed_revisions
    test_case_4_race_invalid_param
    test_case_5_generic_stop_error

    blue "================================================================"
    if [ "$FAIL_COUNT" -eq 0 ]; then
        green "All ${PASS_COUNT}/${TOTAL_COUNT} assertions passed across 5 test cases."
        exit 0
    fi
    red "${FAIL_COUNT}/${TOTAL_COUNT} assertions failed across the 5 test cases."
    exit 1
}

main "$@"

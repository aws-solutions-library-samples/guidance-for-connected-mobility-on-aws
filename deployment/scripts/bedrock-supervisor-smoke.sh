#!/usr/bin/env bash
#
# bedrock-supervisor-smoke.sh — invoke the CMS Bedrock supervisor agent with a
# fixed set of representative prompts and capture the responses to disk.
#
# Used by the 2026-06-01-cms-sonnet-4-5-bump spec to capture a Sonnet 4.0
# baseline (pre-deploy) and a Sonnet 4.5 candidate (post-deploy) for
# side-by-side comparison. Idempotent — re-running with the same <output-tag>
# overwrites the prior output files.
#
# Usage:
#   bedrock-supervisor-smoke.sh <stage> <region> <profile> <output-tag>
#
# Example:
#   bedrock-supervisor-smoke.sh staging us-west-2 cms-staging baseline-4.0
#
# Outputs:
#   /tmp/sonnet-4-5-bump-smoke-<output-tag>.json   (machine-readable)
#   /tmp/sonnet-4-5-bump-smoke-<output-tag>.md     (human-readable)
#
# Exit codes:
#   0  — all 5 prompts returned non-empty completions
#   1  — argument or pre-flight failure
#   2  — at least one prompt errored or returned empty completion text
#
set -euo pipefail
export AWS_PAGER=""

# --- arg parsing ------------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: bedrock-supervisor-smoke.sh <stage> <region> <profile> <output-tag>

  <stage>       CFN stack stage (e.g., staging, prod). Stack name is
                derived as cms-<stage>-bedrock-agents.
  <region>      AWS region (e.g., us-west-2, us-east-1, ap-northeast-1).
  <profile>     AWS CLI profile name with read+invoke access to the stack
                and the Bedrock supervisor agent.
  <output-tag>  Suffix for output files (no spaces, no slashes). Output
                lands at /tmp/sonnet-4-5-bump-smoke-<output-tag>.{json,md}.

Examples:
  bedrock-supervisor-smoke.sh staging us-west-2 cms-staging baseline-4.0
  bedrock-supervisor-smoke.sh staging us-west-2 cms-staging candidate-4.5
EOF
}

if [ "$#" -lt 1 ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
  usage
  exit 1
fi

if [ "$#" -ne 4 ]; then
  echo "ERROR: expected 4 positional args, got $#" >&2
  usage
  exit 1
fi

STAGE="$1"
REGION="$2"
PROFILE="$3"
TAG="$4"

# Validate tag — no slashes / spaces / control chars.
if [[ "$TAG" =~ [[:space:]/\\] ]]; then
  echo "ERROR: <output-tag> may not contain whitespace or path separators: '$TAG'" >&2
  exit 1
fi

STACK_NAME="cms-${STAGE}-bedrock-agents"
OUT_JSON="/tmp/sonnet-4-5-bump-smoke-${TAG}.json"
OUT_MD="/tmp/sonnet-4-5-bump-smoke-${TAG}.md"

# --- color helpers ----------------------------------------------------------

if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi
log()  { echo -e "${BLUE}[smoke] $*${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }

# --- discover supervisor agent + alias from CFN outputs ---------------------

log "Discovering supervisor agent IDs from $STACK_NAME ($REGION, profile=$PROFILE)…"

if ! AGENT_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --profile "$PROFILE" --region "$REGION" \
    --no-cli-pager --no-paginate \
    --query 'Stacks[0].Outputs[?OutputKey==`PrimaryAgentId`].OutputValue' \
    --output text 2>&1); then
  err "describe-stacks failed for $STACK_NAME: $AGENT_ID"
  exit 1
fi

if [ -z "$AGENT_ID" ] || [ "$AGENT_ID" = "None" ]; then
  err "PrimaryAgentId output not found on stack $STACK_NAME"
  exit 1
fi

if ! ALIAS_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --profile "$PROFILE" --region "$REGION" \
    --no-cli-pager --no-paginate \
    --query 'Stacks[0].Outputs[?OutputKey==`PrimaryAliasId`].OutputValue' \
    --output text 2>&1); then
  err "describe-stacks failed for PrimaryAliasId: $ALIAS_ID"
  exit 1
fi

if [ -z "$ALIAS_ID" ] || [ "$ALIAS_ID" = "None" ]; then
  err "PrimaryAliasId output not found on stack $STACK_NAME"
  exit 1
fi

# Capture the live foundation_model on the supervisor for the report header.
FOUNDATION_MODEL=$(aws bedrock-agent get-agent \
  --agent-id "$AGENT_ID" \
  --profile "$PROFILE" --region "$REGION" \
  --no-cli-pager --no-paginate \
  --query 'agent.foundationModel' \
  --output text 2>/dev/null || echo "unknown")

STACK_LAST_UPDATED=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --profile "$PROFILE" --region "$REGION" \
  --no-cli-pager --no-paginate \
  --query 'Stacks[0].LastUpdatedTime' \
  --output text 2>/dev/null || echo "unknown")

ok "AgentId=$AGENT_ID  AliasId=$ALIAS_ID  foundationModel=$FOUNDATION_MODEL"

# --- prompts ----------------------------------------------------------------

# 5 representative prompts that exercise the 4 specialist sub-agents through
# the supervisor (cost, maintenance, rebalancing, recall-warranty, plus a
# vehicles-list fan-out prompt).
PROMPTS=(
  "List the vehicles in fleet fleet-001"
  "What is the maintenance status of vehicle vin-12345?"
  "Show me cost summary for the last 30 days"
  "Are there any open recalls on vehicle vin-12345?"
  "Rebalance fleet fleet-001 for 3 vehicles"
)

# --- invoke + capture -------------------------------------------------------

INVOKE_DIR=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$INVOKE_DIR'" EXIT

# Initialize JSON output as an array we'll incrementally append to via Python.
# Using Python for JSON assembly avoids quoting hell in pure bash.
python3 - "$OUT_JSON" "$STACK_NAME" "$REGION" "$AGENT_ID" "$ALIAS_ID" "$FOUNDATION_MODEL" "$STACK_LAST_UPDATED" "$TAG" <<'PY'
import json, sys
out_path, stack, region, agent_id, alias_id, fm, updated, tag = sys.argv[1:]
data = {
    "tag": tag,
    "stack_name": stack,
    "region": region,
    "agent_id": agent_id,
    "alias_id": alias_id,
    "foundation_model": fm,
    "stack_last_updated": updated,
    "prompts": [],
}
with open(out_path, "w") as f:
    json.dump(data, f, indent=2)
PY

failures=0
prompt_idx=0
for prompt in "${PROMPTS[@]}"; do
  prompt_idx=$((prompt_idx + 1))
  log "Prompt $prompt_idx/5: $prompt"

  # Bedrock Agent Runtime sessions need a unique session ID per invocation.
  session_id="smoke-${TAG}-${prompt_idx}-$$"

  start_ms=$(python3 -c 'import time; print(int(time.time()*1000))')

  # Use boto3 directly — AWS CLI v2.32.20 dropped the `invoke-agent`
  # subcommand on `bedrock-agent-runtime`, but the underlying API is still
  # there and reachable via the SDK. Returns a single-line JSON document
  # with status / completion / trace_count / error keys.
  result_json=$(python3 - "$AGENT_ID" "$ALIAS_ID" "$session_id" "$prompt" "$REGION" "$PROFILE" <<'PY'
import boto3, sys, json
agent_id, alias_id, session_id, prompt, region, profile = sys.argv[1:]
try:
    session = boto3.Session(profile_name=profile, region_name=region)
    client = session.client('bedrock-agent-runtime')
    resp = client.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
        sessionId=session_id,
        inputText=prompt,
        enableTrace=True,
    )
    chunks = []
    trace_count = 0
    for event in resp['completion']:
        if 'chunk' in event:
            chunks.append(event['chunk'].get('bytes', b'').decode('utf-8', errors='replace'))
        if 'trace' in event:
            trace_count += 1
    print(json.dumps({
        'status': 'ok',
        'completion': ''.join(chunks),
        'trace_count': trace_count,
        'error': '',
    }))
except Exception as e:
    print(json.dumps({
        'status': 'error',
        'completion': '',
        'trace_count': 0,
        'error': f'{type(e).__name__}: {e}',
    }))
PY
)

  end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  latency_ms=$((end_ms - start_ms))

  # Parse the result JSON.
  invoke_status=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['status'])" "$result_json")
  completion_text=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['completion'])" "$result_json")
  trace_count=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['trace_count'])" "$result_json")
  invoke_error=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['error'])" "$result_json")

  if [ "$invoke_status" = "error" ]; then
    err "  invoke-agent failed for prompt $prompt_idx: $invoke_error"
    failures=$((failures + 1))
  fi

  # Empty completion = treat as failure (per task constraints).
  if [ "$invoke_status" = "ok" ] && [ -z "$completion_text" ]; then
    invoke_status="empty"
    err "  completion text was empty for prompt $prompt_idx"
    failures=$((failures + 1))
  fi

  # Append this prompt's record to the JSON output.
  python3 - "$OUT_JSON" "$prompt_idx" "$prompt" "$invoke_status" "$completion_text" "$invoke_error" "$latency_ms" "$trace_count" <<'PY'
import json, sys
out_path, idx, prompt, status, completion, error, latency, traces = sys.argv[1:]
with open(out_path) as f:
    data = json.load(f)
data["prompts"].append({
    "index": int(idx),
    "prompt": prompt,
    "status": status,
    "completion": completion,
    "error": error,
    "latency_ms": int(latency),
    "trace_count": int(traces),
})
with open(out_path, "w") as f:
    json.dump(data, f, indent=2)
PY

  ok "  status=$invoke_status latency=${latency_ms}ms traces=$trace_count completion_chars=${#completion_text}"
done

# --- write human-readable markdown report -----------------------------------

python3 - "$OUT_JSON" "$OUT_MD" <<'PY'
import json, sys, datetime
in_path, out_path = sys.argv[1:]
with open(in_path) as f:
    data = json.load(f)
lines = []
lines.append(f"# Bedrock Supervisor Smoke — {data['tag']}\n")
lines.append(f"- Stack: `{data['stack_name']}` ({data['region']})")
lines.append(f"- AgentId: `{data['agent_id']}`")
lines.append(f"- AliasId: `{data['alias_id']}`")
lines.append(f"- foundationModel: `{data['foundation_model']}`")
lines.append(f"- stack LastUpdatedTime: `{data['stack_last_updated']}`")
lines.append(f"- captured: {datetime.datetime.utcnow().isoformat()}Z\n")
for p in data["prompts"]:
    lines.append(f"## Prompt {p['index']}: {p['prompt']}")
    lines.append(f"- status: `{p['status']}` — latency: {p['latency_ms']}ms — traces: {p['trace_count']}")
    if p["error"]:
        lines.append(f"- error: `{p['error']}`")
    lines.append("")
    lines.append("**Completion:**")
    lines.append("")
    completion = p["completion"] or "(empty)"
    # Block-quote each line of the completion for readability.
    for cl in completion.splitlines() or ["(empty)"]:
        lines.append(f"> {cl}")
    lines.append("")
with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")
PY

ok "wrote $OUT_JSON and $OUT_MD"

if [ "$failures" -gt 0 ]; then
  err "$failures of ${#PROMPTS[@]} prompts failed or returned empty completions"
  exit 2
fi

ok "all ${#PROMPTS[@]} prompts returned non-empty completions"
exit 0

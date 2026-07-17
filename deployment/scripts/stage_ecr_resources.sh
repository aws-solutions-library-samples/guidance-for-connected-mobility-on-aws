#!/usr/bin/env bash
#
# stage_ecr_resources.sh — stage simulation container build contexts under
# deployment/ecr/<image-name>/ per the AWS Solutions Engineering public-ECR
# convention (dir name == public repo name). Consumed by `make publish-public-ecr`
# (and, if CMS later onboards to the full AWS Solutions pipeline, by the
# PublishPublicECR CodeBuild project, which discovers dirs under deployment/ecr/).
#
# deployment/ecr/ is a BUILD-TIME staging dir — gitignored, never committed.
#
# Both images derive from services/simulation/ (shared context):
#   - cms-sim-service  -> Dockerfile
#   - cms-fwe-agent    -> Dockerfile.fwe (staged as Dockerfile)
#
# Idempotent + non-interactive. Run from anywhere.
#
# Spec: .kiro/specs/2026-06-16-cms-sim-images-codebuild-ecr/spec.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$DEPLOYMENT_DIR")"

SRC_DIR="$REPO_ROOT/services/simulation"
ECR_DIR="$DEPLOYMENT_DIR/ecr"

SIM_SERVICE_NAME="cms-sim-service"
FWE_AGENT_NAME="cms-fwe-agent"

log()  { printf '\033[0;34m[stage-ecr]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[stage-ecr]\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m[stage-ecr]\033[0m %s\n' "$*" >&2; }

# Prune a staged dir to honor services/simulation/.dockerignore (cp -R ignores
# it). Reads each pattern; skips blank lines, comments (#...) and negations
# (!...). Matches by basename via `find -name`, so it handles literal names,
# globs (e.g. *.json) and directory entries (trailing slash stripped) anywhere
# in the staged tree. Keeps .dockerignore the single source of truth.
prune_per_dockerignore() {
  local dest="$1" di="$SRC_DIR/.dockerignore" pat
  [ -f "$di" ] || return 0
  while IFS= read -r pat || [ -n "$pat" ]; do
    pat="${pat%$'\r'}"                            # strip trailing CR (CRLF safety)
    case "$pat" in ''|\#*|!*) continue ;; esac    # skip blank / comment / negation
    pat="${pat%/}"                                # dir entries: strip trailing slash
    [ -n "$pat" ] || continue
    find "$dest" -depth -name "$pat" -exec rm -rf {} + 2>/dev/null || true
  done < "$di"
}

if [ ! -d "$SRC_DIR" ]; then
  err "source context not found: $SRC_DIR"
  exit 1
fi
if [ ! -f "$SRC_DIR/Dockerfile" ]; then
  err "missing $SRC_DIR/Dockerfile"
  exit 1
fi
if [ ! -f "$SRC_DIR/Dockerfile.fwe" ]; then
  err "missing $SRC_DIR/Dockerfile.fwe"
  exit 1
fi

# Copy the shared build context into a target dir, excluding noise + the
# staging tree itself, then set the target's Dockerfile.
stage_one() {
  local name="$1" dockerfile="$2"
  local dest="$ECR_DIR/$name"
  log "staging $name (Dockerfile: $dockerfile)"
  rm -rf "$dest"
  mkdir -p "$dest"
  # -R copy of context; prune caches afterward. SRC_DIR is outside ECR_DIR so
  # there is no recursive-copy risk.
  cp -R "$SRC_DIR"/. "$dest"/
  # Honor services/simulation/.dockerignore: `cp -R` does NOT, so without this
  # the staged tree (which `make publish-public-ecr` scans) would contain local
  # dev venvs (can_env/, sim_env/), per-developer config (fwe_config/), key
  # material (*.pem/*.key/*.crt) and dev/seed scripts that the real `docker
  # build` context excludes and the published image never ships. Prune by
  # reading .dockerignore so it stays the single source of truth (no drift).
  prune_per_dockerignore "$dest"
  # Normalize the Dockerfile for this image: the dir must expose exactly one
  # canonical "Dockerfile".
  if [ "$dockerfile" != "Dockerfile" ]; then
    cp "$SRC_DIR/$dockerfile" "$dest/Dockerfile"
    rm -f "$dest/$dockerfile"
  fi
  ok "staged $dest"
}

log "repo root: $REPO_ROOT"
mkdir -p "$ECR_DIR"
stage_one "$SIM_SERVICE_NAME" "Dockerfile"
stage_one "$FWE_AGENT_NAME" "Dockerfile.fwe"
ok "done — staged: $SIM_SERVICE_NAME, $FWE_AGENT_NAME under $ECR_DIR"

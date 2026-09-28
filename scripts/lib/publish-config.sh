#!/usr/bin/env bash
# publish-config.sh — static config for publish-to-github.sh
# Sourced by publish-to-github.sh; do not execute directly.

# Sanity check: must be run from inside a git repo
_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "ERROR: publish-config.sh must be sourced from within a git repo" >&2; return 1; }

export GITHUB_REMOTE_URL="git@github.com:aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git"
export PUBLISH_BRANCH="main"
export PUBLISH_EXCLUDE_FILE="${_REPO_ROOT}/.publish-exclude"
export SECRETS_SCAN_CONFIG="${_REPO_ROOT}/.publish-secrets-scan.yml"
export MKTEMP_PREFIX="cms-publish-"

# publish_preflight — CMS-specific pre-publish check.
# Called by publish-to-github.sh if `declare -f publish_preflight` succeeds.
# Verifies that the SIM_IMAGE_VERSION constant in deployment/stacks/_sim_image_config.py
# matches the --tag being published, so the public ECR image reference is always
# in lock-step with the release tag.
#
# Backlog row: "Sim image version pin" P2 (folded in via publish-flow extract spec,
# canonical publish_preflight hook + CMS publish-config.sh override).
publish_preflight() {
  local sim_config_file="${_REPO_ROOT}/deployment/stacks/_sim_image_config.py"

  # Extract the module-level SIM_IMAGE_VERSION constant value.
  # Pattern matches both typed form (SIM_IMAGE_VERSION: str = "v0.2.6") and
  # untyped form (SIM_IMAGE_VERSION = "v0.2.6").
  local sim_version
  sim_version="$(grep -E '^SIM_IMAGE_VERSION\s*[=:]' "${sim_config_file}" \
    | grep -oE '"[^"]+"' | head -1 | tr -d '"')" || true

  if [[ -z "$sim_version" ]]; then
    echo "ERROR: publish_preflight: could not read SIM_IMAGE_VERSION from ${sim_config_file}" >&2
    echo "  Ensure deployment/stacks/_sim_image_config.py defines SIM_IMAGE_VERSION at module level." >&2
    return 1
  fi

  if [[ "$sim_version" != "$TAG" ]]; then
    # Deliberate escape hatch. There are legitimate publishes where the operator
    # knowingly keeps the sim images at an older tag (e.g. a docs-only release
    # that ships no simulation change). Without a documented override, the first
    # time this gate is inconvenient somebody deletes the function — so make the
    # override explicit, loud, and auditable instead.
    if [[ "${PUBLISH_SKIP_SIM_IMAGE_PREFLIGHT:-}" == "1" ]]; then
      echo "  [preflight] WARN SIM_IMAGE_VERSION=${sim_version} != tag ${TAG}, but" >&2
      echo "  [preflight] WARN PUBLISH_SKIP_SIM_IMAGE_PREFLIGHT=1 was set — proceeding anyway." >&2
      echo "  [preflight] WARN A fresh 'make deploy-all' off this release will pull" >&2
      echo "  [preflight] WARN cms-{sim-service,fwe-agent}:${sim_version}, NOT :${TAG}." >&2
      return 0
    fi
    echo "ERROR: publish_preflight: SIM_IMAGE_VERSION=${sim_version} does not match publish tag=${TAG}" >&2
    echo "  Bump the constant and re-run: make publish-public-ecr VERSION=${TAG} first" >&2
    echo "  then retry: bash scripts/publish-to-github.sh --tag ${TAG}" >&2
    echo "  If the older image tag is intentional for this release, re-run with:" >&2
    echo "      PUBLISH_SKIP_SIM_IMAGE_PREFLIGHT=1 bash scripts/publish-to-github.sh --tag ${TAG}" >&2
    return 1
  fi

  echo "  [preflight] SIM_IMAGE_VERSION=${sim_version} matches tag ${TAG} — OK"
}

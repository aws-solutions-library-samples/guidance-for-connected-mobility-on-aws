#!/usr/bin/env bash
# publish-to-github.sh — sanitize and force-push a tagged release to the public GitHub mirror.
#
# Usage:
#   scripts/publish-to-github.sh --tag <vX.Y.Z> [--dry-run] [--yes] [--allow-finding p:f:l] ...
#
# Requirements: git, python3 (for secret-scan.py)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"

# shellcheck source=scripts/lib/publish-config.sh
source "${SCRIPT_DIR}/lib/publish-config.sh"

# ── Logging ──────────────────────────────────────────────────────────────────
log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
info() { log "INFO  $*"; }
warn() { log "WARN  $*" >&2; }
err()  { log "ERROR $*" >&2; }
die()  { err "$*"; exit 2; }

# ── Argument parsing ──────────────────────────────────────────────────────────
TAG=""
DRY_RUN=false
YES=false
ALLOW_FINDINGS=()

usage() {
  cat <<EOF
Usage: $(basename "$0") --tag <vX.Y.Z> [--dry-run] [--yes] [--allow-finding p:f:l] ...

  --tag <vX.Y.Z>          Required. Annotated tag to publish.
  --dry-run               Stage + scan but do NOT push. Preserves staging dir.
  --yes                   Skip confirmation prompt before push.
  --allow-finding p:f:l   Suppress a specific scanner finding (repeatable).
  -h, --help              Show this help.
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)            TAG="${2:?--tag requires a value}"; shift 2 ;;
    --dry-run)        DRY_RUN=true; shift ;;
    --yes)            YES=true; shift ;;
    --allow-finding)  ALLOW_FINDINGS+=("${2:?--allow-finding requires a value}"); shift 2 ;;
    -h|--help)        usage ;;
    *)                die "Unknown option: $1" ;;
  esac
done

[[ -z "$TAG" ]] && die "Missing required --tag argument. Use --help for usage."

# ── Staging dir management ────────────────────────────────────────────────────
STAGING=""
cleanup() {
  if [[ -n "$STAGING" && -d "$STAGING" ]]; then
    if $DRY_RUN; then
      info "Dry-run: staging dir preserved at: $STAGING"
    else
      rm -rf "$STAGING"
    fi
  fi
}
trap cleanup EXIT

# ── Validation ────────────────────────────────────────────────────────────────
info "Validating pre-conditions for tag: $TAG"

# Tag must exist locally
git -C "$REPO_ROOT" rev-parse "refs/tags/${TAG}" >/dev/null 2>&1 \
  || die "Tag '${TAG}' does not exist locally. Create it first."

# Working tree must be clean (tracked files only — untracked files don't affect publish)
DIRTY="$(git -C "$REPO_ROOT" status --porcelain | grep -v '^??' || true)"
[[ -z "$DIRTY" ]] || die "Working tree has uncommitted changes. Commit or stash changes before publishing."

# 'github' remote must exist
git -C "$REPO_ROOT" remote get-url github >/dev/null 2>&1 \
  || die "Remote 'github' is not configured. Add it first."

# ── Stage ─────────────────────────────────────────────────────────────────────
STAGING="$(mktemp -d -t "cms-publish-${TAG}-XXXXXX")"
info "Cloning to staging dir: $STAGING"
git clone --no-local "$REPO_ROOT" "$STAGING"
git -C "$STAGING" checkout "$TAG"

# Count files before strip
FILES_BEFORE="$(find "$STAGING" -not -path '*/.git/*' -type f | wc -l | tr -d ' ')"

# ── Strip (apply .publish-exclude) ───────────────────────────────────────────
if [[ -f "$PUBLISH_EXCLUDE_FILE" ]]; then
  info "Stripping paths per $PUBLISH_EXCLUDE_FILE"
  while IFS= read -r pattern || [[ -n "$pattern" ]]; do
    # Skip comments and blank lines
    [[ "$pattern" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${pattern// }" ]] && continue

    # Strip trailing slash (we handle directories and files identically)
    pattern_clean="${pattern%/}"
    # Strip leading slash (root-anchored is the same as relative for our purposes
    # since we always prefix with $STAGING/)
    pattern_clean="${pattern_clean#/}"

    # Three resolution strategies, applied in order (any may hit zero matches):
    # 1. Direct path under staging (handles `clients/ios`, `.kiro`, exact files)
    # 2. Glob expansion under staging (handles `docs/customer-*.md`)
    # 3. find -name (handles patterns like `*.swp`, `.DS_Store` that should match anywhere)

    # Strategy 1: direct path
    if [[ -e "$STAGING/$pattern_clean" ]]; then
      rm -rf "$STAGING/$pattern_clean"
    fi

    # Strategy 2: glob expansion (only if pattern contains a wildcard)
    if [[ "$pattern_clean" == *"*"* ]] || [[ "$pattern_clean" == *"?"* ]]; then
      # Use a subshell with nullglob so an unmatched glob doesn't error
      (
        shopt -s nullglob 2>/dev/null || setopt null_glob 2>/dev/null
        # shellcheck disable=SC2086
        for match in $STAGING/$pattern_clean; do
          [[ -e "$match" ]] && rm -rf "$match"
        done
      )
    fi

    # Strategy 3: -name match (for basename patterns like *.swp, .DS_Store, .vscode)
    # Only apply if pattern has no slashes (otherwise direct path / glob covered it)
    if [[ "$pattern_clean" != */* ]]; then
      find "$STAGING" -name "$pattern_clean" -not -path '*/.git/*' -prune \
        -exec rm -rf {} + 2>/dev/null || true
    fi
  done < "$PUBLISH_EXCLUDE_FILE"
else
  warn ".publish-exclude not found at $PUBLISH_EXCLUDE_FILE — skipping strip step"
fi

FILES_AFTER="$(find "$STAGING" -not -path '*/.git/*' -type f | wc -l | tr -d ' ')"
info "Files: before strip=$FILES_BEFORE, after strip=$FILES_AFTER (removed $((FILES_BEFORE - FILES_AFTER)))"

# ── Scan ──────────────────────────────────────────────────────────────────────
SCAN_SCRIPT="${SCRIPT_DIR}/lib/secret-scan.py"
[[ -f "$SCAN_SCRIPT" ]] || { $DRY_RUN && warn "Secret scanner not found: $SCAN_SCRIPT (skipping in dry-run)"; } \
  || die "Secret scanner not found: $SCAN_SCRIPT"
[[ -f "$SECRETS_SCAN_CONFIG" ]] || { $DRY_RUN && warn "Scan config not found: $SECRETS_SCAN_CONFIG (skipping in dry-run)"; } \
  || die "Scan config not found: $SECRETS_SCAN_CONFIG"

SCAN_RESULT="skipped (scanner or config not found)"
if [[ -f "$SCAN_SCRIPT" && -f "$SECRETS_SCAN_CONFIG" ]]; then
  info "Running secret scanner against staged tree..."
  SCAN_ARGS=(python3 "$SCAN_SCRIPT" --config "$SECRETS_SCAN_CONFIG" --root "$STAGING" --pretty)
  for af in "${ALLOW_FINDINGS[@]+"${ALLOW_FINDINGS[@]}"}"; do
    SCAN_ARGS+=(--allow-finding "$af")
  done

  SCAN_OUTPUT="$("${SCAN_ARGS[@]}" 2>&1)" && SCAN_EXIT=0 || SCAN_EXIT=$?
  # Print scanner output (never contains secret values per scanner contract)
  echo "$SCAN_OUTPUT"

  if [[ $SCAN_EXIT -ne 0 ]]; then
    err "Secret scanner found critical findings. Aborting."
    err "Use --allow-finding pattern_name:file:line to suppress known false positives."
    exit 1
  fi
  SCAN_RESULT="clean"
  info "Scanner: clean"
fi

# ── Dry-run exit ──────────────────────────────────────────────────────────────
if $DRY_RUN; then
  info "=== DRY-RUN SUMMARY ==="
  info "  Tag:           $TAG"
  info "  Files before:  $FILES_BEFORE"
  info "  Files after:   $FILES_AFTER"
  info "  Scanner:       $SCAN_RESULT"
  info "  Staging dir:   $STAGING (preserved for review)"
  info "Dry-run complete. No changes pushed."
  exit 0
fi

# ── Confirmation ──────────────────────────────────────────────────────────────
if ! $YES; then
  echo ""
  warn "About to force-push tag '$TAG' to: $GITHUB_REMOTE_URL"
  warn "Branch: $PUBLISH_BRANCH  |  Files: $FILES_AFTER"
  printf 'Proceed? [y/N] '
  read -r CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || die "Aborted by user."
fi

# ── Verify remote URL before push ─────────────────────────────────────────────
# Safety: confirm the configured URL still points at aws-solutions-library-samples
if [[ "$GITHUB_REMOTE_URL" != *"aws-solutions-library-samples"* ]]; then
  die "GITHUB_REMOTE_URL does not point at aws-solutions-library-samples — aborting to prevent accidental push."
fi

# ── Rebuild git history (squash to single commit) ────────────────────────────
info "Rebuilding git history in staging clone..."
TAG_MESSAGE="$(git -C "$REPO_ROOT" tag -l --format='%(contents)' "$TAG")"
[[ -z "$TAG_MESSAGE" ]] && TAG_MESSAGE="Release $TAG"

rm -rf "${STAGING}/.git"
git -C "$STAGING" init
# 2026-06-11 (v0.2.1): use `git add -f` to bypass .gitignore. The source repo's
# .gitignore has broad patterns (e.g. `*credentials*`) that match files we DO
# want shipped (e.g. modules/cms_ui/source/frontend/src/api/credentials-provider.ts,
# pre-build-cleanup.js, signal-catalog.json). Source-of-truth for what to ship
# is the staging tree (post .publish-exclude strip + scanner), NOT .gitignore.
# Without -f, ~16 user-facing files get silently dropped from the public mirror,
# breaking UI build for external users (v0.2.0 publish-readiness defect).
git -C "$STAGING" add -f .
git -C "$STAGING" \
  -c user.name="$(git -C "$REPO_ROOT" config user.name)" \
  -c user.email="$(git -C "$REPO_ROOT" config user.email)" \
  commit -m "$TAG_MESSAGE"

STAGING_COMMIT="$(git -C "$STAGING" rev-parse HEAD)"
info "Staging commit: $STAGING_COMMIT"

# ── Configure remote in staging clone ────────────────────────────────────────
# The user's main checkout has push URL set to 'no-direct-push' as a safety guard.
# We operate entirely in the staging clone, which has its own .git config.
#
# Push-URL resolution priority (highest first):
#   1. $GITHUB_PUSH_URL_OVERRIDE — explicit override (e.g., GitLab CI sets this
#                                  to https://${GITHUB_TOKEN}@github.com/<org>/<repo>.git
#                                  so we don't need github.com SSH known_hosts in
#                                  the runner).
#   2. The user's main repo's `github` remote URL (if it isn't 'no-direct-push').
#   3. The constant from publish-config.sh ($GITHUB_REMOTE_URL).
#
# In a fresh CI runner without ~/.ssh/known_hosts, SSH-based pushes to
# git@github.com fail with "Host key verification failed". The CI before_script
# is expected to configure HTTPS-with-token auth and set GITHUB_PUSH_URL_OVERRIDE.
EFFECTIVE_PUSH_URL="${GITHUB_PUSH_URL_OVERRIDE:-}"
if [[ -z "$EFFECTIVE_PUSH_URL" ]]; then
  USER_REMOTE="$(git -C "$REPO_ROOT" remote get-url --push github 2>/dev/null || echo "")"
  if [[ -n "$USER_REMOTE" && "$USER_REMOTE" != "no-direct-push" ]]; then
    EFFECTIVE_PUSH_URL="$USER_REMOTE"
  else
    EFFECTIVE_PUSH_URL="$GITHUB_REMOTE_URL"
  fi
fi

# Safety: even with override, the URL must point at the configured org.
if [[ "$EFFECTIVE_PUSH_URL" != *"aws-solutions-library-samples"* ]]; then
  die "Effective push URL does not point at aws-solutions-library-samples — aborting to prevent accidental push. URL: $EFFECTIVE_PUSH_URL"
fi

# Mask token in logs if URL contains user:pass
LOG_PUSH_URL="$(echo "$EFFECTIVE_PUSH_URL" | sed -E 's|https://[^@]+@|https://<redacted>@|')"

git -C "$STAGING" remote add github "$EFFECTIVE_PUSH_URL"

# ── Push ──────────────────────────────────────────────────────────────────────
info "Force-pushing $PUBLISH_BRANCH to $LOG_PUSH_URL ..."
git -C "$STAGING" push --force github "HEAD:${PUBLISH_BRANCH}"

info "Pushing tag $TAG ..."
# Create the tag in the staging clone so we can push it
git -C "$STAGING" \
  -c user.name="$(git -C "$REPO_ROOT" config user.name)" \
  -c user.email="$(git -C "$REPO_ROOT" config user.email)" \
  tag "$TAG"
git -C "$STAGING" push github "$TAG"

GITHUB_SHA="$(git -C "$STAGING" rev-parse HEAD)"
info "Published commit SHA: $GITHUB_SHA"

# ── Audit log ─────────────────────────────────────────────────────────────────
HISTORY_DIR="${REPO_ROOT}/.publish-history"
mkdir -p "$HISTORY_DIR"
LOG_FILE="${HISTORY_DIR}/${TAG}.log"

LINES_TOTAL="$(find "$STAGING" -not -path '*/.git/*' -type f \
  -exec wc -l {} + 2>/dev/null | tail -1 | awk '{print $1}')"

cat > "$LOG_FILE" <<EOF
timestamp:  $(date -u '+%Y-%m-%dT%H:%M:%SZ')
tag:        $TAG
committer:  $(git -C "$REPO_ROOT" config user.name) <$(git -C "$REPO_ROOT" config user.email)>
remote:     $GITHUB_REMOTE_URL
branch:     $PUBLISH_BRANCH
files:      $FILES_AFTER
lines:      ${LINES_TOTAL:-unknown}
commit_sha: $GITHUB_SHA
EOF

info "Audit log written: $LOG_FILE"
info "Publish complete: $TAG → $GITHUB_REMOTE_URL ($PUBLISH_BRANCH)"

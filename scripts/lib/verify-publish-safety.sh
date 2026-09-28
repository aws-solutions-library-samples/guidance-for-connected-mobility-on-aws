#!/usr/bin/env bash
# verify-publish-safety.sh — would publishing HEAD right now leak anything?
#
# WHY THIS EXISTS
# ---------------
# The secret scanner previously ran in exactly one place: `publish-to-github.sh`, at
# publish time. That is too late to be a guard — by then the leak is already committed,
# reviewed and tagged, and the only options are abort-and-scramble or ship it.
#
# Worse, the scanner cannot see files listed in `scan_exclude`, and such a file STILL
# SHIPS unless it is also in `.publish-exclude`. That gap put an internal AWS account ID
# on the live public mirror twice — once via `.gitignore` (in patterns whose purpose was
# to prevent committing files named after that account) and once via
# `modules/flink/deploy.sh`. Both had been identified in
# `docs/SECURITY-AUDIT-FINDINGS.md` and marked "MUST be stripped"; neither failed a
# build, because nothing executed that finding.
#
# So this runs BOTH halves, on every push:
#
#   1. The behavioural scan — build the archive that a publish would produce, apply
#      `.publish-exclude`, and scan the result. Answers "would the publish abort?"
#   2. The invariant tests — assert that no scan-excluded file is also published while
#      carrying forbidden content. Answers "is anything HIDDEN from that scan?"
#
# Neither alone is sufficient. (1) is blind to `scan_exclude` by construction; (2) does
# not prove the scanner itself passes.
#
# Usage:
#   bash scripts/lib/verify-publish-safety.sh                 # scan committed HEAD
#   bash scripts/lib/verify-publish-safety.sh --working-tree  # scan uncommitted work
#   REF=v0.2.9 bash scripts/lib/verify-publish-safety.sh      # scan a tag
#
# `--working-tree` exists because the default (HEAD) validates the COMMITTED tree, which
# is right for a CI gate and awkward the moment you are actually fixing a leak: you
# cannot confirm the fix until after committing it. That bites in practice — during the
# 2026-08-04 cleanup it forced hand-built staging trees twice. Working-tree mode copies
# tracked files as they exist on disk, so a fix can be verified before it is committed.
#
# Exits non-zero on any finding. Intended for CI (default mode) and for local pre-commit
# checks (--working-tree).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

REF="${REF:-HEAD}"
MODE="committed"
for arg in "$@"; do
  case "$arg" in
    --working-tree|-w) MODE="working-tree" ;;
    -h|--help) sed -n '1,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

CONFIG=".publish-secrets-scan.yml"
SCANNER="scripts/lib/secret-scan.py"
PYTHON="${PYTHON:-python3}"

info() { printf '\033[0;34m[info]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[ok]\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m[fail]\033[0m %s\n' "$*" >&2; }

[[ -f "$CONFIG" ]]  || { err "missing $CONFIG"; exit 1; }
[[ -f "$SCANNER" ]] || { err "missing $SCANNER"; exit 1; }

STAGING="$(mktemp -d)"
cleanup() { rm -rf "$STAGING"; [ -n "${SCAN_CONFIG:-}" ] && [ "$SCAN_CONFIG" != "$CONFIG" ] && rm -f "$SCAN_CONFIG"; return 0; }
trap cleanup EXIT

# ── 1. Behavioural scan of the tree a publish would actually produce ──────────
if [ "$MODE" = "working-tree" ]; then
  info "staging tracked files from the WORKING TREE (uncommitted edits included)"
  # Copy the tracked set as it exists on disk. Deliberately tracked-only: untracked
  # files do not publish, so including them would invent findings that a real publish
  # could never produce.
  git ls-files -z | while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue          # skip deleted-but-tracked paths
    mkdir -p "$STAGING/$(dirname "$f")"
    cp -p "$f" "$STAGING/$f"
  done
  info "  staged $(git ls-files | wc -l | tr -d ' ') tracked path(s)"
else
  info "building publish archive from $REF"
  git archive --format=tar "$REF" | tar -x -C "$STAGING"
fi

info "applying .publish-exclude ($MODE)"
# Python for glob correctness — bash pathname expansion does not match
# .gitignore-style patterns closely enough, and a pattern that silently fails to
# match would make this check pass while the real publish ships the file.
"$PYTHON" - "$STAGING" <<'PY'
import pathlib, shutil, sys
root = pathlib.Path(sys.argv[1])
manifest = root / '.publish-exclude'
if not manifest.exists():
    print("  no .publish-exclude in archive; nothing stripped")
    raise SystemExit(0)
pats = [
    l.strip() for l in manifest.read_text().splitlines()
    if l.strip() and not l.strip().startswith('#') and not l.strip().startswith('!')
]
n = 0
for p in pats:
    p = p.strip('/')
    for m in list(root.glob(p)) + list(root.glob('**/' + p)):
        if not m.exists():
            continue
        if m.is_dir():
            shutil.rmtree(m, ignore_errors=True)
        else:
            m.unlink(missing_ok=True)
        n += 1
print(f"  stripped {n} path(s)")
PY

# The config must come from the SAME source as the content being scanned. Mixing them
# produces nonsense in both directions: scanning HEAD's content against a working-tree
# config that has since removed exclusions reports findings a real publish of HEAD would
# never hit (observed 2026-08-04 — 16 phantom findings), and the reverse can hide a real
# one. The config is itself `.publish-exclude`d, so it is stripped from the staging tree
# and has to be materialised separately.
if [ "$MODE" = "working-tree" ]; then
  SCAN_CONFIG="$CONFIG"
else
  SCAN_CONFIG="$(mktemp)"
  if ! git show "${REF}:${CONFIG}" > "$SCAN_CONFIG" 2>/dev/null; then
    err "cannot read $CONFIG at $REF"
    exit 1
  fi
  info "using $CONFIG as of $REF (not the working tree)"
fi

info "scanning the stripped archive"
if "$PYTHON" "$SCANNER" --config "$SCAN_CONFIG" --root "$STAGING" --pretty; then
  ok "scanner clean on the publishable tree"
else
  err "scanner found findings in content that WOULD be published."
  err "Fix the content. Do NOT silence it by adding the file to scan_exclude —"
  err "a scan-excluded file still ships, which is how two account IDs reached the mirror."
  exit 1
fi

# ── 2. Invariant tests — catch what the scan above cannot see ─────────────────
info "running publish-safety invariant tests"
if ! "$PYTHON" -m pytest -q \
     scripts/lib/test_publish_scan_exclude_invariant.py \
     scripts/lib/test_secret_scan.py; then
  err "publish-safety invariants failed — something is excluded from scanning yet published."
  exit 1
fi

ok "publish safety verified ($MODE${REF:+, ref=$REF})"

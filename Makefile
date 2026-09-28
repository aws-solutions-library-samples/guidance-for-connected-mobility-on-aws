
# ── Publish safety ───────────────────────────────────────────────────────────
# Runs the SAME check as the GitLab job `verify-publish-archive-clean`: builds
# the archive a publish would produce, applies `.publish-exclude`, and scans
# what survives — then runs the publish-safety invariant tests.
#
# Run this before pushing anything that adds prose to a shipped directory.
#
# Added 2026-08-11 after that job failed on commit b89e7de4. The cause was a
# customer canary written into a docstring in `deployment/aspects/`, which is
# not `.publish-exclude`d — the same leak class that had been fixed hours
# earlier in the same file. The script already existed and takes ~4 seconds;
# what was missing was any reason to remember it exists. A check that only
# runs in CI is a check you learn about after pushing.
#
# Note that this reads COMMITTED content (`git archive HEAD`), so commit first
# — uncommitted edits are invisible to it, which is a property of what publish
# actually ships, not a limitation to work around.
.PHONY: verify-publish
verify-publish:
	bash scripts/lib/verify-publish-safety.sh

# ── Git hooks ────────────────────────────────────────────────────────────────
# Points core.hooksPath at .githooks, which chains git-defender and then scans
# the STAGED files for publish-scanner findings (~1-2s vs ~87s for the whole
# tree). Repo-local: does not affect any other repository.
#
# .githooks/pre-commit runs git-defender FIRST and fails closed if it cannot —
# taking over core.hooksPath means owning that responsibility. Undo with:
#   git config --unset core.hooksPath
.PHONY: install-hooks
install-hooks:
	@chmod +x .githooks/* scripts/lib/scan-staged-publish.sh
	@git config core.hooksPath .githooks
	@echo "core.hooksPath -> .githooks (git-defender chained, staged publish scan enabled)"
	@echo "verify with: make verify-hooks"

.PHONY: verify-hooks
verify-hooks:
	@printf 'core.hooksPath      : %s\n' "$$(git config --get core.hooksPath || echo '<unset — repo-local hook NOT active>')"
	@printf 'pre-commit hook     : %s\n' "$$(test -x .githooks/pre-commit && echo 'present, executable' || echo 'MISSING or not executable')"
	@printf 'staged scan script  : %s\n' "$$(test -x scripts/lib/scan-staged-publish.sh && echo 'present, executable' || echo 'MISSING or not executable')"
	@printf 'git-defender binary : %s\n' "$$(test -x /usr/local/amazon/var/git-defender/hooks/pre-commit && echo 'present, will be chained' || echo 'MISSING — pre-commit will fail closed')"

# ── iOS UAT monitor ──────────────────────────────────────────────────────────
# Tails the booted simulator log stream and auto-writes issue reports +
# tasks to issues/ and .kiro/specs/uat-bugs/tasks.md when errors are detected.
# Requires a booted iOS simulator. Stop with Ctrl-C.
.PHONY: ios-monitor
ios-monitor:
	python3 scripts/ios-monitor.py

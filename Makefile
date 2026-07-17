
# ── iOS UAT monitor ──────────────────────────────────────────────────────────
# Tails the booted simulator log stream and auto-writes issue reports +
# tasks to issues/ and .kiro/specs/uat-bugs/tasks.md when errors are detected.
# Requires a booted iOS simulator. Stop with Ctrl-C.
.PHONY: ios-monitor
ios-monitor:
	python3 scripts/ios-monitor.py

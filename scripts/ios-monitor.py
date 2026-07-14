#!/usr/bin/env python3
"""
iOS UAT monitor — tails the booted simulator log stream, detects errors,
and writes issue reports + tasks to the CMS repo automatically.

Hardening (2026-05-28):
  - BENIGN_PATTERNS: simulator/system noise that NEVER produces an issue.
  - _signature(): stable error signature (timestamps + IDs + UUIDs stripped).
  - seen_signatures: in-memory per-session dedup; second occurrence updates
    the duplicate count on the existing issue instead of opening a new dir.
  - _redact(): scrubs JWTs, ASIA*/AKIA* access key ids, X-Amz-Security-Token,
    X-Amz-Signature, and the 'michelin' tenant canary BEFORE writing report.md.
    Closes the privacy hole that caused issue
    2026-05-28-ios-monitor-dedupe-and-suppress.

Usage:
    python3 scripts/ios-monitor.py

Stop with Ctrl-C. Issues are written to:
    issues/YYYY-MM-DD-ios-uat-<slug>/report.md
    .kiro/specs/uat-bugs/tasks.md  (appended)

Requires a booted iOS simulator with VSACompanion installed.
"""
from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
ISSUES_DIR = REPO_ROOT / "issues"
TASKS_FILE = REPO_ROOT / ".kiro" / "specs" / "uat-bugs" / "tasks.md"

# Log lines that indicate an error worth capturing.
ERROR_PATTERNS = [
    re.compile(r"\bfatalError\b"),
    re.compile(r"\bpreconditionFailure\b"),
    re.compile(r"\bCrash\b", re.IGNORECASE),
    re.compile(r"\bException\b"),
    re.compile(r"threw:"),
    re.compile(r"HTTP [45]\d\d"),
    re.compile(r"\[error\]", re.IGNORECASE),
    re.compile(r"❌"),
    re.compile(r"error occurred", re.IGNORECASE),
    re.compile(r"failed to", re.IGNORECASE),
    re.compile(r"ResourceNotFoundException"),
    re.compile(r"AccessDeniedException"),
    re.compile(r"WebSocket.*not connected", re.IGNORECASE),
    re.compile(r"handshake failed", re.IGNORECASE),
    re.compile(r"URLError"),
    re.compile(r"NSError"),
]

# Lines matching ANY of these are simulator/system subsystem noise — drop the
# entire incident silently. Conservative — only patterns OBSERVED to be benign
# in real UAT sessions. Keep this list visible so reviewers can audit.
BENIGN_PATTERNS = [
    # CoreAudio AMCP HALC plumbing in simulator. Always failures, never affect app.
    re.compile(r"HALC_ProxyObjectMap.*failed to create the local object"),
    re.compile(r"HALC_ShellDevice.*couldn't find the control object"),
    re.compile(r"HALC_ShellObject.*there is no proxy object"),
    re.compile(r"HALC_ProxySystem.*got an error from the server"),
    # FrontBoard processworkspace / common — simulator chrome.
    re.compile(r"FrontBoardServices.*processworkspace"),
    re.compile(r"FrontBoardServices.*common"),
    re.compile(r"FrontBoard.*com\.apple\.frontboard"),
    # libxpc benign churn — invalidations on cancelled connections.
    re.compile(r"libxpc.*invalidated because the current process cancelled"),
    # ExtensionFoundation discovery is informational, not an error.
    re.compile(r"com\.apple\.extensionkit:NSExtension.*discovered extensions"),
    # RunningBoard assertion bookkeeping.
    re.compile(r"RunningBoard.*Acquiring assertion"),
    re.compile(r"RunningBoard.*Invalidating assertion"),
    re.compile(r"RunningBoard.*Attempting to rename power assertion"),
    # Audio plugin manager — informational, not an error.
    re.compile(r"HALPlugInManagement.*loading in-process plug-ins"),
    re.compile(r"AddInstanceForFactory.*No factory registered"),
    # CFNetwork / Network framework verbose internal logging.
    # These fire on every connection attempt and are not app errors.
    re.compile(r"\(CFNetwork\)"),
    re.compile(r"\(Network\)"),
    re.compile(r"nw_connection_"),
    re.compile(r"nw_endpoint_"),
    re.compile(r"com\.apple\.CFNetwork"),
    re.compile(r"com\.apple\.network"),
    # FrontBoard / FrontBoardServices — simulator window management.
    re.compile(r"\(FrontBoard"),
    re.compile(r"com\.apple\.frontboard"),
    # Security framework — key operations, not app errors.
    re.compile(r"\(Security\)"),
    re.compile(r"SecKeyCopy"),
]

# Only capture lines from our app (filter noise from system processes).
APP_FILTER = re.compile(r"VSACompanion|com\.aws\.vsa")

# Rolling window of recent lines for context (kept in memory, not written).
CONTEXT_WINDOW = 30  # lines before the error
DEBOUNCE_LINES = 50  # lines of quiet after an error before closing the incident

# ── Privacy redaction ────────────────────────────────────────────────────────
# Applied to error_lines and context_lines BEFORE writing report.md.
# Patterns mirror /tmp/redact-jwts.py so a re-scan is a no-op.

_REDACT_PATTERNS = [
    # JWTs: header.payload.signature (3 base64url segments starting with eyJ).
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<redacted-jwt>"),
    # Customer canary in URL Tenant-Id headers.
    (re.compile(r"michelin", re.IGNORECASE), "<redacted-tenant>"),
    # AWS STS temporary access key id.
    (re.compile(r"ASIA[A-Z0-9]{16}"), "<redacted-aws-key-id>"),
    # AWS long-term access key id.
    (re.compile(r"AKIA[A-Z0-9]{16}"), "<redacted-aws-key-id>"),
    # SigV4 presigned URL secrets.
    (re.compile(r"X-Amz-Security-Token=[^&\s,\]]+"), "X-Amz-Security-Token=<redacted-token>"),
    (re.compile(r"X-Amz-Signature=[a-fA-F0-9]+"), "X-Amz-Signature=<redacted-sig>"),
]


def _redact(text: str) -> str:
    """Scrub JWTs, AWS temp creds, and the customer tenant canary from ``text``."""
    for pat, repl in _REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


# ── Signature normalization ───────────────────────────────────────────────────
# A stable signature lets us treat 41 captures of the same root cause as one
# incident. The signature is computed on the FIRST error line of an incident,
# stripped of identifiers that vary per-occurrence.

_SIG_STRIP_PATTERNS = [
    # syslog timestamp (yyyy-mm-dd HH:MM:SS.ssssss±0000)
    re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+[+-]\d{4}"),
    # ISO-8601 UTC
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"),
    # Process name + PID: VSACompanion[51780]:
    re.compile(r"VSACompanion\[\d+\]:"),
    # localhost word
    re.compile(r"\blocalhost\b"),
    # NSURLSessionTask <UUID>.<N>
    re.compile(r"Task <[A-F0-9-]+>\.<\d+>"),
    # Connection ids C8.1, C8.1.1, [C8] event etc.
    re.compile(r"\[C\d+(?:\.\d+)*[^\]]*\]"),
    # Bare UUIDs
    re.compile(r"[A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}", re.IGNORECASE),
    # Amazon AWS request id headers
    re.compile(r"x-amz-request-id:\s*\S+", re.IGNORECASE),
    # Address pointers like @0x110248140
    re.compile(r"0x[A-F0-9]{6,}", re.IGNORECASE),
    # IPs and ports
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b"),
    # Long hex digests (16+ chars)
    re.compile(r"\b[A-F0-9]{16,}\b", re.IGNORECASE),
]


def _signature(line: str) -> str:
    """Stable per-incident signature: first error line with variant ids removed.

    Two captures of the same root cause produce the same signature; two
    different bugs produce different signatures.
    """
    sig = line
    for pat in _SIG_STRIP_PATTERNS:
        sig = pat.sub("", sig)
    # Collapse whitespace.
    sig = re.sub(r"\s+", " ", sig).strip()
    # Cap length so wildly long log lines don't blow up the dedup map key.
    return sig[:160]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_from_line(line: str) -> str:
    """Derive a short kebab-case slug from the first error line."""
    msg = re.sub(r"^\S+\s+\S+\s+\S+\s+\S+\s+", "", line).strip()
    msg = re.sub(r"[^a-zA-Z0-9 ]", " ", msg)
    words = msg.lower().split()[:5]
    return "-".join(w for w in words if w) or "unknown-error"


def _next_issue_dir(slug: str) -> Path:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = ISSUES_DIR / f"{date}-ios-uat-{slug}"
    if not base.exists():
        return base
    i = 2
    while (candidate := ISSUES_DIR / f"{date}-ios-uat-{slug}-{i}").exists():
        i += 1
    return candidate


def _is_benign(error_lines: list[str]) -> bool:
    """True if the FIRST error line matches a known benign noise pattern."""
    if not error_lines:
        return True
    first = error_lines[0]
    return any(p.search(first) for p in BENIGN_PATTERNS)


def _bump_duplicate_count(issue_dir: Path) -> None:
    """Append a ``Duplicate Captures`` counter to an existing report.md.

    Idempotent in the sense that re-running the same monitor over the same
    log replay won't duplicate issue dirs (it'll only bump the counter).
    """
    report = issue_dir / "report.md"
    if not report.exists():
        return
    text = report.read_text(encoding="utf-8")
    marker = "## Duplicate Captures"
    if marker not in text:
        text += f"\n\n{marker}\n- count: 2 (suppressed by ios-monitor.py per-session dedup)\n"
        report.write_text(text, encoding="utf-8")
        return
    # Increment the count on the existing line.
    new_text, n = re.subn(
        r"(- count: )(\d+)( \(suppressed[^)]*\))",
        lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}",
        text,
    )
    if n:
        report.write_text(new_text, encoding="utf-8")


def _write_report(issue_dir: Path, error_lines: list[str], context_lines: list[str]) -> None:
    issue_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first_error = next((l for l in error_lines if l.strip()), "unknown")
    # Redact BEFORE writing — closes the JWT/STS-creds privacy hole that
    # caused the 2026-05-28 incident.
    redacted_errors = [_redact(l) for l in error_lines]
    redacted_context = [_redact(l) for l in context_lines]
    redacted_first = _redact(first_error)

    report = textwrap.dedent(f"""\
        # Issue: iOS UAT — {redacted_first[:80]}

        ## Summary
        Error detected by ios-monitor.py during UAT at {ts}.

        ## Impact
        iOS app (VSACompanion) — severity unknown until investigated.

        ## Reproduction
        Detected automatically. Exact user action unknown — check log context below.

        ## Investigation

        ### Context (lines before error)
        ```
        {chr(10).join(redacted_context[-CONTEXT_WINDOW:])}
        ```

        ### Error lines
        ```
        {chr(10).join(redacted_errors)}
        ```
    """)
    (issue_dir / "report.md").write_text(report)
    print(f"  📄 report.md → {issue_dir.relative_to(REPO_ROOT)}")


def _append_task(issue_dir: Path, slug: str) -> None:
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    rel = issue_dir.relative_to(REPO_ROOT)

    if not TASKS_FILE.exists():
        TASKS_FILE.write_text(textwrap.dedent("""\
            # Tasks: iOS UAT Bugs

            Auto-generated by ios-monitor.py. Each task corresponds to an issue
            report in `issues/`. Investigate, fix, then mark `[x]`.

        """))

    existing = TASKS_FILE.read_text()
    if str(rel) in existing:
        print(f"  ⏭️  task already exists for {rel} — skipping")
        return

    task_line = (
        f"- [ ] Investigate and fix: `{slug}` | `{rel}`\n"
        f"  - **Accept**: bug reproduced, root cause identified, fix applied\n"
        f"  - **Verify**: `xcodebuild` clean build passes; issue not reproducible\n\n"
    )
    with TASKS_FILE.open("a") as f:
        f.write(task_line)
    print(f"  📋 task appended → {TASKS_FILE.relative_to(REPO_ROOT)}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    print("🔍 iOS UAT monitor started — watching booted simulator for VSACompanion errors")
    print(f"   Issues → {ISSUES_DIR.relative_to(REPO_ROOT)}/")
    print(f"   Tasks  → {TASKS_FILE.relative_to(REPO_ROOT)}")
    print(f"   Benign-noise suppressors: {len(BENIGN_PATTERNS)} patterns active")
    print("   Stop with Ctrl-C\n")

    cmd = [
        "xcrun", "simctl", "spawn", "booted",
        "log", "stream", "--style", "syslog",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        sys.exit("❌ xcrun not found — run this on macOS with Xcode installed")

    recent: deque[str] = deque(maxlen=CONTEXT_WINDOW)
    incident_lines: list[str] = []
    quiet_count = 0
    incident_slug = ""
    seen_signatures: dict[str, Path] = {}
    suppressed_benign = 0
    suppressed_dupes = 0

    def _close_incident() -> None:
        """Drop, dedup, or write — based on benign/seen-signature gates."""
        nonlocal suppressed_benign, suppressed_dupes
        if not incident_lines:
            return
        if _is_benign(incident_lines):
            suppressed_benign += 1
            print(f"  🤫 benign-noise suppressed (total: {suppressed_benign})")
            return
        sig = _signature(incident_lines[0])
        prior = seen_signatures.get(sig)
        if prior is not None:
            suppressed_dupes += 1
            _bump_duplicate_count(prior)
            print(f"  🔁 duplicate of {prior.name} suppressed (total: {suppressed_dupes})")
            return
        issue_dir = _next_issue_dir(incident_slug)
        _write_report(issue_dir, incident_lines, list(recent))
        _append_task(issue_dir, incident_slug)
        seen_signatures[sig] = issue_dir

    try:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            line = raw_line.rstrip()

            if not APP_FILTER.search(line):
                continue

            is_error = any(p.search(line) for p in ERROR_PATTERNS)

            if is_error:
                if not incident_lines:
                    incident_slug = _slug_from_line(line)
                    print(f"\n🚨 Error detected: {line[:120]}")
                incident_lines.append(line)
                quiet_count = 0
            elif incident_lines:
                incident_lines.append(line)
                quiet_count += 1
                if quiet_count >= DEBOUNCE_LINES:
                    _close_incident()
                    incident_lines = []
                    quiet_count = 0
                    incident_slug = ""
            else:
                recent.append(line)

    except KeyboardInterrupt:
        if incident_lines:
            _close_incident()
        print(f"\n👋 Monitor stopped — suppressed {suppressed_benign} benign, {suppressed_dupes} duplicates")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()

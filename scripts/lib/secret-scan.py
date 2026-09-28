#!/usr/bin/env python3
"""Secret scanner: walks a directory tree and reports forbidden patterns/strings."""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys

import yaml

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
BINARY_CHECK_BYTES = 8192

# Tokenizer for digest-based matching.  We extract candidate tokens and
# sha256-hash each one to compare against the digest set.  This means digest
# canaries only catch secrets that appear as a contiguous token of >=16
# characters in the set [A-Za-z0-9+/=_-] — a real tradeoff versus plaintext
# substring matching.  The reason plaintext `forbidden_strings` entries remain
# the right choice for short, low-entropy codenames (e.g. a 3-char customer
# abbreviation, deliberately not named here) is exactly
# this: such strings often contain characters outside this charset, and even if
# they don't, a 3-8 char token produces too many false positive sha256 collisions
# with other tokens to be useful.
_DIGEST_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{16,}")

# Shape validation for `forbidden_digests` config entries.
_DIGEST_ENTRY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def load_config(config_path):
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"error: config file not found: {config_path}", file=sys.stderr)
        sys.exit(2)
    except yaml.YAMLError as e:
        print(f"error: invalid YAML config: {e}", file=sys.stderr)
        sys.exit(2)
    for key in ("forbidden_patterns", "forbidden_strings", "scan_exclude"):
        if key not in cfg:
            print(f"error: config missing key: {key}", file=sys.stderr)
            sys.exit(2)
    # Compile regexes eagerly with re.IGNORECASE by default. Pattern canaries
    # (brand: `\bACME\b`, `\bGLOBEX\b`; hostname: `internal\.example\.dev`) and
    # key-prefix patterns (`AKIA`, `ASIA`) are conventionally written without
    # case discipline in source comments and identifiers; a case-sensitive scanner
    # gives a false sense of security on the ergonomic-most-likely casings
    # (`Acme`, `ACME`, `Globex`, `Internal.Example.Dev`). Defense-in-depth at the
    # scanner layer eliminates the need for every future canary author to
    # remember a `(?i)` prefix. Patterns that need case-sensitive matching
    # can opt out per-pattern with the inline `(?-i:...)` flag.
    compiled = {}
    for name, entry in cfg["forbidden_patterns"].items():
        try:
            compiled[name] = (
                re.compile(entry["regex"], re.IGNORECASE),
                entry.get("severity", "critical"),
            )
        except re.error as e:
            print(f"error: invalid regex for pattern '{name}': {e}", file=sys.stderr)
            sys.exit(2)

    # `forbidden_digests` is OPTIONAL — ADP and CVX configs will not have it.
    # Only validate shape when the key is present.
    raw_digests = cfg.get("forbidden_digests")
    if raw_digests is not None:
        if not isinstance(raw_digests, list):
            print(
                "error: forbidden_digests must be a list of sha256:<hex> strings",
                file=sys.stderr,
            )
            sys.exit(2)
        for entry in raw_digests:
            if not isinstance(entry, str) or not _DIGEST_ENTRY_RE.match(entry):
                print(
                    f"error: malformed forbidden_digests entry: {entry!r} — "
                    "must be 'sha256:<64 lowercase hex chars>'",
                    file=sys.stderr,
                )
                sys.exit(2)

    return cfg, compiled


def _glob_to_regex(pat):
    """Convert a gitignore-style glob (with **) to a compiled regex."""
    pat = pat.replace("\\", "/")
    result = ""
    i = 0
    while i < len(pat):
        if pat[i:i+3] == "**/":
            result += "(.*/)?"; i += 3
        elif pat[i:i+2] == "**":
            result += ".*"; i += 2
        elif pat[i] == "*":
            result += "[^/]*"; i += 1
        elif pat[i] == "?":
            result += "[^/]"; i += 1
        else:
            result += re.escape(pat[i]); i += 1
    return re.compile("^" + result + "$")


def matches_exclude(rel_path, exclude_patterns):
    """Return True if rel_path matches any exclude glob (supports ** for any depth)."""
    norm = rel_path.replace("\\", "/")
    for pat in exclude_patterns:
        if _glob_to_regex(pat).match(norm):
            return True
    return False


def is_binary(path):
    try:
        with open(path, "rb") as f:
            chunk = f.read(BINARY_CHECK_BYTES)
        return b"\x00" in chunk
    except OSError:
        return True


def _redact_token(token):
    """Return a redacted representation of a matched token.

    Emits the first 4 and last 4 characters with '...' in between.
    Example: 'SYNTHtestTokenABCD' → 'SYNT...ABCD'.

    This is the one finding type whose `matched` field is deliberately redacted.
    Digest canaries exist precisely so that live credential literals do NOT appear
    in any config, log, or CI output — pasting the full matched token into the
    finding would defeat that purpose.  Four-char prefix + suffix gives an
    operator enough context to locate the offending token in the file (the line
    number is also in the finding) without exposing the full secret.
    """
    if len(token) <= 8:
        # Very short tokens: emit first half and last half
        mid = len(token) // 2
        return f"{token[:mid]}...{token[mid:]}"
    return f"{token[:4]}...{token[-4:]}"


def scan_file(path, rel_path, compiled_patterns, forbidden_strings, forbidden_digest_set=None):
    findings = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                for name, (pattern, severity) in compiled_patterns.items():
                    m = pattern.search(line)
                    if m:
                        findings.append({
                            "file": rel_path,
                            "line": lineno,
                            "pattern_name": name,
                            "matched": m.group(0),
                            "severity": severity,
                        })
                for fs in forbidden_strings:
                    if isinstance(fs, str) and fs in line:
                        findings.append({
                            "file": rel_path,
                            "line": lineno,
                            "pattern_name": f"forbidden_string:{fs}",
                            "matched": fs,
                            "severity": "critical",
                        })
                # Digest-based matching.
                #
                # Tokens are hashed exactly as found — credentials are case-sensitive.
                # This deliberately differs from the re.IGNORECASE default applied to
                # `forbidden_patterns`.  A case-insensitive digest check would require
                # pre-normalising the token, but that would silently miss a credential
                # whose sha256 was registered in its canonical casing.  Operators who
                # register a digest must register the hash of the credential in its
                # exact on-disk form.
                if forbidden_digest_set:
                    for token in _DIGEST_TOKEN_RE.findall(line):
                        token_hash = "sha256:" + hashlib.sha256(token.encode()).hexdigest()
                        if token_hash in forbidden_digest_set:
                            hex_part = token_hash[7:]  # strip "sha256:"
                            findings.append({
                                "file": rel_path,
                                "line": lineno,
                                "pattern_name": f"forbidden_digest:sha256:{hex_part[:8]}...",
                                "matched": _redact_token(token),
                                "severity": "critical",
                            })
    except OSError:
        pass
    return findings


def parse_allow(allow_list):
    """Parse --allow-finding args into set of (pattern_name, file, line) tuples."""
    allowed = set()
    for item in allow_list:
        parts = item.rsplit(":", 2)
        if len(parts) == 3:
            pname, fpath, lineno = parts
            try:
                allowed.add((pname, fpath, int(lineno)))
            except ValueError:
                print(f"error: invalid --allow-finding format: {item}", file=sys.stderr)
                sys.exit(2)
        else:
            print(f"error: --allow-finding must be pattern_name:file:line, got: {item}", file=sys.stderr)
            sys.exit(2)
    return allowed


def main():
    parser = argparse.ArgumentParser(description="Scan a directory tree for secrets.")
    parser.add_argument("--config", required=True, help="Path to YAML config file")
    parser.add_argument("--root", required=True, help="Root directory to scan")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any finding (including warnings)")
    parser.add_argument("--allow-finding", action="append", default=[], metavar="pattern_name:file:line",
                        help="Suppress a specific finding (repeatable)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f"error: root directory not found: {args.root}", file=sys.stderr)
        sys.exit(2)

    cfg, compiled_patterns = load_config(args.config)
    exclude_patterns = cfg.get("scan_exclude", [])
    forbidden_strings = cfg.get("forbidden_strings", [])
    allowed = parse_allow(args.allow_finding)

    # Build the digest set (frozenset for fast O(1) lookup).
    # Empty frozenset when the key is absent — backward-compatible with configs
    # that don't define forbidden_digests.
    raw_digests = cfg.get("forbidden_digests") or []
    forbidden_digest_set = frozenset(raw_digests)

    all_findings = []

    for dirpath, dirnames, filenames in os.walk(args.root):
        # Sort for determinism
        dirnames.sort()
        for fname in sorted(filenames):
            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, args.root)

            if matches_exclude(rel_path, exclude_patterns):
                continue

            try:
                size = os.path.getsize(full_path)
            except OSError:
                continue

            if size > MAX_FILE_SIZE:
                print(f"info: skipping large file ({size} bytes): {rel_path}", file=sys.stderr)
                continue

            if is_binary(full_path):
                continue

            findings = scan_file(
                full_path, rel_path, compiled_patterns, forbidden_strings,
                forbidden_digest_set=forbidden_digest_set,
            )
            all_findings.extend(findings)

    # Apply allow-list
    filtered = [
        f for f in all_findings
        if (f["pattern_name"], f["file"], f["line"]) not in allowed
    ]

    has_critical = any(f["severity"] == "critical" for f in filtered)
    has_any = bool(filtered)

    clean = not has_critical and (not args.strict or not has_any)

    result = {"clean": clean, "findings": filtered}
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent))

    if has_critical or (args.strict and has_any):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

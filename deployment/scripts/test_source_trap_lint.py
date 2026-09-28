#!/usr/bin/env python3
"""AST lint for two traps this repo keeps re-shipping despite documenting them.

Both were found live on 2026-08-19, both in code whose authors had the warning
available, and both had already been explained in comments elsewhere in the tree.
Documentation demonstrably does not contain either; an executable check might.

TRAP A — interpolated source passed to a Python interpreter
    subprocess.check_output([sys.executable, '-c', f"...{value}..."])

  Found in services/simulation/simulation_api.py, where `value` was a VIN taken
  from `request.get_json()`. An apostrophe in the request body terminated a string
  literal and the remainder executed as Python, in a process whose role reads the
  device-certificate table. See
  issues/2026-08-19-cms-sim-api-subprocess-source-interpolation/.
  Fix: pass values via argv (or env) and keep the child program a constant.

TRAP B — DynamoDB FilterExpression combined with Limit in one call
    table.scan(FilterExpression=..., Limit=1)

  DynamoDB applies Limit BEFORE the filter, so the call examines Limit items and
  filters those. Measured on a 59-row table: Limit=1 -> ScannedCount 1, Count 0;
  unlimited -> ScannedCount 59, Count 1. Found in simulation_lambda.py (the
  "mark vehicles disconnected" branch, which therefore never ran) and in
  simulation_api.py's certificate fallback. The trap was ALREADY documented in
  services/vfo-pipeline/vfo_tools.py:137-150 and three comments in
  main_api/index.py, and still shipped twice.
  Fix: paginate on LastEvaluatedKey, or query an index instead of scanning.

Why AST rather than grep
------------------------
Both traps defeat text matching in opposite directions:

* Grepping `'-c', f"` matches `cdk synth -c key=value` context flags, which are
  harmless and numerous — every hit in this repo is a false positive.
* Grepping `FilterExpression.*Limit=` matches the COMMENTS that warn about the
  trap (including the fixes' own comments), while MISSING real multi-line calls,
  which is the form the live defects actually took.

An AST walk sees calls, not lines, and never sees comments at all.

Run:
    python3 deployment/scripts/test_source_trap_lint.py            # lint the repo
    python3 deployment/scripts/test_source_trap_lint.py --self-test  # prove it fires
"""
from __future__ import annotations

import argparse
import ast
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories that are vendored, generated, or staged build contexts.
EXCLUDED_PARTS = {
    ".venv", "venv", "can_env", "sim_env", "node_modules", "__pycache__",
    ".build", "cdk.out", "ecr", "package", "site-packages", ".git",
}
EXCLUDED_PREFIXES = ("cdk.out",)

SUBPROCESS_FUNCS = {"run", "check_output", "check_call", "call", "Popen"}
SCAN_FUNCS = {"scan", "query"}

# --- baseline ----------------------------------------------------------------
# Known, deliberately-unfixed sites. A lint that fails CI on pre-existing findings
# gets disabled, so each survivor is listed HERE with a reason and an owner rather
# than silenced by an inline comment (which is what gets copy-pasted onto new
# violations). Adding a NEW site still fails. Removing a fixed site is the only
# way an entry should ever leave this list.
#
# Format: "<repo-relative path>:<trap letter>" -> reason
BASELINE: dict[str, str] = {
    # THREE remaining sites, all one shape: pagination semantics.
    #
    # Identified by WHAT they query rather than by line number, because line
    # numbers drift — these moved by ~30 lines when the DTC lookup above them was
    # fixed on 2026-08-20:
    #
    #   - trips-by-driver              (trips_table.scan, FilterExpression driverId)
    #   - safety-events-by-vehicle     (safety_events_table.scan, FilterExpression vehicleId)
    #   - recent-safety-events         (safety_events_table.scan, FilterExpression timestamp >=)
    #
    # The caller asks for `limit` rows and DDB filters AFTER the page cut, so these
    # UNDER-RETURN rather than mis-resolve a specific record. Fixing them changes
    # paging behaviour on live endpoints, and index.py is the active surface of spec
    # 2026-08-07-cms-account-provisioning-model, so they are deferred rather than
    # edited from a concurrent session.
    #
    # A fourth site — the DTC description lookup — WAS baselined here and was fixed
    # on 2026-08-20. It was a genuine silently-missing lookup rather than an
    # under-return, so it did not belong in the same deferral. Its absence from this
    # list is the record that it is done.
    # Tracked in issues/2026-08-19-cms-connection-state-housekeeping/.
    "modules/cms_ui/source/handlers/main_api/index.py:B": (
        "3 sites — pagination under-returns (trips-by-driver, "
        "safety-events-by-vehicle, recent-safety-events), not failed lookups. "
        "Contended file; deferred."
    ),
    # Stale scripts. Both target hardcoded tables that DO NOT EXIST
    # (cms-631ca2-591631-trips-new, cms-0a0e68e9-telemetry), carry a hardcoded
    # profile/region, and scripts/README.md documents a --vehicle-id/--table CLI
    # that neither implements. delete-vehicle-data.py additionally has no dry-run
    # and no confirmation, so REPAIRING its scan would make an unguarded
    # destructive script effective — the opposite of an improvement. Awaiting a
    # decision to delete or rewrite; not fixed in place.
    "scripts/delete-vehicle-data.py:B": "stale script, dead tables, no guards — do not repair in place",
    "scripts/find-vehicle-data.py:B": "stale script, sibling of the above — same decision pending",
}


def _baseline_key(path: Path, trap: str) -> str:
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        rel = path
    return f"{rel}:{trap}"


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        parts = set(path.parts)
        if parts & EXCLUDED_PARTS:
            continue
        if any(p.startswith(EXCLUDED_PREFIXES) for p in path.parts):
            continue
        yield path


def _is_python_interpreter(node: ast.AST) -> bool:
    """True if the node plausibly names a Python interpreter."""
    # sys.executable
    if isinstance(node, ast.Attribute) and node.attr == "executable":
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "python" in node.value.lower()
    # A variable named like an interpreter, e.g. PY / py_exe.
    if isinstance(node, ast.Name):
        return node.id.lower() in {"py", "python", "py_exe", "interpreter", "executable"}
    return False


def _is_subprocess_call(node: ast.Call) -> bool:
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr in SUBPROCESS_FUNCS:
        # subprocess.run(...) or sp.run(...)
        return True
    return False


def check_trap_a(tree: ast.AST, path: Path) -> list[str]:
    """Interpolated (f-string) source handed to an interpreter via -c."""
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        if not node.args:
            continue
        argv = node.args[0]
        if not isinstance(argv, (ast.List, ast.Tuple)):
            continue
        elts = list(argv.elts)
        if not elts or not _is_python_interpreter(elts[0]):
            # `cdk -c key=value` and friends land here and are correctly ignored.
            continue
        for i, elt in enumerate(elts):
            is_dash_c = isinstance(elt, ast.Constant) and elt.value == "-c"
            if not is_dash_c or i + 1 >= len(elts):
                continue
            payload = elts[i + 1]
            if isinstance(payload, ast.JoinedStr):
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}:{payload.lineno}: TRAP A — "
                    "f-string interpolated into interpreter source passed via -c. "
                    "Pass values as argv and keep the child program a constant."
                )
    return findings


def check_trap_b(tree: ast.AST, path: Path) -> list[str]:
    """DynamoDB scan/query using FilterExpression together with Limit."""
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute) or f.attr not in SCAN_FUNCS:
            continue
        kw = {k.arg for k in node.keywords if k.arg}
        if "FilterExpression" in kw and "Limit" in kw:
            findings.append(
                f"{path.relative_to(REPO_ROOT)}:{node.lineno}: TRAP B — "
                f"{f.attr}() combines FilterExpression with Limit. DynamoDB applies "
                "Limit BEFORE the filter, so this examines Limit items and filters "
                "those. Paginate on LastEvaluatedKey, or query an index."
            )
    return findings


def lint(root: Path) -> tuple[list[str], list[str]]:
    """Return (findings, suppressed) — suppressed are baseline hits."""
    findings: list[str] = []
    suppressed: list[str] = []
    for path in _iter_python_files(root):
        try:
            # Scanned files may contain their own SyntaxWarnings (e.g. an invalid
            # "\d" escape in a non-raw string). Those belong to that file's own
            # build output, not to this lint's, so keep them out of our report.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue  # not our business; the build will complain
        for trap, hits in (("A", check_trap_a(tree, path)),
                           ("B", check_trap_b(tree, path))):
            if not hits:
                continue
            key = _baseline_key(path, trap)
            if key in BASELINE:
                suppressed.extend(hits)
            else:
                findings.extend(hits)
    return findings, suppressed


# --- positive controls -------------------------------------------------------
# A lint that has never been observed to fire is not known to work. These are the
# original defect shapes, reduced.

_BAD_A = '''
import subprocess, sys
vin = request_json["vin"]
subprocess.check_output([sys.executable, "-c", f"""
print('{vin}')
"""])
'''

_BAD_A_MULTILINE = '''
import subprocess, sys
subprocess.check_output(
    [
        sys.executable,
        "-c",
        f"x = '{value}'",
    ],
    text=True,
)
'''

_BAD_B = '''
resp = table.scan(
    FilterExpression="vin = :v",
    ExpressionAttributeValues={":v": vin},
    Limit=1,
)
'''

_GOOD_CDK_CONTEXT = '''
import subprocess
subprocess.run(["cdk", "synth", "-c", f"uiCustomDomain={domain}"])
'''

_GOOD_ARGV = '''
import subprocess, sys
SRC = "import sys; print(sys.argv[1])"
subprocess.check_output([sys.executable, "-c", SRC, vin])
'''

_GOOD_PAGINATED = '''
kwargs = {"FilterExpression": "vin = :v"}
while True:
    resp = table.scan(**kwargs)
    if resp.get("Items") or "LastEvaluatedKey" not in resp:
        break
    kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
'''


def self_test() -> int:
    cases = [
        ("BAD  trap A (f-string payload)", _BAD_A, "A", True),
        ("BAD  trap A (multi-line argv)", _BAD_A_MULTILINE, "A", True),
        ("BAD  trap B (filter + limit)", _BAD_B, "B", True),
        ("GOOD cdk -c context flag", _GOOD_CDK_CONTEXT, "A", False),
        ("GOOD argv-parameterised -c", _GOOD_ARGV, "A", False),
        ("GOOD paginated scan", _GOOD_PAGINATED, "B", False),
    ]
    failures = 0
    for label, src, trap, should_fire in cases:
        tree = ast.parse(src)
        fake = REPO_ROOT / "self_test.py"
        found = (check_trap_a(tree, fake) if trap == "A"
                 else check_trap_b(tree, fake))
        fired = bool(found)
        ok = fired == should_fire
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'}  {label}: "
              f"{'fired' if fired else 'silent'} (expected "
              f"{'fire' if should_fire else 'silence'})")
    print()
    if failures:
        print(f"{failures} self-test case(s) FAILED — the lint is not trustworthy.")
        return 1
    print("Self-test passed: the lint fires on both original defect shapes and "
          "stays silent on the benign look-alikes.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Lint for two known repo traps.")
    p.add_argument("--self-test", action="store_true",
                   help="verify the lint fires on the original defect shapes")
    p.add_argument("--root", default=str(REPO_ROOT))
    args = p.parse_args()

    if args.self_test:
        return self_test()

    findings, suppressed = lint(Path(args.root))
    if suppressed:
        print(f"{len(suppressed)} baselined finding(s) (see BASELINE in this file):")
        for f in suppressed:
            print(f"  [baselined] {f.split(' TRAP')[0]}")
        print()
    if findings:
        print(f"{len(findings)} NEW finding(s):\n")
        for f in findings:
            print(f"  {f}")
        print("\nSee the module docstring for the fix pattern for each trap.")
        print("If a finding is deliberate, add it to BASELINE with a reason — do "
              "not silence it inline.")
        return 1
    print("No new findings: no interpolated interpreter source, no unbaselined "
          "FilterExpression+Limit calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

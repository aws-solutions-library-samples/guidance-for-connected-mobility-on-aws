"""Portfolio guard: no nested def may close over a function-local-shadowed import.

Why this file exists
--------------------
On 2026-09-10 this bug class produced two live 500s in
``modules/cms_ui/source/handlers/main_api/index.py`` within hours of each other,
and a third historical instance is documented in that file's own comments:

    issues/2026-09-10-hashlib-shadowed-local-500s-service-history/
    issues/2026-09-10-decimal-shadowed-local-500s-service-history-at-scale/

The mechanism::

    import hashlib                      # module level

    def handler(...):
        ...
        import hashlib                  # ANYWHERE in this function's body
        ...
        def helper():
            hashlib.sha256(...)         # <- unbound local on paths that
                                        #    have not run the import above

Importing a name inside a function makes it a local of the *entire* function.
A nested ``def`` then closes over that local, which is unbound on any path that
has not already executed the import statement, raising::

    free variable 'X' referenced before assignment in enclosing scope

Three properties make this class expensive to catch any other way:

* It is invisible in review. Every line reads correctly in isolation.
* It is invisible to a green unit suite when the nested def sits behind a
  short-circuit (``a or helper()``) or is a lazily-invoked callback
  (``json.dumps(default=helper)`` only calls it on meeting an unserialisable
  value). Both of those are exactly how the two 2026-09-10 instances hid.
* The obvious narrow guard does not work. The first fix added
  ``assert 'import hashlib' not in handler_source``, which passed continuously
  while the ``Decimal`` instance was live in production, because it asserted a
  name instead of the rule.

So this asserts the rule, across every handler, not one name in one function.

Severity split
--------------
**RISK** - the nested def does not import the name and no sibling import
precedes it. Broken on at least one reachable path. Fails this test.

**LUCK** - a sibling import precedes the def in the same body, so it works by
statement *order* rather than by scoping. Reported, not failed: these are not
currently broken, and churning them across files owned by concurrent specs costs
more than it buys. They are fragile, though — reordering, or wrapping the import
in a branch, breaks them silently. Five such helpers in ``main_api/index.py``
were hardened when ``Decimal`` broke, because that is precisely the luck that
ran out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Live, deployed source. Deliberately NOT the whole repo: cdk.out/ and .build/
# hold stale asset snapshots of pre-fix files, which would report findings that
# no longer exist in any source we ship.
SCAN_DIRS = (
    "modules/cms_ui/source/handlers",
    "services",
    "deployment/stacks",
    "deployment/scripts",
)

EXCLUDE_PARTS = (
    ".venv", "node_modules", "cdk.out", ".build", "site-packages",
    "build", ".git", "__pycache__",
)


def _module_level_imports(tree: ast.Module) -> set[str]:
    return {
        alias.asname or alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


def _imports_anywhere_in(node: ast.AST) -> set[str]:
    return {
        alias.asname or alias.name.split(".")[0]
        for child in ast.walk(node)
        if isinstance(child, (ast.Import, ast.ImportFrom))
        for alias in child.names
    }


def _loaded_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _sibling_imports_before(outer: ast.AST, inner: ast.AST, names: set[str]) -> bool:
    """True only if an unconditional sibling import of every name precedes ``inner``.

    Execution guarantee, not line position. An import nested inside ``if`` /
    ``for`` / ``try`` textually precedes a later def while being skippable at
    runtime — which is precisely the shape of the 2026-09-10 ``hashlib`` bug,
    where the shadowing import sat inside ``if _drivers_cache:``. Only an import
    in the *same statement list* as the def is guaranteed to have run by the time
    the def's body executes.

    An earlier version of this helper compared line numbers, classified that
    known-bad shape as LUCK, and was caught by
    ``test_scanner_detects_a_known_positive``. Keeping the reasoning here because
    the weaker rule looks correct and under-reports real breakage.
    """
    for block in ast.walk(outer):
        body = getattr(block, "body", None)
        if not isinstance(body, list) or inner not in body:
            continue
        idx = body.index(inner)
        bound: set[str] = set()
        for stmt in body[:idx]:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    bound.add(alias.asname or alias.name.split(".")[0])
        if names <= bound:
            return True
        # `orelse` / `finalbody` are also statement lists that can hold the def
    for block in ast.walk(outer):
        for attr in ("orelse", "finalbody"):
            body = getattr(block, attr, None)
            if not isinstance(body, list) or inner not in body:
                continue
            idx = body.index(inner)
            bound = set()
            for stmt in body[:idx]:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    for alias in stmt.names:
                        bound.add(alias.asname or alias.name.split(".")[0])
            if names <= bound:
                return True
    return False


def scan_source(source: str) -> tuple[list[str], list[str]]:
    """Return (risk, luck) finding descriptions for one module's source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []

    module_level = _module_level_imports(tree)
    risk: list[str] = []
    luck: list[str] = []

    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        shadowed = _imports_anywhere_in(outer) & module_level
        if not shadowed:
            continue

        for inner in ast.walk(outer):
            if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if inner is outer:
                continue
            risky = sorted((_loaded_names(inner) & shadowed) - _imports_anywhere_in(inner))
            if not risky:
                continue
            guaranteed = _sibling_imports_before(outer, inner, set(risky))
            desc = f"line {inner.lineno}: {outer.name}() > {inner.name}() uses {risky}"
            (luck if guaranteed else risk).append(desc)

    return risk, luck


def _python_files() -> list[Path]:
    files: list[Path] = []
    for rel in SCAN_DIRS:
        base = REPO_ROOT / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in EXCLUDE_PARTS for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def test_no_nested_def_closes_over_a_shadowed_import() -> None:
    """No nested def may reference a name shadowed by a function-local import.

    This is the executable form of the rule. It is verified to catch both
    2026-09-10 instances: reintroducing the function-local ``import hashlib``,
    or removing the def-local ``from decimal import Decimal`` from
    ``_sh_decimal_default``, each makes this fail.
    """
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        risk, _ = scan_source(path.read_text(encoding="utf-8", errors="replace"))
        if risk:
            offenders[str(path.relative_to(REPO_ROOT))] = risk

    if offenders:
        lines = ["nested def(s) close over a function-local-shadowed import.", ""]
        for rel, findings in sorted(offenders.items()):
            lines.append(f"  {rel}")
            lines.extend(f"      {f}" for f in findings)
        lines += [
            "",
            "Each raises \"free variable 'X' referenced before assignment in "
            "enclosing scope\" on any path that has not already executed the",
            "shadowing import. Fix: import the name inside the nested def's own body.",
            "See issues/2026-09-10-decimal-shadowed-local-500s-service-history-at-scale/.",
        ]
        pytest.fail("\n".join(lines))


def test_scanner_detects_a_known_positive() -> None:
    """Positive control: the scanner must flag the real 2026-09-10 shape.

    Without this, a scanner bug that silently matches nothing turns the guard
    above into a permanent green — the failure mode where a test asserts a
    property it can no longer observe.
    """
    known_bad = (
        "import hashlib\n"
        "def handler(event, context):\n"
        "    if event:\n"
        "        import hashlib\n"          # shadows for the whole function
        "    def _key(v):\n"
        "        return hashlib.sha256(v).hexdigest()\n"
        "    return _key(b'x')\n"
    )
    risk, luck = scan_source(known_bad)
    assert risk, f"scanner failed to flag the known-bad shape (luck={luck})"
    assert "_key" in risk[0]


def test_scanner_accepts_the_applied_fix() -> None:
    """Negative control: a def-local import must NOT be flagged.

    Pins the fix shape actually shipped, so a future scanner change that starts
    rejecting it fails here rather than sending someone chasing a false positive.
    """
    fixed = (
        "import hashlib\n"
        "def handler(event, context):\n"
        "    if event:\n"
        "        import hashlib\n"
        "    def _key(v):\n"
        "        import hashlib\n"          # def-local: the fix
        "        return hashlib.sha256(v).hexdigest()\n"
        "    return _key(b'x')\n"
    )
    risk, _ = scan_source(fixed)
    assert not risk, f"def-local import wrongly flagged: {risk}"

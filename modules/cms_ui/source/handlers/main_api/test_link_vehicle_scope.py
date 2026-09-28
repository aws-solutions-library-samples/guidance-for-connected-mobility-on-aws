"""Guard: no self-service route may grant an UNSCOPED group.

Security review cycle 1 of spec `2026-08-07-cms-account-provisioning-model` found
`POST /api/v1/users/link-vehicle` calling
`admin_add_user_to_group(caller, GroupName='fleet-viewer')` with no authorization check.

`fleet-viewer` sets `has_unscoped_access = True` (index.py:902), so any caller holding one
valid link code obtained global cross-fleet read. It also defeated the route's own intent:
the very next statement writes `custom:fleetIds`/`custom:vehicleIds` to scope the user, which
is meaningless for a group that ignores scoping.

Not reachable on prod when found (`AllowAdminCreateUserOnly=True`, so a stranger could not get
an account), but it would have gone live the moment Phase B external self-signup was enabled —
and it is the same shape as the `Fail-open authz` P0 that motivated this spec: a route handing
out privilege without a group check.

THE INVARIANT: the unscoped group names must never appear as HARDCODED literals in a
group-granting call. The legitimate admin routes (`POST /api/v1/users`,
`PUT /api/v1/users/*`, both gated `and is_admin`) pass a *variable* group chosen by an
authenticated admin. So a hardcoded unscoped literal is, structurally, a self-service grant.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_INDEX = Path(__file__).parent / "index.py"

# Groups that confer unscoped access. Keep in sync with `has_unscoped_access` (index.py:902):
#   has_unscoped_access = is_admin or is_viewer
_UNSCOPED_GROUPS = ("platform-admin", "fleet-viewer")


def _code_only(src: str) -> str:
    """Strip docstrings and comments so prose cannot trip or mask the assertions."""
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


class TestNoSelfServiceUnscopedGrant(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _code_only(_INDEX.read_text())

    def test_no_hardcoded_unscoped_group_is_granted(self) -> None:
        """No `GroupName='platform-admin'|'fleet-viewer'` literal anywhere in main_api."""
        for group in _UNSCOPED_GROUPS:
            for quote in ("'", '"'):
                needle = f"GroupName={quote}{group}{quote}"
                self.assertNotIn(
                    needle,
                    self.code,
                    f"{needle} is a hardcoded unscoped-group grant. Admin routes pass a "
                    f"variable group; a literal here means a self-service route is handing "
                    f"out unscoped access. Use a scoped group (fleet-guest) instead.",
                )

    def test_link_vehicle_grants_the_scoped_guest_group(self) -> None:
        """The specific route from the finding grants fleet-guest, positively asserted.

        Absence-only assertions pass if the call is deleted, which would silently break the
        feature. This pins the intended behaviour.
        """
        i = self.code.index("/api/v1/users/link-vehicle")
        route = self.code[i : i + 2600]
        self.assertIn("admin_add_user_to_group", route, "link-vehicle no longer grants a group")
        self.assertIn(
            "GroupName='fleet-guest'",
            route,
            "link-vehicle must grant the scoped read-only group",
        )
        # And it must still scope the caller, which is the other half of the contract.
        self.assertIn("custom:fleetIds", route)
        self.assertIn("custom:vehicleIds", route)

    def test_admin_grant_sites_remain_admin_gated(self) -> None:
        """The two variable-group grants must keep their `and is_admin` gate.

        If one loses it, the hardcoded-literal check above would not catch the resulting hole,
        because those sites pass a caller-supplied group rather than a literal.
        """
        for route in ("'/api/v1/users' and method == 'POST'",
                      "'/api/v1/users/') and method == 'PUT'"):
            i = self.code.find(route)
            self.assertNotEqual(i, -1, f"route declaration moved or changed: {route}")
            decl = self.code[i : self.code.index("\n", i)]
            self.assertIn(
                "is_admin", decl,
                f"route lost its is_admin gate, which would allow granting any group: {decl.strip()}",
            )


class TestEveryGrantSiteIsGatedOrScoped(unittest.TestCase):
    """Close the CLASS, not just the literal instance (security review cycle 2, S8).

    The literal check above cannot see a NEW route that grants an unscoped group via a
    *variable* with no `is_admin` gate — which is the shape of both authz defects this repo
    has had: the `Fail-open authz` P0 (groupless treated as admin) and link-vehicle
    (self-service grant of `fleet-viewer`). So walk the AST instead of the text.

    THE RULE: every `admin_add_user_to_group` call must satisfy at least one of
      (a) its enclosing `if`/`elif` route condition mentions `is_admin` — an admin chose the
          group, so a variable is fine; or
      (b) its `GroupName` is a string literal naming a SCOPED group.

    A call that is neither is a self-service grant of an admin-chosen or unknown group.
    """

    _SCOPED_GROUPS = ("fleet-guest", "fleet-operator", "fleet-manager", "driver")

    def test_every_admin_add_user_to_group_is_gated_or_scoped(self) -> None:
        import ast

        tree = ast.parse(_INDEX.read_text())

        # Map each line number to the set of enclosing `if` test sources, so we can ask what
        # guards a given call without relying on line proximity.
        guards: dict[int, list[str]] = {}

        def walk(node: ast.AST, enclosing: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.If):
                    cond = ast.unparse(child.test)
                    for stmt in child.body:
                        for n in ast.walk(stmt):
                            if hasattr(n, "lineno"):
                                guards.setdefault(n.lineno, []).append(cond)
                        walk(stmt, enclosing + [cond])
                    for stmt in child.orelse:
                        walk(stmt, enclosing)
                else:
                    walk(child, enclosing)

        walk(tree, [])

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "admin_add_user_to_group"):
                continue

            group_kw = next((k for k in node.keywords if k.arg == "GroupName"), None)
            is_scoped_literal = (
                group_kw is not None
                and isinstance(group_kw.value, ast.Constant)
                and group_kw.value.value in self._SCOPED_GROUPS
            )
            admin_gated = any(
                "is_admin" in cond for cond in guards.get(node.lineno, [])
            )
            if not (is_scoped_literal or admin_gated):
                shown = ast.unparse(group_kw.value) if group_kw else "<no GroupName>"
                violations.append(
                    f"line {node.lineno}: GroupName={shown} — neither admin-gated nor a "
                    f"scoped literal"
                )

        self.assertEqual(
            violations,
            [],
            "Ungated group grant(s) found. Every admin_add_user_to_group must either sit "
            "under a route condition mentioning is_admin, or grant a scoped group literal "
            f"from {self._SCOPED_GROUPS}:\n  " + "\n  ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()

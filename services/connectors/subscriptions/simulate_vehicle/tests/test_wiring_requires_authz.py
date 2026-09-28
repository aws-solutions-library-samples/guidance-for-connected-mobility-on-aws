"""Executable form of FG2.2's guard: this handler cannot be WIRED without authz.

WHY THIS EXISTS
---------------
`simulate_vehicle/handler.py` shipped in commit `aa9e6c1a` with **zero**
authorization: `_claims(event)` was defined and never invoked, neither handler
read `event.requestContext`, and `_list_all_vehicles` is a full unfiltered
`scan()`. Security review Cycle 3 caught it as a Warning. Wired with the standard
Cognito authorizer, `GET /simulate/vehicles` would have returned every VIN plus
`producer` and `dataSource` across all customers to any authenticated caller.

It was not live-exploitable only because the Lambda has no route and no IAM grant
in `subscriptions_stack.py`. The frontend
(`subscriptionsClient.ts::listVehiclesForSimulation`, called from
`SimulateVehicleView` on mount) already presumes the endpoint, so the wiring
commit is the one remaining step.

FG2.2 recorded that requirement in the handler's module docstring, and the
implementing agent correctly reported the control as **documentary only**. A rule
that exists only in a comment is a rule that gets crossed — `agentic-tiers.md`
says so about prompts and the same applies to docstrings. This test is the
executable form.

WHAT IT ASSERTS
---------------
A conditional ratchet, so it is meaningful both before and after wiring:

  * **Always**: if `_require_operator` is called in the handler, both public
    entrypoints must call it, and each must have a 403 path. This is what makes
    the test non-vacuous today.
  * **On wiring**: the moment `subscriptions_stack.py` gains any reference to
    this handler, the guard must already be present. Wiring it unguarded fails
    here rather than in production.

It deliberately does NOT require the route to exist. It requires that route and
guard arrive together, in either order.

Deliberately source-structural, not behavioural: the behavioural coverage lives
in `test_handler.py`'s `TestAuthorizationListVehicles` /
`TestAuthorizationSimulateStart` (7 cases each, each mutation-verified). This file
guards the *deployment* seam those tests cannot see, and needs no `cdk synth` —
which matters because this app's app-wide fail-closed synth guards require
Secrets Manager credentials.
"""
from __future__ import annotations

import re
from pathlib import Path
import unittest


def _repo_root() -> Path:
    """Anchor by walking up to the `.git` marker.

    NOT a `parents[N]` depth count. Security review Cycle 1 of this spec failed on
    exactly that: `test_vehicle_sold_to.py` used un-`resolve()`d `parents[4]`,
    resolved both its roots to non-existent paths, walked ZERO files and passed
    unconditionally. A depth count breaks the moment a file moves.
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"could not locate repo root (no .git marker above {here})")


_ROOT = _repo_root()
_HANDLER = _ROOT / "services/connectors/subscriptions/simulate_vehicle/handler.py"
_STACK = _ROOT / "deployment/stacks/subscriptions_stack.py"

# The two public Lambda entrypoints. Both are reachable from API Gateway once
# wired, so both need the guard — a single shared check would not be enough,
# and FG2.1's mutations were run per-handler for this reason.
_ENTRYPOINTS = ("list_vehicles_handler", "simulate_start_handler")

# Any of these appearing in the stack means this handler is being deployed.
_WIRING_MARKERS = ("simulate_vehicle", "simulate-vehicle", "SimulateVehicle")


def _handler_source() -> str:
    assert _HANDLER.exists(), f"handler not found at {_HANDLER}"
    return _HANDLER.read_text()


def _stack_source() -> str:
    assert _STACK.exists(), f"stack not found at {_STACK}"
    return _STACK.read_text()


def _strip_comments_and_docstrings(src: str) -> str:
    """Remove `#` comments and triple-quoted blocks.

    Without this the test would pass on a handler that merely *mentions*
    `_require_operator` in its docstring — which is precisely the documentary-only
    state this file exists to replace.
    """
    src = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    src = re.sub(r"'''.*?'''", "", src, flags=re.DOTALL)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )


def _body_of(func_name: str, code: str) -> str:
    """Return `func_name`'s body, ending at the next top-level `def`/`class`."""
    match = re.search(rf"^def {re.escape(func_name)}\b", code, flags=re.MULTILINE)
    if not match:
        return ""
    rest = code[match.end():]
    nxt = re.search(r"^(def |class )", rest, flags=re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


class SimulateWiringRequiresAuthzTest(unittest.TestCase):
    # ---- anti-vacuity: the inputs this test reasons about must be real -------

    def test_premise_handler_and_stack_are_readable(self):
        code = _handler_source()
        self.assertGreater(len(code), 2000, "handler source implausibly small")
        self.assertGreater(len(_stack_source()), 2000, "stack source implausibly small")

    def test_premise_both_entrypoints_exist(self):
        """If an entrypoint is renamed, this test must fail rather than silently
        stop checking it — the failure mode that made Cycle 1's tripwire vacuous."""
        code = _handler_source()
        for fn in _ENTRYPOINTS:
            self.assertRegex(
                code, rf"(?m)^def {fn}\b",
                msg=(f"entrypoint `{fn}` not found. If it was renamed, update "
                     f"_ENTRYPOINTS — do NOT delete this assertion."),
            )

    def test_premise_comment_stripping_works(self):
        """Guards the stripper itself: a docstring mention must not count."""
        stripped = _strip_comments_and_docstrings(
            'def f():\n    """calls _require_operator"""\n    return 1\n'
        )
        self.assertNotIn("_require_operator", stripped)

    # ---- the always-on assertions -------------------------------------------

    def test_both_entrypoints_call_require_operator(self):
        code = _strip_comments_and_docstrings(_handler_source())
        missing = [
            fn for fn in _ENTRYPOINTS
            if "_require_operator" not in _body_of(fn, code)
        ]
        self.assertEqual(
            missing, [],
            msg=(
                f"These simulate entrypoints do NOT call `_require_operator` in "
                f"executable code: {missing}.\n"
                "`GET /simulate/vehicles` returns every VIN plus producer and "
                "dataSource across all customers; unguarded it is the repo's "
                "open-P0 fail-open shape (issues/2026-08-05-main-api-fail-open-"
                "authz-defaults). See security-review.md Cycle 3."
            ),
        )

    def test_both_entrypoints_have_a_403_path(self):
        """Calling the guard is not enough if the exception is swallowed."""
        code = _strip_comments_and_docstrings(_handler_source())
        for fn in _ENTRYPOINTS:
            self.assertIn(
                "403", _body_of(fn, code),
                msg=(f"`{fn}` calls the guard but has no 403 path. A swallowed "
                     f"_Unauthorized fails OPEN."),
            )

    def test_require_operator_is_defined_not_just_referenced(self):
        code = _strip_comments_and_docstrings(_handler_source())
        self.assertRegex(
            code, r"(?m)^def _require_operator\b",
            msg="`_require_operator` is called but not defined in this module.",
        )

    # ---- the conditional ratchet -------------------------------------------

    def test_wiring_the_route_requires_the_guard_to_be_present(self):
        """The load-bearing case. Fires the moment someone wires this handler.

        Today the stack has no reference, so this passes on the second branch.
        It is not vacuous: the assertions above independently prove the guard is
        present, so this cannot become the only thing standing between a wired
        route and an unguarded scan.
        """
        stack = _stack_source()
        wired = [m for m in _WIRING_MARKERS if m in stack]
        code = _strip_comments_and_docstrings(_handler_source())
        guarded = all("_require_operator" in _body_of(fn, code) for fn in _ENTRYPOINTS)

        if wired:
            self.assertTrue(
                guarded,
                msg=(
                    f"subscriptions_stack.py now references this handler "
                    f"({wired}), so it is being deployed — but not every "
                    f"entrypoint calls `_require_operator`. Wiring the route and "
                    f"guarding it must land together. Per the handler docstring, "
                    f"the wiring commit must also add an IAM grant scoped to the "
                    f"vehicles table and a smoke test asserting a non-operator "
                    f"gets 403."
                ),
            )
        else:
            self.assertFalse(
                guarded and False,
                msg="unreachable; documents that the un-wired branch is expected",
            )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Pins the CMS -> DMS outbound dispatch route and its 403 disambiguation.

Issue: ``issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/``

WHY THIS FILE EXISTS
--------------------
``POST /api/dispatch`` forwarded to ``/api/dms/repair-orders``, which has **no POST
method** — ``dms_api_stack.py:1228-1232`` creates that resource only as a prefix for
``/{roId}/notes``. API Gateway answers an unmatched route with
``403 {"message":"Missing Authentication Token"}``, and the handler passed 401/403
through verbatim on the assumption they carry auth context. So a routing bug presented
as a permissions problem, and dispatch was broken for **every** caller from its
introduction until 2026-09-24.

Proven at route level against deployed staging, unauthenticated::

    POST /api/dms/repair-orders        -> 403 {"message":"Missing Authentication Token"}
    POST /api/dms/fleet/repair-orders  -> 401 {"message":"Unauthorized"}

403-on-the-first and 401-on-the-second is conclusive: the first has no POST.

Source-level assertions rather than an import-and-invoke of ``handler``: ``index.py`` is
a ~10k-line monolith whose import path pulls in module-level AWS clients and env-var
reads. The sibling ``test_connected_services_proxy.py`` sets the same precedent
(``import ast``, repo-root-by-marker) for contract guards over this file.

WHY NOT A CROSS-REPO CHECK
--------------------------
The ideal assertion compares the CMS literal against DMS's *wired* routes. That needs the
DMS repo present, which cannot be assumed in CI for this repo, so a skipped test would be
the common case — a guard that usually does not run. Instead the correct path is asserted
directly here and the cross-repo gap is recorded as prevention item 3 in the issue.
"""
from __future__ import annotations

import os
import re

import pytest

_HANDLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_PY = os.path.join(_HANDLER_DIR, "index.py")

#: The only correct outbound target. ``dms_api_stack.py:1247`` wires
#: ``POST /api/dms/fleet/repair-orders`` to ``repair_orders.create_fleet_repair_order``,
#: the sole handler that writes ``initiated_by='cms_booking'`` plus the ``evidence`` map
#: v1.5 T2.7 depends on, and whose group check admits ``fleet-operator``.
_CORRECT_PATH = "/api/dms/fleet/repair-orders"

#: The unrouted path this bug used. Must never appear as a URL-construction literal.
_UNROUTED_PATH = "/api/dms/repair-orders"


@pytest.fixture(scope="module")
def src() -> str:
    with open(_INDEX_PY, encoding="utf-8") as fh:
        return fh.read()


def _dispatch_url_literals(src: str) -> list[str]:
    """Every ``'/api/dms/...'`` literal used to BUILD a URL (i.e. concatenated onto an
    endpoint), ignoring prose in comments and docstrings.

    Matching on the concatenation is deliberate: ``_UNROUTED_PATH`` legitimately appears
    in this module's explanatory comments, and a bare substring search would therefore
    fail on the very comment that documents the bug. Only URL *construction* is the
    contract.
    """
    return re.findall(r"""rstrip\(['"]/['"]\)\s*\+\s*['"](/api/dms/[^'"]*)['"]""", src)


class TestOutboundDispatchRoute:
    def test_dispatch_targets_the_fleet_surface(self, src: str) -> None:
        """The dispatch route builds its URL from the fleet path.

        Mutation check (recorded in the issue): reverting the literal to
        ``/api/dms/repair-orders`` fails this test.
        """
        assert f"+ '{_CORRECT_PATH}'" in src, (
            f"The dispatch outbound URL must be built from {_CORRECT_PATH!r}. "
            "See issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/."
        )

    def test_no_url_is_built_from_the_unrouted_path(self, src: str) -> None:
        """No CMS->DMS call constructs a URL from the method-less collection path.

        This is the assertion that actually catches the regression. It is scoped to URL
        construction so the issue's own explanatory comments do not trip it — verified by
        ``test_the_unrouted_path_is_still_named_in_a_comment`` below, which proves the
        scoping is real rather than accidental.
        """
        built = _dispatch_url_literals(src)
        assert built, (
            "Found no '/api/dms/...' URL-construction literals at all. Either the "
            "concatenation style changed or this guard has gone vacuous — fix the regex."
        )
        offenders = [p for p in built if p == _UNROUTED_PATH]
        assert not offenders, (
            f"{len(offenders)} CMS->DMS call(s) build a URL from {_UNROUTED_PATH!r}, "
            "which has no POST method (dms_api_stack.py:1228-1232 wires only "
            "/{roId}/notes under it). API Gateway returns 403 'Missing Authentication "
            "Token' for it, which reads as an auth failure. Use "
            f"{_CORRECT_PATH!r}."
        )

    def test_every_dms_url_is_on_the_fleet_surface(self, src: str) -> None:
        """All CMS->DMS URL construction targets ``/api/dms/fleet/*``.

        Every other CMS->DMS call already did this correctly before the fix — the
        dispatch route was a lone divergence, with the right value present three times
        elsewhere in the same file. That makes 'all of them' the honest invariant, and it
        catches the next copy of this typo rather than only the one instance fixed here.
        """
        built = _dispatch_url_literals(src)
        assert built, "no '/api/dms/...' URL construction found — guard is vacuous"
        strays = [p for p in built if not p.startswith("/api/dms/fleet/")]
        assert not strays, (
            "CMS reaches DMS only through the fleet surface (spec "
            "2026-09-02-cms-dms-service-convergence D3 — the dealer-nested paths gate on "
            "dealer groups and correctly 403 a forwarded fleet token). Off-surface "
            f"target(s): {strays}"
        )

    def test_the_unrouted_path_is_still_named_in_a_comment(self, src: str) -> None:
        """Positive control for the scoping of the two tests above.

        Without this, ``test_no_url_is_built_from_the_unrouted_path`` would also pass if
        the regex silently stopped matching anything, or if someone deleted the comments
        explaining the bug. The unrouted path MUST remain documented in prose while
        absent from URL construction — that combination is the whole point, and this test
        proves the distinction is being drawn rather than assumed.
        """
        assert _UNROUTED_PATH in src, (
            "The unrouted path should still be named in the explanatory comment at the "
            "dispatch site, so the next reader learns why the fleet path is required."
        )
        assert _UNROUTED_PATH not in _dispatch_url_literals(src), (
            "…but it must not be a URL-construction target."
        )


class TestForbiddenTokenDisambiguation:
    """API Gateway overloads 403, so the handler must not pass it through blindly.

    It returns ``403 {"message":"Missing Authentication Token"}`` for a path/method it
    does not have — indistinguishable from a real DMS authorization refusal on status
    alone. That ambiguity cost two investigations: an operator and an architect both read
    ``"DMS responded 403."`` as a Cognito groups problem.
    """

    @staticmethod
    def _marker_branch(src: str) -> str:
        """Return exactly the unmatched-route branch's source.

        Bounded by its own opening condition and the fallback ``print`` that follows it,
        rather than by a fixed byte count. An earlier version of these tests took a
        900-character window, which ended *before* the branch's response dict — so
        ``test_dms_response_body_is_never_forwarded`` inspected a region that could not
        contain the thing it was asserting about, and a mutation forwarding the upstream
        body survived. Found by mutation, not by reading.

        The two positive controls below exist so that anchor drift fails loudly instead of
        silently shrinking the span back to a vacuous one.
        """
        start = src.index("if _he.code == 403 and 'Missing Authentication Token' in _he_marker:")
        end = src.index("print(f'POST /api/dispatch: DMS returned", start)
        branch = src[start:end]
        assert "'statusCode'" in branch, (
            "span lost the response dict — the branch anchors have drifted, fix them "
            "rather than letting this guard go vacuous"
        )
        assert "access will not help" in branch, (
            "span lost the operator-facing detail — anchors have drifted"
        )
        return branch

    def test_handler_detects_the_unrouted_marker(self, src: str) -> None:
        assert "Missing Authentication Token" in src, (
            "The dispatch error path must detect API Gateway's unmatched-route marker "
            "and report a routing failure rather than passing 403 through as auth."
        )

    def test_unrouted_403_is_not_reported_as_an_auth_failure(self, src: str) -> None:
        """The marker branch must return 502, not 403.

        Returning 403 would preserve exactly the ambiguity this branch exists to remove.
        """
        branch = self._marker_branch(src)
        assert "'statusCode': 502" in branch, (
            "The unmatched-route branch must answer 502 (upstream misconfiguration), "
            "not 403 — 403 is what made this a two-investigation bug."
        )
        assert "'statusCode': 403" not in branch, (
            "The unmatched-route branch must not answer 403."
        )
        # Join adjacent Python string literals before matching prose: the operator-facing
        # message is wrapped across source lines, so the phrase is not contiguous in the
        # raw text. Asserting on a shorter fragment instead would have been the weaker
        # fix — this keeps the full sentence as the contract.
        joined = re.sub(r"'\s*\n\s*'", "", branch)
        assert "not a permissions problem" in joined, (
            "The operator-facing detail should say plainly that this is not a "
            "permissions problem, so the next reader is not sent to Cognito."
        )

    def test_dms_response_body_is_never_forwarded(self, src: str) -> None:
        """Reading the upstream body to classify it must not become forwarding it.

        The pre-existing comment states the DMS body is deliberately not forwarded — it is
        another service's error shape and could carry detail CMS has not reviewed. The fix
        reads up to 512 bytes of it to look for the marker, so this pins that the read did
        not turn into a passthrough.

        Mutation-verified: appending ``+ _he_marker`` to the operator-facing detail fails
        this test. It did NOT fail an earlier byte-windowed version, which is why the span
        is now anchor-bounded.
        """
        branch = self._marker_branch(src)
        response_body = branch.split("'body': json.dumps(")[1]
        assert "_he_marker" not in response_body, (
            "The upstream DMS body (_he_marker) must not appear in CMS's response body — "
            "it is another service's error shape and may carry unreviewed detail. CMS "
            "emits its own message; the read is for classification only."
        )

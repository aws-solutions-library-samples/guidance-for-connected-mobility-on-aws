#!/usr/bin/env python3
"""Unit tests for ``connected_services_proxy`` — spec T1.4.

Spec: ``.kiro/specs/2026-09-10-cms-connected-services-consumer/`` T1.4 (Group 1).

Every test in this file uses ``MockHttpClient`` for the producer HTTP layer.
No test in Group 1 reaches the network — the real ``UrllibHttpClient`` is
exercised in Group 2's T2.1 against the deployed producer endpoint.

Run from repo root::

    python3 -m pytest modules/cms_ui/source/handlers/main_api/tests/test_connected_services_proxy.py -v

Test structure:
- ``TestConfig``            — ``ProxyConfig`` rejects bad configuration.
- ``TestGetSubscriptionFeed``— happy path, 404, 401, 5xx, network failure, header shape.
- ``TestEnrollVin``         — happy path, input validation, error mapping.
- ``TestUnenrollVin``       — happy path, path-injection guard.
- ``TestTokenProvider``     — proxy fails cleanly when the token provider
                               fails or returns empty.
- ``TestAuthorizationBoundary`` — asserts the CMS end-user's JWT never
                               reaches ``MockHttpClient``, only CMS's
                               subscriber token does. (Spec D2 —
                               structural, not stylistic.)
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time

import pytest

# Add the module's parent directory to sys.path so `import connected_services_proxy`
# works from any cwd. Same bootstrap style as the sibling tests in the same tree.
_HANDLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HANDLER_DIR)


def _find_repo_root() -> str:
    """Walk up to the directory holding ``deployment/stacks/ui_stack.py``.

    Resolved by marker rather than by a hardcoded number of ``..`` hops so
    that moving this test file does not silently break the auth-flow
    contract guard (``TestCognitoAuthFlowContract``) into a vacuous pass.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isfile(os.path.join(here, "deployment", "stacks", "ui_stack.py")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    raise AssertionError(
        "Could not locate the repo root (no deployment/stacks/ui_stack.py "
        "found walking up from this test file). TestCognitoAuthFlowContract "
        "cannot verify the Cognito auth-flow contract without it."
    )


_REPO_ROOT = _find_repo_root()

#: The live-captured producer contract. T2.1 asserts against this file rather
#: than against hand-written mock bodies, per spec R3: Group 1's three
#: happy-path mocks were all invented, and all three were wrong — the records
#: route returns four keys the mock omitted, ``POST /scope`` returns 200 with
#: ``added`` as a *list* where the mock had 201 with ``added: True``, and
#: ``DELETE /scope/{vin}`` returns 200 with a four-key body where the mock had
#: an empty 204. Deriving the mocks from the capture makes "re-capture after a
#: producer change" the single action that keeps the tests honest.
_LIVE_SHAPES_PATH = os.path.join(
    _REPO_ROOT,
    ".kiro",
    "specs",
    "2026-09-10-cms-connected-services-consumer",
    "producer-live-shapes.json",
)


def _load_live_shapes() -> dict:
    if not os.path.isfile(_LIVE_SHAPES_PATH):
        raise AssertionError(
            f"Live-shape capture not found at {_LIVE_SHAPES_PATH}. T2.1's shape "
            f"contracts are asserted against the real deployed producer's "
            f"responses; without the capture these tests would silently revert "
            f"to asserting a guess."
        )
    with open(_LIVE_SHAPES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


_LIVE = _load_live_shapes()


def _live_body(route: str) -> dict:
    """Return the live-captured 2xx body for ``route``, or fail loudly."""
    responses = _LIVE.get("responses") or {}
    if route not in responses:
        raise AssertionError(
            f"Live capture has no entry for {route!r}. Available: "
            f"{sorted(responses)}"
        )
    return responses[route]["body"]


#: The five routes this proxy calls, mapped to their captured 2xx bodies.
LIVE_RECORDS_BODY = _live_body("GET /subscriptions/{id}/records (scope non-empty)")
LIVE_RECORDS_EMPTY_BODY = _live_body("GET /subscriptions/{id}/records")
LIVE_SCOPE_ADD_BODY = _live_body("POST /subscriptions/{id}/scope")
LIVE_SCOPE_ADD_REPEAT_BODY = _live_body(
    "POST /subscriptions/{id}/scope (idempotent repeat)"
)
LIVE_SCOPE_REMOVE_BODY = _live_body("DELETE /subscriptions/{id}/scope/{vin}")
LIVE_SCOPE_REMOVE_REPEAT_BODY = _live_body(
    "DELETE /subscriptions/{id}/scope/{vin} (idempotent repeat)"
)


import connected_services_proxy  # noqa: E402
from connected_services_proxy import (  # noqa: E402
    COGNITO_AUTH_FLOW,
    COGNITO_AUTH_FLOW_CDK_KWARG,
    ERROR_CONFIG_MISSING,
    ERROR_PRODUCER_SHAPE_DRIFT,
    ERROR_PRODUCER_UNAUTHORIZED,
    ERROR_PRODUCER_UNAVAILABLE,
    TOKEN_REFRESH_MARGIN_SECONDS,
    CognitoUserPasswordTokenProvider,
    MockHttpClient,
    ProxyConfig,
    ProxyResult,
    StaticTokenProvider,
    TokenAcquisitionError,
    UrllibHttpClient,
    enroll_vin,
    get_subscription_feed,
    unenroll_vin,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> ProxyConfig:
    return ProxyConfig(
        producer_endpoint="https://api.example/prod",
        cms_subscription_id="sub-cms-telemetry-01",
    )


@pytest.fixture
def token_provider() -> StaticTokenProvider:
    return StaticTokenProvider(token="cms-subscriber-token-abc")


@pytest.fixture
def http_client() -> MockHttpClient:
    return MockHttpClient()


# ── ProxyConfig validation ───────────────────────────────────────────────

class TestConfig:
    """A malformed ProxyConfig must fail at construction, not at call time."""

    def test_rejects_empty_endpoint(self):
        with pytest.raises(ValueError, match="https://"):
            ProxyConfig(producer_endpoint="", cms_subscription_id="s")

    def test_rejects_http_endpoint(self):
        # Same rule as the DMS proxy's own https-only guard.
        with pytest.raises(ValueError, match="https://"):
            ProxyConfig(
                producer_endpoint="http://api.example/prod",
                cms_subscription_id="s",
            )

    def test_rejects_empty_subscription_id(self):
        with pytest.raises(ValueError, match="subscription_id"):
            ProxyConfig(
                producer_endpoint="https://api.example/prod",
                cms_subscription_id="",
            )

    def test_accepts_valid_config(self):
        c = ProxyConfig(
            producer_endpoint="https://api.example/prod",
            cms_subscription_id="sub-01",
        )
        assert c.producer_endpoint == "https://api.example/prod"


# ── get_subscription_feed ────────────────────────────────────────────────

class TestGetSubscriptionFeed:
    def test_happy_path_returns_producer_payload(self, config, token_provider, http_client):
        # Body is the live capture, not a guess. The previous version of this
        # test registered ``{"records": [{"vin": "VIN-A", "timestamp": ...}]}``,
        # which the real producer never returns: it omits ``count``, ``quota``,
        # ``vins_in_scope`` and ``unresolved_vins``, and its record has no
        # ``signals``. T2.1's validator now rejects that body, which is how the
        # invented mock was found.
        http_client.register(
            "GET",
            "/subscriptions/sub-cms-telemetry-01/records",
            200,
            LIVE_RECORDS_BODY,
        )
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 200
        assert result.body == LIVE_RECORDS_BODY

    def test_hits_correct_url(self, config, token_provider, http_client):
        http_client.register("GET", "/records", 200, LIVE_RECORDS_EMPTY_BODY)
        get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert http_client.calls[0]["url"] == (
            "https://api.example/prod/subscriptions/sub-cms-telemetry-01/records"
        )

    def test_producer_404_passes_through_as_404(self, config, token_provider, http_client):
        # Spec design: "not enrolled" is a normal state, not an error.
        http_client.register(
            "GET", "/records", 404, {"error": "subscription not found"}
        )
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 404
        assert result.body == {"error": "subscription not found"}

    def test_producer_401_maps_to_502_unauthorized(self, config, token_provider, http_client):
        # Producer rejecting CMS's subscriber credential is a CMS-side
        # config problem — 502, not 401 to the end user.
        http_client.register("GET", "/records", 401, {"error": "invalid token"})
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_PRODUCER_UNAUTHORIZED

    def test_producer_403_maps_to_502_unauthorized(self, config, token_provider, http_client):
        http_client.register("GET", "/records", 403, {"error": "forbidden"})
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_PRODUCER_UNAUTHORIZED

    def test_producer_500_maps_to_502_unavailable(self, config, token_provider, http_client):
        http_client.register("GET", "/records", 500, {"error": "internal"})
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_PRODUCER_UNAVAILABLE
        assert result.body["producer_status"] == 500

    def test_network_failure_maps_to_503(self, config, token_provider, http_client):
        # status == 0 is the ProducerHttpClient's network-failure marker.
        http_client.register("GET", "/records", 0, {"error": "URLError"})
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 503
        assert result.body["error"] == ERROR_PRODUCER_UNAVAILABLE
        # The specific network error type is preserved for diagnostics.
        assert result.body["detail"] == "URLError"

    def test_sends_bearer_auth_header(self, config, token_provider, http_client):
        http_client.register("GET", "/records", 200, {"records": []})
        get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert (
            http_client.calls[0]["headers"]["Authorization"]
            == "Bearer cms-subscriber-token-abc"
        )

    def test_sends_json_content_type(self, config, token_provider, http_client):
        http_client.register("GET", "/records", 200, {"records": []})
        get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert (
            http_client.calls[0]["headers"]["Content-Type"] == "application/json"
        )

    def test_no_body_on_get(self, config, token_provider, http_client):
        http_client.register("GET", "/records", 200, {"records": []})
        get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert http_client.calls[0]["body"] is None


# ── enroll_vin ───────────────────────────────────────────────────────────

class TestEnrollVin:
    def test_happy_path_sends_vin_in_body(self, config, token_provider, http_client):
        # 200 with ``added`` as a list — the live contract. The previous version
        # registered a 201 with ``{"vin": ..., "added": True}``: wrong status,
        # wrong type for ``added``, and missing ``subscription_id``,
        # ``already_present`` and ``scope_size`` entirely.
        http_client.register(
            "POST",
            "/subscriptions/sub-cms-telemetry-01/scope",
            200,
            LIVE_SCOPE_ADD_BODY,
        )
        result = enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 200
        assert http_client.calls[0]["method"] == "POST"
        assert http_client.calls[0]["body"] == {"vin": "ABCDEFGHJKLMNPRST"}

    def test_rejects_empty_vin_before_hitting_producer(
        self, config, token_provider, http_client
    ):
        result = enroll_vin(
            vin="",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 400
        assert http_client.calls == []  # Never hit the network.

    def test_rejects_non_string_vin(self, config, token_provider, http_client):
        result = enroll_vin(
            vin=None,  # type: ignore[arg-type]
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 400
        assert http_client.calls == []

    @pytest.mark.parametrize(
        "bad_vin",
        [
            "VIN?admin=true",   # URL query-metacharacter — truncates path
            "VIN#fragment",     # URL fragment metacharacter — truncates path
            "VIN%2Fadmin",      # URL-encoded slash — allow-list rejects percent
            "VIN with spaces",  # whitespace — parser-implementation-defined
            "VIN\r\nX",         # CRLF — closed by http.client but rejected here too
            "VIN.O.I.Q",        # ISO 3779 forbidden chars (O, I, Q)
            "V" * 18,           # exceeds 17-char VIN length
            "VIN/../admin",     # path-traversal (regressed the prior deny-list test)
        ],
    )
    def test_rejects_url_metacharacter_and_iso3779_violations(
        self, config, token_provider, http_client, bad_vin
    ):
        """Fix Group 1 — VIN allow-list catches URL-metacharacter injection
        that the prior deny-list of {'/', '..'} silently permitted. See
        security-review Cycle 1 Warning.
        """
        result = enroll_vin(
            vin=bad_vin,
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 400
        assert result.body["error"] == "vin format invalid"
        # Fail-closed at validation: no producer call for a bad VIN.
        assert http_client.calls == []

    def test_producer_500_maps_to_502(self, config, token_provider, http_client):
        http_client.register("POST", "/scope", 500, {})
        result = enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 502

    def test_producer_400_from_bad_cms_request_maps_to_502(
        self, config, token_provider, http_client
    ):
        # If CMS builds a malformed request the frontend should not see 400
        # — that would let a CMS bug read as a user error.
        http_client.register("POST", "/scope", 400, {"error": "missing field"})
        result = enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 502


# ── unenroll_vin ─────────────────────────────────────────────────────────

class TestUnenrollVin:
    def test_happy_path(self, config, token_provider, http_client):
        # 200 with a four-key body. The previous version registered a 204 with
        # an empty body — the producer's ``remove_handler`` returns
        # ``{subscription_id, vin, removed, scope_size}`` and never a 204, so
        # ``removed`` (the one field the UI needs to know whether anything
        # changed) was not modelled at all.
        http_client.register(
            "DELETE", "/scope/ABCDEFGHJKLMNPRST", 200, LIVE_SCOPE_REMOVE_BODY
        )
        result = unenroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 200
        assert http_client.calls[0]["method"] == "DELETE"
        assert http_client.calls[0]["url"].endswith("/scope/ABCDEFGHJKLMNPRST")

    @pytest.mark.parametrize(
        "bad_vin",
        [
            "VIN?admin=true",   # URL query-metacharacter (would truncate path)
            "VIN#frag",         # URL fragment (would truncate path)
            "VIN%2Fadmin",      # URL-encoded slash — allow-list rejects percent
            "VIN with spaces",  # whitespace
            "VIN\r\nX",         # CRLF
            "VIN/../admin",     # traditional path-traversal
            "..",               # traditional double-dot
            "VIN/OTHER",        # slash inside VIN
            "V" * 18,           # over 17 chars
            "",                 # empty
        ],
    )
    def test_rejects_url_metacharacter_and_traversal(
        self, config, token_provider, http_client, bad_vin
    ):
        """Fix Group 1 — VIN allow-list closes the ``?``/``#``/``%``/space/CRLF
        gap the prior deny-list left open. See security-review Cycle 1
        Warning. All rejected before URL construction; no producer call.
        """
        result = unenroll_vin(
            vin=bad_vin,
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 400
        assert result.body["error"] == "vin format invalid"
        assert http_client.calls == []

    def test_rejects_iso3779_forbidden_letters(
        self, config, token_provider, http_client
    ):
        # I, O, and Q are excluded from ISO 3779 to prevent visual confusion
        # with 1 and 0. A VIN containing any of them is malformed and must
        # not reach the producer.
        for bad in ["VINWITHIOQ", "VINWITHONLYO", "IIIIIIIII"]:
            result = unenroll_vin(
                vin=bad,
                config=config,
                token_provider=token_provider,
                http_client=http_client,
            )
            assert result.status_code == 400
            assert http_client.calls == []


# ── TokenProvider failure paths ──────────────────────────────────────────

class _FailingTokenProvider:
    def get_token(self) -> str:
        raise RuntimeError("Cognito unreachable")


class _EmptyTokenProvider:
    def get_token(self) -> str:
        return ""


class TestTokenProvider:
    def test_provider_exception_fails_closed(self, config, http_client):
        result = get_subscription_feed(
            config=config,
            token_provider=_FailingTokenProvider(),
            http_client=http_client,
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_CONFIG_MISSING
        # Never hit the network — no token, no call.
        assert http_client.calls == []

    def test_empty_token_fails_closed(self, config, http_client):
        result = enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=_EmptyTokenProvider(),
            http_client=http_client,
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_CONFIG_MISSING
        assert http_client.calls == []


# ── Authorization boundary (spec D2) ─────────────────────────────────────

class TestAuthorizationBoundary:
    """Structural, not stylistic. Spec D2 mandates CMS never forwards the
    end-user's JWT to the producer. The proxy signatures make that mistake
    impossible (they don't accept an end-user token), but this test class
    documents the invariant so a future edit that adds a ``caller_jwt``
    parameter fails a test rather than silently regressing D2.
    """

    def test_only_cms_subscriber_token_reaches_producer(
        self, config, token_provider, http_client
    ):
        http_client.register("GET", "/records", 200, {"records": []})
        get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        # The Authorization header carries CMS's static subscriber token
        # exactly — nothing derived from a hypothetical end-user context.
        assert (
            http_client.calls[0]["headers"]["Authorization"]
            == "Bearer cms-subscriber-token-abc"
        )

    def test_public_functions_do_not_accept_caller_jwt_kwarg(self):
        """A hostile refactor could add ``caller_jwt=`` and forward it. That
        signature change would be caught here (kwargs are keyword-only in
        the module's public API, so ``inspect.signature`` is a
        stable check for the invariant).
        """
        import inspect

        for fn in (get_subscription_feed, enroll_vin, unenroll_vin):
            params = inspect.signature(fn).parameters
            forbidden_names = {
                "caller_jwt",
                "user_jwt",
                "end_user_token",
                "authorization",
                "auth_header",
            }
            leaked = forbidden_names & set(params.keys())
            assert not leaked, (
                f"{fn.__name__} has parameters {leaked} that could carry "
                f"the end-user JWT to the producer — violates spec D2."
            )


# ── Cognito auth-flow contract (F1) ──────────────────────────────────────

class TestCognitoAuthFlowContract:
    """The auth flow this module commits to must actually be enabled on the
    pool client CMS's subscriber account authenticates against.

    F1 (2026-09-12): T1.3 chose ``AdminInitiateAuth`` +
    ``ADMIN_USER_PASSWORD_AUTH`` and recorded that "both mechanisms are
    supported by the current pool client". They are not —
    ``ALLOW_ADMIN_USER_PASSWORD_AUTH`` was never granted on
    ``CMSUserPoolClient``. Nothing in Group 1 could catch it: the token
    seam is a ``StaticTokenProvider``, so no test ever spoke to Cognito,
    and T1.3's own Verify checked that its citation resolved to a real
    line — which it did. The citation was accurate; the inference from it
    was wrong.

    These two tests are the executable form of that decision, so a revert
    in either file fails here instead of on the first live call.
    """

    _UI_STACK = os.path.join(
        _REPO_ROOT, "deployment", "stacks", "ui_stack.py"
    )

    @staticmethod
    def _cms_pool_client_auth_flags(source: str) -> set:
        """Return the ``cognito.AuthFlow(...)`` kwargs set True on the
        ``CMSUserPoolClient`` construct.

        Raises rather than returning an empty set when the construct or its
        ``auth_flows=`` block cannot be located — an unparseable file must
        fail this test loudly, not pass it vacuously.
        """
        anchor = source.find('"CMSUserPoolClient"')
        if anchor == -1:
            raise AssertionError(
                "Could not locate the CMSUserPoolClient construct in "
                "ui_stack.py. If it was renamed, this guard needs updating "
                "— do not delete it."
            )
        marker = "auth_flows=cognito.AuthFlow("
        start = source.find(marker, anchor)
        if start == -1:
            raise AssertionError(
                "CMSUserPoolClient no longer declares "
                "'auth_flows=cognito.AuthFlow(' — the client's enabled auth "
                "flows can no longer be read from source. Re-point this "
                "guard before trusting it."
            )
        # Walk to the matching close paren of AuthFlow(.
        depth, i = 0, start + len(marker) - 1
        while i < len(source):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        else:  # pragma: no cover — unbalanced parens in a .py file
            raise AssertionError("Unbalanced parens in the AuthFlow block.")

        block = source[start + len(marker):i]
        flags = set(re.findall(r"(\w+)\s*=\s*True", block))
        if not flags:
            raise AssertionError(
                f"Parsed the AuthFlow block but found no enabled flags in "
                f"{block!r} — the parser is broken or the block now sets "
                f"flows some other way. Failing rather than passing empty."
            )
        return flags

    def test_committed_auth_flow_is_enabled_on_cms_pool_client(self):
        with open(self._UI_STACK, encoding="utf-8") as fh:
            flags = self._cms_pool_client_auth_flags(fh.read())

        # Positive control: proves the parser reads real content, so the
        # assertion below cannot pass on an empty or bogus parse.
        assert "user_srp" in flags, (
            f"Expected the long-standing user_srp flow in the parsed set, got "
            f"{flags}. Parser is suspect — fix it before trusting the next "
            f"assertion."
        )

        assert COGNITO_AUTH_FLOW_CDK_KWARG in flags, (
            f"connected_services_proxy commits to Cognito AuthFlow "
            f"'{COGNITO_AUTH_FLOW}', which needs "
            f"cognito.AuthFlow({COGNITO_AUTH_FLOW_CDK_KWARG}=True) on "
            f"CMSUserPoolClient. ui_stack.py currently enables {flags}. "
            f"Either re-enable it there, or change COGNITO_AUTH_FLOW to a "
            f"flow that IS enabled — but do not leave the two disagreeing: "
            f"the proxy would fail with InvalidParameterException on its "
            f"first live token acquisition, at demo time."
        )

    def test_the_two_constants_cannot_disagree(self):
        """`COGNITO_AUTH_FLOW_CDK_KWARG` must be the kwarg for
        `COGNITO_AUTH_FLOW`, whatever route produced the value.

        Review of FG2.1 raised this as mutation M7: with the two constants
        declared independently, setting the flow to `USER_PASSWORD_AUTH` and
        the kwarg to `user_srp` passed every test, because both flows are
        enabled on this client so the ui_stack assertion still held. The
        constants are now derived from one mapping, which makes that state
        unrepresentable *by accident* — but an edit that replaces the
        derivation with a literal would reopen it, so the invariant is
        asserted on the values rather than on the source that produced them.
        """
        from connected_services_proxy import _AVAILABLE_FLOW_CDK_KWARGS

        assert COGNITO_AUTH_FLOW in _AVAILABLE_FLOW_CDK_KWARGS, (
            f"COGNITO_AUTH_FLOW={COGNITO_AUTH_FLOW!r} is not a flow known to be "
            f"available on CMSUserPoolClient (known: "
            f"{sorted(_AVAILABLE_FLOW_CDK_KWARGS)}). If a new flow was enabled "
            f"on the client, add it to the mapping in the same edit."
        )
        assert COGNITO_AUTH_FLOW_CDK_KWARG == _AVAILABLE_FLOW_CDK_KWARGS[
            COGNITO_AUTH_FLOW
        ], (
            f"COGNITO_AUTH_FLOW={COGNITO_AUTH_FLOW!r} pairs with CDK kwarg "
            f"{_AVAILABLE_FLOW_CDK_KWARGS[COGNITO_AUTH_FLOW]!r}, but "
            f"COGNITO_AUTH_FLOW_CDK_KWARG is {COGNITO_AUTH_FLOW_CDK_KWARG!r}. "
            f"The ui_stack assertion in this class checks the kwarg, so a "
            f"mismatched pair would verify the wrong flow's availability."
        )

    def test_module_does_not_reach_for_the_admin_auth_flow(self):
        """Negative control for the specific regression F1 was.

        ``ADMIN_USER_PASSWORD_AUTH`` is not enabled on the pool client, so
        any *use* of it (or of ``admin_initiate_auth``) in this module is
        either dead code or a live bug.

        Comments and docstrings are exempt, and precisely so: the whole
        point of the ``COGNITO_AUTH_FLOW`` docstring is to name both
        forbidden identifiers and explain why neither is used. Exempting
        them by *syntactic role* (via ``ast``) rather than by matching
        prohibitive phrasing means a real use in code still fails even if
        someone writes it on a line that reads like a warning.
        """
        with open(connected_services_proxy.__file__, encoding="utf-8") as fh:
            source = fh.read()

        docstring_lines = set()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstring_lines.update(
                    range(first.lineno, (first.end_lineno or first.lineno) + 1)
                )

        # Positive control: the COGNITO_AUTH_FLOW rationale names both
        # forbidden identifiers in comments/docstrings. If the exemption
        # logic swallowed the whole file, this would be 0 and every
        # assertion below would pass vacuously.
        exempted_mentions = sum(
            1
            for n, line in enumerate(source.splitlines(), start=1)
            if ("admin_initiate_auth" in line or "ADMIN_USER_PASSWORD_AUTH" in line)
            and (n in docstring_lines or line.lstrip().startswith("#"))
        )
        assert exempted_mentions >= 2, (
            f"Expected the COGNITO_AUTH_FLOW rationale to still name both "
            f"forbidden identifiers in prose (found {exempted_mentions} "
            f"exempted mentions). If the rationale was deleted, restore it — "
            f"it is the record of why this guard exists."
        )

        for forbidden in ("ADMIN_USER_PASSWORD_AUTH", "admin_initiate_auth"):
            used_in_code = [
                f"{n}: {line.strip()}"
                for n, line in enumerate(source.splitlines(), start=1)
                if forbidden in line
                and n not in docstring_lines
                and not line.lstrip().startswith("#")
            ]
            assert not used_in_code, (
                f"{forbidden} is used in executable code: {used_in_code}. "
                f"That flow is NOT enabled on CMSUserPoolClient — the call "
                f"would fail with InvalidParameterException at runtime. See "
                f"COGNITO_AUTH_FLOW."
            )


# ── ProxyResult -> Lambda response shape ─────────────────────────────────

class TestProxyResult:
    def test_to_lambda_response_wraps_body_as_json_string(self):
        # index.py returns {'statusCode': int, 'headers': dict, 'body': str}
        # — the body MUST be a JSON string, not a dict, for API Gateway.
        r = ProxyResult(status_code=200, body={"records": [1, 2, 3]})
        resp = r.to_lambda_response(cors_headers={"Access-Control-Allow-Origin": "*"})
        assert resp["statusCode"] == 200
        # T2.3 / security-review SG5 changed this contract deliberately: the
        # response now carries three security headers in addition to whatever
        # CORS headers the caller passes. This assertion previously required
        # EXACT equality with the caller's dict, which was the correct pin at
        # T2.1 (nothing else was stamped then) and would now forbid the fix.
        # Rewritten to pin both halves of the new contract rather than relaxed
        # to a subset check: the CORS header must survive, and the three
        # security headers must be present with these exact values.
        assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
        assert resp["headers"]["Cache-Control"] == "no-store"
        assert resp["headers"]["X-Content-Type-Options"] == "nosniff"
        assert resp["headers"]["Content-Type"] == "application/json"
        assert set(resp["headers"]) == {
            "Access-Control-Allow-Origin",
            "Cache-Control",
            "X-Content-Type-Options",
            "Content-Type",
        }
        # Body is a string, and re-parseable as JSON.
        import json as _j
        assert _j.loads(resp["body"]) == {"records": [1, 2, 3]}

    def test_security_headers_win_over_a_conflicting_caller_header(self):
        """A caller cannot weaken the invariant by passing its own value.

        `SECURITY_HEADERS` is applied last, so a `cors_headers` dict that
        happened to carry `Cache-Control: max-age=300` — from a copy-paste, or
        from a future caller that caches something else — cannot silently
        re-enable storage of a per-operator feed or a stale 502.
        """
        r = ProxyResult(status_code=502, body={"error": "producer_unavailable"})
        resp = r.to_lambda_response(
            cors_headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "max-age=300",
            }
        )
        assert resp["headers"]["Cache-Control"] == "no-store"


# ── UrllibHttpClient smoke check (does NOT hit the network) ──────────────

class TestUrllibHttpClientSmoke:
    """The real client is present in Group 1 for T2.1 to swap in, but no
    Group 1 code path dispatches to it. This test only verifies the class
    is importable and its ``request`` method signature matches the protocol
    — never opens a socket.
    """

    def test_class_matches_protocol(self):
        client = UrllibHttpClient()
        import inspect

        sig = inspect.signature(client.request)
        assert set(sig.parameters.keys()) >= {
            "method",
            "url",
            "headers",
            "body",
            "timeout",
        }



# ── CognitoUserPasswordTokenProvider (T2.0a) ─────────────────────────────

class _FakeCognito:
    """Programmable ``cognito-idp`` double. Records every call."""

    def __init__(self, responses):
        # responses: list of dicts to return in order, or Exception to raise
        self._responses = list(responses)
        self.calls = []

    def initiate_auth(self, **kwargs):
        self.calls.append(kwargs)
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _auth_ok(id_token="id-tok", expires_in=3600, refresh="refresh-tok"):
    result = {"IdToken": id_token, "ExpiresIn": expires_in,
              "AccessToken": "access-tok-DO-NOT-USE"}
    if refresh is not None:
        result["RefreshToken"] = refresh
    return {"AuthenticationResult": result}


def _provider(cognito, password="pw-correct-horse"):
    return CognitoUserPasswordTokenProvider(
        user_pool_client_id="client-123",
        username="cms-staging-subscriber",
        password_provider=lambda: password,
        cognito_client=cognito,
    )


class TestCognitoTokenProvider:
    """T2.0a. The token seam's real implementation.

    Not live-verified — T2.1 does that against the deployed producer. What is
    verified here is every decision that would otherwise be discovered as a
    401 or a 504 at demo time.
    """

    def test_uses_the_committed_auth_flow(self):
        c = _FakeCognito([_auth_ok()])
        _provider(c).get_token()
        assert c.calls[0]["AuthFlow"] == COGNITO_AUTH_FLOW

    def test_returns_the_id_token_not_the_access_token(self):
        """The producer's authorizer is COGNITO_USER_POOLS and validates
        `aud`, which only the IdToken carries. Returning the AccessToken
        yields a 401 from API Gateway before the producer's handler runs.
        """
        c = _FakeCognito([_auth_ok(id_token="the-id-token")])
        token = _provider(c).get_token()
        assert token == "the-id-token"
        assert token != "access-tok-DO-NOT-USE"

    def test_caches_within_validity_and_does_not_re_auth(self):
        c = _FakeCognito([_auth_ok()])
        p = _provider(c)
        assert p.get_token() == p.get_token() == "id-tok"
        assert len(c.calls) == 1, "second get_token must not re-authenticate"

    def test_refreshes_before_expiry_using_refresh_flow(self):
        # expires_in inside the refresh margin => the next call must refresh.
        c = _FakeCognito([
            _auth_ok(expires_in=TOKEN_REFRESH_MARGIN_SECONDS - 1),
            _auth_ok(id_token="id-tok-2", refresh=None),
        ])
        p = _provider(c)
        assert p.get_token() == "id-tok"
        assert p.get_token() == "id-tok-2"
        assert c.calls[1]["AuthFlow"] == "REFRESH_TOKEN_AUTH"
        assert c.calls[1]["AuthParameters"] == {"REFRESH_TOKEN": "refresh-tok"}

    def test_refresh_token_is_retained_when_refresh_response_omits_it(self):
        """REFRESH_TOKEN_AUTH does not re-issue a refresh token. Clearing it
        on a refresh would force a full password re-auth on every subsequent
        expiry — working, but needlessly reading the secret each hour.
        """
        c = _FakeCognito([
            _auth_ok(expires_in=1),
            _auth_ok(id_token="t2", expires_in=1, refresh=None),
            _auth_ok(id_token="t3", expires_in=1, refresh=None),
        ])
        p = _provider(c)
        p.get_token(); p.get_token(); p.get_token()
        assert [c["AuthFlow"] for c in c.calls] == [
            COGNITO_AUTH_FLOW, "REFRESH_TOKEN_AUTH", "REFRESH_TOKEN_AUTH"
        ]

    def test_falls_back_to_full_auth_when_refresh_is_rejected(self):
        """A 30-day-old refresh token is the expected state after an idle
        period, not an error — it must not surface to the caller.
        """
        c = _FakeCognito([
            _auth_ok(expires_in=1),
            Exception("NotAuthorizedException"),
            _auth_ok(id_token="fresh"),
        ])
        p = _provider(c)
        p.get_token()
        assert p.get_token() == "fresh"
        assert [x["AuthFlow"] for x in c.calls] == [
            COGNITO_AUTH_FLOW, "REFRESH_TOKEN_AUTH", COGNITO_AUTH_FLOW
        ]

    def test_challenge_response_raises_with_the_remediation(self):
        """The producer creates subscribers with MessageAction=SUPPRESS, so a
        fresh account is in FORCE_CHANGE_PASSWORD and InitiateAuth returns a
        challenge with NO tokens. Failing with the fix named beats a 401
        three services away.
        """
        c = _FakeCognito([{"ChallengeName": "NEW_PASSWORD_REQUIRED"}])
        with pytest.raises(TokenAcquisitionError) as ei:
            _provider(c).get_token()
        msg = str(ei.value)
        assert "NEW_PASSWORD_REQUIRED" in msg
        assert "AdminSetUserPassword(Permanent=True)" in msg

    def test_missing_id_token_fails_closed_and_says_why(self):
        c = _FakeCognito([{"AuthenticationResult": {"AccessToken": "a"}}])
        with pytest.raises(TokenAcquisitionError) as ei:
            _provider(c).get_token()
        assert "IdToken" in str(ei.value)

    def test_never_returns_empty_string_on_failure(self):
        """A provider that returned "" would send an empty Bearer header and
        surface as a producer 401 — the failure must not be silent.
        """
        c = _FakeCognito([Exception("boom")])
        with pytest.raises(TokenAcquisitionError):
            _provider(c).get_token()


class TestCognitoTokenProviderSecrecy:
    """The password must not reach an exception, a log, or a repr.

    Group 1's security review asked for exactly this before the real provider
    was wired: boto3's own ClientError does not echo the password, but a
    well-meaning wrapping layer interpolating the exception would.
    """

    _SECRET = "SuperSecret-Passw0rd-DoNotLeak"

    def test_password_absent_from_exception_message_and_chain(self):
        c = _FakeCognito([Exception(f"request was: PASSWORD={self._SECRET}")])
        with pytest.raises(TokenAcquisitionError) as ei:
            _provider(c, password=self._SECRET).get_token()
        # The message must not carry it, and `from None` must have severed the
        # __cause__ chain so a traceback printer cannot reach the original.
        assert self._SECRET not in str(ei.value)
        assert ei.value.__cause__ is None, (
            "raise ... from None is load-bearing here: the underlying "
            "exception's message contains the password."
        )

    def test_password_absent_from_repr(self):
        p = _provider(_FakeCognito([_auth_ok()]), password=self._SECRET)
        p.get_token()
        assert self._SECRET not in repr(p)
        assert "id-tok" not in repr(p), "the token itself should not be in repr either"

    def test_password_not_logged(self, caplog):
        import logging

        caplog.set_level(logging.DEBUG)
        c = _FakeCognito([
            _auth_ok(expires_in=1),
            Exception("NotAuthorizedException"),
            _auth_ok(),
        ])
        p = _provider(c, password=self._SECRET)
        p.get_token()
        p.get_token()  # exercises the refresh-failure log line
        assert self._SECRET not in caplog.text



class TestCognitoTokenProviderHardening:
    """Security-review T2.0a suggestions 2 and 3, made executable."""

    _SECRET = "SuperSecret-Passw0rd-DoNotLeak"

    def test_password_absent_from_frame_locals_in_the_traceback(self):
        """`from None` keeps the password out of the exception chain, but the
        failing frame's locals still held it in `auth_parameters` until the
        `finally` clears them. A formatter reading locals at format time is a
        real exposure path — error reporters do exactly this.
        """
        import traceback

        c = _FakeCognito([Exception("upstream boom")])
        try:
            _provider(c, password=self._SECRET).get_token()
        except TokenAcquisitionError as e:
            rendered = "".join(
                traceback.TracebackException.from_exception(
                    e, capture_locals=True
                ).format()
            )
        else:
            pytest.fail("expected TokenAcquisitionError")

        assert self._SECRET not in rendered, (
            "the password survived into a locals-capturing traceback — the "
            "`finally: auth_parameters.pop('PASSWORD')` in _auth is what "
            "prevents this"
        )

    def test_zero_expires_in_is_honoured_not_rewritten_to_an_hour(self):
        """`get("ExpiresIn") or 3600` would turn a Cognito-issued 0 into an
        hour of confident reuse of a token that is already invalid. `0` must
        mean 0.
        """
        c = _FakeCognito([
            {"AuthenticationResult": {"IdToken": "t1", "ExpiresIn": 0,
                                      "RefreshToken": "r1"}},
            _auth_ok(id_token="t2", refresh=None),
        ])
        p = _provider(c)
        assert p.get_token() == "t1"
        # Already past the margin, so the next call must not serve t1 again.
        assert p.get_token() == "t2"
        assert len(c.calls) == 2

    def test_absent_expires_in_still_defaults(self):
        c = _FakeCognito([{"AuthenticationResult": {"IdToken": "t1"}}])
        p = _provider(c)
        assert p.get_token() == "t1"
        assert p._expires_at > time.time() + 3000, "absent ExpiresIn should default to ~1h"



# ── T2.1: response-shape contracts against the live capture ───────────────

class TestLiveShapeContractsAcceptRealResponses:
    """Positive controls. Each validator must accept the real captured body.

    These exist because a shape validator whose only tests are negative can
    pass while rejecting everything — including what the producer actually
    sends. Asserting the live body verbatim is the check that the contract
    describes reality rather than merely being self-consistent.
    """

    def test_records_empty_scope(self):
        connected_services_proxy.validate_records_shape(LIVE_RECORDS_EMPTY_BODY)

    def test_records_non_empty_scope(self):
        connected_services_proxy.validate_records_shape(LIVE_RECORDS_BODY)

    def test_records_capture_is_not_vacuous(self):
        """A capture with zero records could not exercise the per-record checks.

        The non-empty capture must actually contain records, or
        ``test_records_non_empty_scope`` above is asserting nothing about the
        record contract it exists to pin.
        """
        assert LIVE_RECORDS_BODY["count"] > 0, (
            "the 'scope non-empty' capture has no records, so the per-record "
            "key and signals-type assertions are unreachable — re-capture "
            "against a VIN that has telemetry"
        )
        assert LIVE_RECORDS_BODY["unresolved_vins"], (
            "the capture has no unresolved VIN, so the mixed "
            "resolved/unresolved case the UI must render is unmodelled"
        )

    def test_scope_add(self):
        connected_services_proxy.validate_scope_add_shape(LIVE_SCOPE_ADD_BODY)

    def test_scope_add_idempotent_repeat(self):
        connected_services_proxy.validate_scope_add_shape(
            LIVE_SCOPE_ADD_REPEAT_BODY
        )

    def test_scope_remove(self):
        connected_services_proxy.validate_scope_remove_shape(
            LIVE_SCOPE_REMOVE_BODY
        )

    def test_scope_remove_idempotent_repeat(self):
        """``removed: false`` is a bool, and must not be mistaken for absence."""
        assert LIVE_SCOPE_REMOVE_REPEAT_BODY["removed"] is False
        connected_services_proxy.validate_scope_remove_shape(
            LIVE_SCOPE_REMOVE_REPEAT_BODY
        )


class TestLiveShapeContractsRejectDrift:
    """Negative controls, one per required key, derived from the capture.

    Parametrized off the live body's own keys rather than a hand-listed set:
    a key added to the producer contract and to the capture is automatically
    covered, instead of being covered only if someone remembered to add a case.
    """

    @pytest.mark.parametrize("key", sorted(LIVE_RECORDS_BODY))
    def test_records_missing_any_required_key_is_drift(self, key):
        body = {k: v for k, v in LIVE_RECORDS_BODY.items() if k != key}
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_records_shape(body)
        assert key in str(exc.value)

    @pytest.mark.parametrize("key", sorted(LIVE_SCOPE_ADD_BODY))
    def test_scope_add_missing_any_required_key_is_drift(self, key):
        body = {k: v for k, v in LIVE_SCOPE_ADD_BODY.items() if k != key}
        with pytest.raises(connected_services_proxy.ShapeDrift):
            connected_services_proxy.validate_scope_add_shape(body)

    @pytest.mark.parametrize("key", sorted(LIVE_SCOPE_REMOVE_BODY))
    def test_scope_remove_missing_any_required_key_is_drift(self, key):
        body = {k: v for k, v in LIVE_SCOPE_REMOVE_BODY.items() if k != key}
        with pytest.raises(connected_services_proxy.ShapeDrift):
            connected_services_proxy.validate_scope_remove_shape(body)

    def test_records_count_disagreeing_with_len_is_drift(self):
        """The card renders ``count``; if it disagrees, the card lies."""
        body = dict(LIVE_RECORDS_BODY)
        body["count"] = body["count"] + 1
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_records_shape(body)
        assert "disagrees" in str(exc.value)

    def test_records_wrong_container_type_is_drift(self):
        body = dict(LIVE_RECORDS_BODY)
        body["records"] = {"0": body["records"][0]}  # object where list expected
        with pytest.raises(connected_services_proxy.ShapeDrift):
            connected_services_proxy.validate_records_shape(body)

    def test_record_missing_signals_is_drift(self):
        body = dict(LIVE_RECORDS_BODY)
        first = {k: v for k, v in body["records"][0].items() if k != "signals"}
        body["records"] = [first]
        body["count"] = 1
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_records_shape(body)
        assert "signals" in str(exc.value)

    def test_record_signals_not_an_object_is_drift(self):
        body = dict(LIVE_RECORDS_BODY)
        first = dict(body["records"][0])
        first["signals"] = ["engine_temp_c", 82.1]  # list where object expected
        body["records"] = [first]
        body["count"] = 1
        with pytest.raises(connected_services_proxy.ShapeDrift):
            connected_services_proxy.validate_records_shape(body)

    def test_drift_in_a_later_record_is_caught_not_only_the_first(self):
        """Validation must walk every record, not sample the first one.

        A producer that changes shape partway through a page — a
        differently-serialized record for an unresolved VIN, say — would slip
        past a first-record-only check for exactly the rows the operator most
        needs to see.
        """
        body = dict(LIVE_RECORDS_BODY)
        good = dict(body["records"][0])
        bad = {k: v for k, v in good.items() if k != "vin"}
        body["records"] = [good, good, bad]
        body["count"] = 3
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_records_shape(body)
        assert "records[2]" in str(exc.value)

    def test_removed_as_int_is_drift_not_a_truthy_bool(self):
        """``removed: 1`` is not ``removed: true``.

        ``isinstance(True, int)`` is true in Python, so a type check written the
        obvious way would accept an int here and a bool in ``scope_size``. The
        UI branches on ``removed``; an int that happens to be truthy today is a
        contract that can silently become ``removed: 2``.
        """
        body = dict(LIVE_SCOPE_REMOVE_BODY)
        body["removed"] = 1
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_scope_remove_shape(body)
        assert "bool" in str(exc.value)

    def test_scope_size_as_bool_is_drift_not_an_int(self):
        body = dict(LIVE_SCOPE_REMOVE_BODY)
        body["scope_size"] = True
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_scope_remove_shape(body)
        assert "int" in str(exc.value)

    def test_added_as_bool_is_drift(self):
        """The exact shape T1.4's mock invented: ``added: True``."""
        body = dict(LIVE_SCOPE_ADD_BODY)
        body["added"] = True
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_scope_add_shape(body)
        assert "list" in str(exc.value)

    @pytest.mark.parametrize("not_an_object", [[], "records", 7, None])
    def test_non_object_body_is_drift(self, not_an_object):
        with pytest.raises(connected_services_proxy.ShapeDrift):
            connected_services_proxy.validate_records_shape(not_an_object)

    def test_drift_message_names_keys_and_types_never_values(self):
        """The drift reason reaches the frontend; it must not carry payload.

        A message that interpolated the offending value would put producer
        telemetry — position, VIN — into a CMS error body and into CloudWatch.
        """
        body = dict(LIVE_RECORDS_BODY)
        body["quota"] = "SENSITIVE-CANARY-VALUE"
        with pytest.raises(connected_services_proxy.ShapeDrift) as exc:
            connected_services_proxy.validate_records_shape(body)
        assert "SENSITIVE-CANARY-VALUE" not in str(exc.value)
        assert "quota" in str(exc.value)


class TestShapeDriftFailsClosedThroughTheProxy:
    """End-to-end: drift becomes a 502 with a distinct marker, not a 200."""

    def test_feed_drift_returns_502_shape_drift(
        self, config, token_provider, http_client
    ):
        drifted = {k: v for k, v in LIVE_RECORDS_BODY.items() if k != "records"}
        http_client.register("GET", "/records", 200, drifted)
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_PRODUCER_SHAPE_DRIFT

    def test_drifted_body_is_not_passed_through_to_the_caller(
        self, config, token_provider, http_client
    ):
        """The half-understood payload must not reach the frontend.

        Returning it alongside the error would let a UI written defensively
        render partial data anyway, which is the outcome failing closed exists
        to prevent.
        """
        drifted = dict(LIVE_RECORDS_BODY)
        drifted.pop("quota")
        http_client.register("GET", "/records", 200, drifted)
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert "records" not in result.body
        assert set(result.body) == {"error", "detail"}

    def test_enroll_drift_returns_502_shape_drift(
        self, config, token_provider, http_client
    ):
        http_client.register("POST", "/scope", 200, {"added": True})
        result = enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_PRODUCER_SHAPE_DRIFT

    def test_unenroll_drift_returns_502_shape_drift(
        self, config, token_provider, http_client
    ):
        http_client.register("DELETE", "/scope/ABCDEFGHJKLMNPRST", 204, {})
        result = unenroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 502
        assert result.body["error"] == ERROR_PRODUCER_SHAPE_DRIFT

    def test_non_2xx_is_not_validated_as_a_success_body(
        self, config, token_provider, http_client
    ):
        """A 404's body must not be judged against the success contract.

        Otherwise the normal "not enrolled" state would surface as shape drift
        and the UI would show an error where it should show an enroll button.
        """
        http_client.register(
            "GET", "/records", 404, {"error": "Subscription not found"}
        )
        result = get_subscription_feed(
            config=config, token_provider=token_provider, http_client=http_client
        )
        assert result.status_code == 404
        assert result.body == {"error": "Subscription not found"}


# ── T2.1: the VIN contract must match the producer's, exactly ─────────────

class TestVinContractMatchesProducer:
    """Pins this proxy's VIN rule to the producer's committed regex.

    Read from the producer's source rather than restated, because the failure
    this guards is a *divergence* — and a divergence cannot be detected by a
    constant that only this side declares. T1.4 declared ``{1,17}`` on the
    stated guess that the producer accepted shorter VINs; the producer requires
    exactly 17, so every 1-16 char VIN became a producer 400 that this proxy
    reported as a 502 ``producer_unavailable``.
    """

    PRODUCER_HANDLER = os.path.join(
        _REPO_ROOT,
        "services",
        "connectors",
        "subscriptions",
        "subscription_scope",
        "handler.py",
    )

    def _producer_vin_pattern(self) -> str:
        assert os.path.isfile(self.PRODUCER_HANDLER), (
            f"producer handler not found at {self.PRODUCER_HANDLER}; this test "
            f"cannot verify the VIN contract and must not pass vacuously"
        )
        with open(self.PRODUCER_HANDLER, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_VIN_RE" not in targets:
                continue
            # `_VIN_RE = re.compile(r"...", re.IGNORECASE)` — first arg.
            call = node.value
            assert isinstance(call, ast.Call), "_VIN_RE is not a re.compile call"
            first = call.args[0]
            assert isinstance(first, ast.Constant), "_VIN_RE pattern is not a literal"
            return first.value
        raise AssertionError(
            f"_VIN_RE not found in {self.PRODUCER_HANDLER}; the producer's VIN "
            f"contract cannot be read, so this proxy's rule is unverified"
        )

    def test_pattern_is_character_for_character_the_producers(self):
        assert (
            connected_services_proxy._VIN_ALLOWED.pattern
            == self._producer_vin_pattern()
        ), (
            "this proxy's VIN allow-list has diverged from the producer's "
            "_VIN_RE; a VIN accepted here and rejected there surfaces as a 502 "
            "producer_unavailable rather than a 400"
        )

    @pytest.mark.parametrize("length", [1, 5, 16, 18, 34])
    def test_wrong_length_is_rejected_here_as_it_is_there(self, length):
        candidate = "A" * length
        assert connected_services_proxy._normalize_vin(candidate) is None
        assert not re.match(self._producer_vin_pattern(), candidate)

    def test_seventeen_is_accepted_both_sides(self):
        candidate = "ABCDEFGHJKLMNPRST"
        assert connected_services_proxy._normalize_vin(candidate) == candidate
        assert re.match(self._producer_vin_pattern(), candidate)

    def test_lowercase_is_normalized_not_rejected(self):
        """The producer does ``strip().upper()``; so must this side.

        Rejecting a lowercase VIN here that the producer would accept made the
        two sides disagree about the same input — the proxy was *stricter* than
        the service it fronts.
        """
        assert (
            connected_services_proxy._normalize_vin("abcdefghjklmnprst")
            == "ABCDEFGHJKLMNPRST"
        )

    def test_surrounding_whitespace_is_stripped_not_rejected(self):
        assert (
            connected_services_proxy._normalize_vin("  ABCDEFGHJKLMNPRST\t")
            == "ABCDEFGHJKLMNPRST"
        )

    def test_normalized_form_is_what_goes_on_the_wire(
        self, config, token_provider, http_client
    ):
        """The producer stores the uppercased VIN, so DELETE must send that.

        Sending the caller's lowercase form would target a member that is not in
        the set, and the producer would honestly answer ``removed: false`` for a
        VIN that is in fact enrolled.
        """
        http_client.register(
            "DELETE", "/scope/ABCDEFGHJKLMNPRST", 200, LIVE_SCOPE_REMOVE_BODY
        )
        result = unenroll_vin(
            vin="abcdefghjklmnprst",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert result.status_code == 200
        assert http_client.calls[0]["url"].endswith("/scope/ABCDEFGHJKLMNPRST")

    def test_enroll_sends_the_normalized_vin_in_the_body(
        self, config, token_provider, http_client
    ):
        http_client.register("POST", "/scope", 200, LIVE_SCOPE_ADD_BODY)
        enroll_vin(
            vin=" abcdefghjklmnprst ",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert http_client.calls[0]["body"] == {"vin": "ABCDEFGHJKLMNPRST"}

    @pytest.mark.parametrize("excluded", ["I", "O", "Q"])
    def test_iso_3779_excluded_letters_still_rejected(self, excluded):
        candidate = (excluded + "BCDEFGHJKLMNPRST")[:17]
        assert connected_services_proxy._normalize_vin(candidate) is None


# ── T2.1: env-var wiring ─────────────────────────────────────────────────

class TestProxyConfigFromEnv:
    def test_builds_from_both_vars(self):
        config = ProxyConfig.from_env(
            {
                connected_services_proxy.ENV_PRODUCER_ENDPOINT: "https://p/prod",
                connected_services_proxy.ENV_CMS_SUBSCRIPTION_ID: "sub-1",
            }
        )
        assert config.producer_endpoint == "https://p/prod"
        assert config.cms_subscription_id == "sub-1"

    @pytest.mark.parametrize(
        "omit",
        ["CS_PRODUCER_API_ENDPOINT", "CS_SUBSCRIPTION_ID"],
    )
    def test_missing_var_raises_naming_the_variable(self, omit):
        env = {
            connected_services_proxy.ENV_PRODUCER_ENDPOINT: "https://p/prod",
            connected_services_proxy.ENV_CMS_SUBSCRIPTION_ID: "sub-1",
        }
        env.pop(omit)
        with pytest.raises(KeyError) as exc:
            ProxyConfig.from_env(env)
        assert omit in str(exc.value)

    def test_does_not_read_the_cs_portal_spec_variable(self):
        """``CONNECTED_SERVICES_API_ENDPOINT`` belongs to another spec.

        It is set in ``deployment/config/staging.env`` for
        ``2026-09-03-cms-connected-services-portal`` — currently to the
        placeholder ``https://api.example.invalid``. If this proxy ever read it,
        that placeholder would become this spec's producer endpoint and the feed
        would fail with a DNS error attributed to the wrong owner.
        """
        assert (
            connected_services_proxy.ENV_PRODUCER_ENDPOINT
            != "CONNECTED_SERVICES_API_ENDPOINT"
        )
        source_path = os.path.join(_HANDLER_DIR, "connected_services_proxy.py")
        with open(source_path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        offenders = {
            literal
            for literal in literals
            if literal == "CONNECTED_SERVICES_API_ENDPOINT"
        }
        assert not offenders, (
            "connected_services_proxy.py references another spec's env var "
            "as a string literal"
        )

    def test_non_https_endpoint_from_env_still_fails_closed(self):
        with pytest.raises(ValueError):
            ProxyConfig.from_env(
                {
                    connected_services_proxy.ENV_PRODUCER_ENDPOINT: "http://p/prod",
                    connected_services_proxy.ENV_CMS_SUBSCRIPTION_ID: "sub-1",
                }
            )


# ── T2.1: live producer integration (opt-in) ─────────────────────────────

_LIVE_OPT_IN = os.environ.get("CS_LIVE_INTEGRATION") == "1"


@pytest.mark.integration
@pytest.mark.skipif(
    not _LIVE_OPT_IN,
    reason="set CS_LIVE_INTEGRATION=1 (with AWS credentials) to hit real staging",
)
class TestLiveProducerIntegration:
    """Runs the real transport against the deployed producer.

    Opt-in rather than default: the unit suite must stay runnable with no AWS
    credentials and no network. What this adds over the unit tests is the one
    thing they cannot establish — that the validators accept what the *service*
    returns right now, not what a fixture recorded earlier. The fixture is a
    snapshot; this is the check that the snapshot is still true.

    Real staging identifiers (endpoint, pool client id) are read from the
    live-shape capture under ``.kiro/`` rather than written here, because
    ``.kiro/`` is ``.publish-exclude``d and this file ships. Sibling precedent
    for why that distinction is load-bearing: open issue
    ``2026-09-10-admin-provision-subscriber-real-pool-id-in-docstring``.
    """

    def _config_and_token(self):
        import boto3

        region = "us-west-2"
        account = boto3.client("sts").get_caller_identity()["Account"]
        secret_id = (
            f"cms-staging-connected-services-subscriber-{region}-{account}"
        )
        secret = json.loads(
            boto3.client("secretsmanager", region_name=region)
            .get_secret_value(SecretId=secret_id)["SecretString"]
        )
        endpoint = _LIVE["producer_endpoint"]
        config = ProxyConfig(
            producer_endpoint=endpoint,
            cms_subscription_id=secret["subscription_id"],
        )
        # Unpack the password into a local and close over *that*, not over the
        # whole secret dict (security review SG2). A lambda capturing `secret`
        # keeps every field — including the password — reachable through the
        # provider's `__closure__` for as long as the provider lives, which for
        # a test fixture is the whole test. Defence in depth; the provider
        # already never logs or reprs it.
        password = secret["password"]
        provider = CognitoUserPasswordTokenProvider(
            user_pool_client_id=_LIVE["cognito_user_pool_client_id"],
            username=secret["username"],
            password_provider=lambda: password,
        )
        return config, provider

    def test_records_route_still_matches_the_captured_contract(self):
        config, provider = self._config_and_token()
        result = get_subscription_feed(
            config=config,
            token_provider=provider,
            http_client=UrllibHttpClient(),
        )
        assert result.status_code == 200, result.body
        # A 200 here already means the validator passed — the proxy would have
        # returned 502 on drift. Asserting the marker's absence makes the
        # intent explicit rather than implied by the status.
        assert result.body.get("error") != ERROR_PRODUCER_SHAPE_DRIFT
        connected_services_proxy.validate_records_shape(result.body)

    def test_enroll_then_unenroll_round_trip_leaves_no_residue(self):
        """Exercises both scope routes and restores the starting state.

        Uses a synthetic-but-valid VIN so the round trip cannot disturb a real
        vehicle's enrollment, and asserts the scope is empty afterwards rather
        than trusting the DELETE's own return value.

        The enroll call is INSIDE the ``try`` and the ``finally`` actually
        remediates (security review SG1). Previously the enroll and its two
        assertions sat outside the ``try``, so an assertion failure between a
        successful enroll and the unenroll would skip cleanup entirely and
        strand the synthetic VIN in CMS's real staging subscription — a test
        that leaves state behind on the path it exists to guard.
        """
        config, provider = self._config_and_token()
        client = UrllibHttpClient()
        vin = "ZZ999999999999999"
        enrolled = False

        try:
            added = enroll_vin(
                vin=vin, config=config, token_provider=provider, http_client=client
            )
            assert added.status_code == 200, added.body
            enrolled = True
            assert vin in (added.body["added"] + added.body["already_present"])

            removed = unenroll_vin(
                vin=vin, config=config, token_provider=provider, http_client=client
            )
            assert removed.status_code == 200, removed.body
            assert removed.body["vin"] == vin
            enrolled = False
        finally:
            # Remediate, then verify. Best-effort: a failure here must not mask
            # the assertion that actually failed, but leaving the VIN enrolled
            # is worse than a noisy cleanup, so it is attempted unconditionally
            # whenever the enroll is known to have landed.
            if enrolled:
                try:
                    unenroll_vin(
                        vin=vin,
                        config=config,
                        token_provider=provider,
                        http_client=client,
                    )
                except Exception as cleanup_error:  # noqa: BLE001
                    print(f"cleanup unenroll failed for {vin}: {cleanup_error!r}")

            # Confirm from the producer's own detail route that nothing was left
            # enrolled — the DELETE's own return value is not the authority.
            detail_url = (
                f"{config.producer_endpoint.rstrip('/')}"
                f"/subscriptions/{config.cms_subscription_id}"
            )
            status, body = client.request(
                method="GET",
                url=detail_url,
                headers={
                    "Authorization": f"Bearer {provider.get_token()}",
                    "Accept": "application/json",
                },
            )
            assert status == 200, body
            assert vin not in (body.get("vehicle_scope") or []), (
                f"integration test left {vin} enrolled in CMS's subscription"
            )



class TestEnrollRequestBodyKeyMatchesProducer:
    """Pins the *request* body key, not just the response shapes.

    Review of T2.1 cycle 1 (Suggestion 1) noted that ``enroll_vin`` sends
    ``{"vin": ...}`` — the singular form — and nothing tied that choice to the
    producer's acceptance. The producer's ``_extract_vins`` accepts ``vins`` (a
    list) *or* ``vin`` (a scalar), so the singular form works today. But this is
    the same divergence class as the two T2.1 already fixed: a contract that
    holds by luck rather than by assertion. If the producer dropped the singular
    convenience form, this proxy would send a body the producer rejects with a
    400, and ``_call_producer_api`` would map that to a 502
    ``producer_unavailable`` — an upstream-outage story for what is really a
    request CMS built wrong.

    Read from the producer's source for the same reason as
    ``TestVinContractMatchesProducer``: a divergence cannot be detected by a
    constant only this side declares.
    """

    PRODUCER_HANDLER = os.path.join(
        _REPO_ROOT,
        "services",
        "connectors",
        "subscriptions",
        "subscription_scope",
        "handler.py",
    )

    def _accepted_body_keys(self) -> set[str]:
        """The literal keys ``_extract_vins`` tests membership of on the body.

        Derived from the AST rather than grepped so that a rename inside the
        producer is picked up structurally.
        """
        assert os.path.isfile(self.PRODUCER_HANDLER), (
            f"producer handler not found at {self.PRODUCER_HANDLER}; this test "
            f"cannot verify the request-body contract and must not pass "
            f"vacuously"
        )
        with open(self.PRODUCER_HANDLER, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_extract_vins":
                target = node
                break
        assert target is not None, (
            "_extract_vins not found in the producer handler; the accepted "
            "request-body keys cannot be read"
        )
        keys: set[str] = set()
        for node in ast.walk(target):
            # `if "vins" in body:` / `elif "vin" in body:`
            if (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and isinstance(node.comparators[0], ast.Name)
                and node.comparators[0].id == "body"
            ):
                keys.add(node.left.value)
        assert keys, (
            "no `<literal> in body` membership tests found in _extract_vins; "
            "the producer's body contract has changed shape and this test can "
            "no longer read it"
        )
        return keys

    def test_producer_still_accepts_the_key_this_proxy_sends(
        self, config, token_provider, http_client
    ):
        http_client.register("POST", "/scope", 200, LIVE_SCOPE_ADD_BODY)
        enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        sent_keys = set(http_client.calls[0]["body"])
        accepted = self._accepted_body_keys()
        assert sent_keys <= accepted, (
            f"enroll_vin sends body key(s) {sorted(sent_keys - accepted)} that "
            f"the producer's _extract_vins does not accept (it accepts "
            f"{sorted(accepted)}); the producer would answer 400 and this proxy "
            f"would report it as a 502 producer_unavailable"
        )

    def test_the_live_capture_used_the_same_key(self):
        """The captured POST shape must have been produced by this body form.

        Otherwise the response contract is pinned against a request this proxy
        never makes — the fixture would be describing someone else's call.
        """
        captured_request = _LIVE["responses"]["POST /subscriptions/{id}/scope"][
            "request_body"
        ]
        accepted = self._accepted_body_keys()
        assert set(captured_request) <= accepted

    def test_singular_form_is_what_the_proxy_sends(
        self, config, token_provider, http_client
    ):
        """Documents the choice explicitly, so a change to it is deliberate.

        The plural ``vins`` form is also accepted and would let CMS batch, which
        is a real future option — but the proxy's public interface is one VIN per
        call, so sending a one-element list would add a shape for no gain.
        """
        http_client.register("POST", "/scope", 200, LIVE_SCOPE_ADD_BODY)
        enroll_vin(
            vin="ABCDEFGHJKLMNPRST",
            config=config,
            token_provider=token_provider,
            http_client=http_client,
        )
        assert http_client.calls[0]["body"] == {"vin": "ABCDEFGHJKLMNPRST"}

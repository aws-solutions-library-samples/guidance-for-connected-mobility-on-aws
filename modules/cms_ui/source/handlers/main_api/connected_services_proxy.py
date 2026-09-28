# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CMS-side proxy to the Connected Services subscription plane.

Spec ``.kiro/specs/2026-09-10-cms-connected-services-consumer/`` T1.4 (Group 1).

## Scope

This module is the **proxy logic in isolation** — it holds no route
dispatch state, no shared globals with ``index.py``, and no import of
``index.py`` itself. ``index.py``'s route table wires the three new routes
(T2.3, Group 2) by calling into the three public functions here.

Group 1 (this task) implements the module against a **mocked** upstream:
``_call_producer_api()`` is unit-tested with the producer's HTTP responses
faked. The real HTTP call and real Cognito token acquisition land in T2.1,
which swaps in the real implementations of the ``TokenProvider`` interface
and (optionally) the ``ProducerHttpClient`` interface below without changing
the signatures of ``get_subscription_feed`` / ``enroll_vin`` / ``unenroll_vin``.

## Interfaces (so T2.1 can swap real for mock)

Two seams keep the real network out of Group 1 tests:

1. ``TokenProvider`` — returns an access token as a str. Group 1 uses
   ``StaticTokenProvider`` for tests. T2.1 will add
   ``CognitoUserPasswordTokenProvider`` (see docs/tech.md — Connected
   Services subscription plane — machine-client auth), which calls
   ``cognito_idp.initiate_auth`` with ``AuthFlow=COGNITO_AUTH_FLOW`` and
   credentials from Secrets Manager, and caches + refreshes the token.

   **Do not use ``admin_initiate_auth`` here.** See ``COGNITO_AUTH_FLOW``
   below — the admin flow is not enabled on the pool client CMS's
   subscriber account lives in, so it fails at runtime, not at import.
2. ``ProducerHttpClient`` — the HTTP transport. Group 1 uses ``MockHttpClient``
   in tests. T2.1 will use the real ``UrllibHttpClient`` (already implemented
   below) against the deployed producer endpoint from
   ``CONNECTED_SERVICES_API_ENDPOINT``. The real client is included in Group 1
   so it can be smoke-checked, but no code path in Group 1 dispatches to it.

## Authorization (spec D2 — reinforced by design, not by trust)

This module NEVER forwards the CMS end-user's JWT to the producer. Every
outbound HTTP call carries CMS's own subscriber-account token from the
``TokenProvider``. The three public functions receive a ``caller_context``
that has ALREADY been fleet-scope-checked by ``index.py`` (per D3) — this
module does not re-implement CMS's auth check, only performs the proxy.

## Error surface (spec R1 — fail visibly, not silently)

Every non-2xx from the producer is returned as a distinct status_code so
the UI can distinguish "not enrolled" (a 404) from "feed unavailable" (a
5xx or connection failure). A blanket ``500`` on any producer failure
would let a demo-day connectivity issue read as an app bug.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

#: Environment variables this proxy reads. Named constants because T2.3 wires
#: them in ``ui_stack.py`` and a typo on either side is a runtime-only failure.
#:
#: **Not ``CONNECTED_SERVICES_API_ENDPOINT``**, which is what this spec's
#: ``tasks.md`` T2.1 Accept text names. That variable already exists and belongs
#: to a different spec: ``deployment/config/staging.env:207`` sets it (currently
#: to the placeholder ``https://api.example.invalid``) for
#: ``2026-09-03-cms-connected-services-portal``, which consumes it as the
#: standalone portal's own API endpoint. Two specs sharing one variable is how
#: one team's deploy silently changes another team's runtime, so this spec takes
#: a distinct name and records the deviation from its own task text in
#: ``decisions.md``. The ``CS_`` prefix matches the sibling
#: ``CS_FEED_CACHE_TABLE_NAME`` that T1.5's stack already publishes.
ENV_PRODUCER_ENDPOINT = "CS_PRODUCER_API_ENDPOINT"

#: CMS's own ``subscription_id``. Sourced from the Secrets Manager entry the
#: provisioning script wrote, then surfaced as an env var by T2.3 — the
#: subscription id is not a credential (the password in the same secret is), so
#: it does not need a per-invocation secret read.
ENV_CMS_SUBSCRIPTION_ID = "CS_SUBSCRIPTION_ID"

#: Feed-cache table name, and the freshness window in seconds (T2.6, spec D3).
#:
#: **Both OPTIONAL, unlike the two above.** An unset table name means "no cache
#: configured" and the feed is served live — which is exactly what T2.3 shipped
#: and what every environment does until the consumer stack is deployed
#: (`cms-{stage}-connected-services-consumer` is opt-in behind
#: `DEPLOY_CONNECTED_SERVICES_CONSUMER`). Making these required would take a
#: capability that currently WORKS and break it everywhere the cache is absent,
#: to add an optimisation. `from_env` therefore reads them with `.get`, and only
#: the producer endpoint and subscription id still raise `KeyError`.
ENV_FEED_CACHE_TABLE = "CS_FEED_CACHE_TABLE_NAME"
ENV_FEED_CACHE_TTL_SECONDS = "CS_FEED_CACHE_TTL_SECONDS"

#: Default freshness window. 60s balances two costs: materially longer serves
#: a feed the producer may have already superseded; materially shorter spends
#: a producer round trip per page view for data that cannot have changed. The
#: CS producer's own refresh cadence is not published to CMS, so this value is
#: a judgment call, not derived from a known upstream schedule.
DEFAULT_FEED_CACHE_TTL_SECONDS = 60

#: Partition/sort key of the whole-feed cache row. The table also supports
#: `vin#<VIN>` rows per its documented schema; this task writes only `latest`,
#: because the three routes read the whole feed and filter in `index.py` — a
#: per-VIN row would be a second representation of the same data with its own
#: invalidation problem and no reader.
FEED_CACHE_LATEST_KEY = "latest"

#: ISO 3779 VIN allow-list. VINs are 17 chars for post-1981 vehicles; the
#: character set excludes I, O, Q, and any URL-metacharacter (``?``, ``#``,
#: ``%``, whitespace, ``/``, ``\``, ``\r``, ``\n``). Applied to both
#: ``enroll_vin`` and ``unenroll_vin`` even though only ``unenroll_vin``
#: interpolates the value into a URL path — the two-char deny-list this
#: replaces missed ``?``/``#``/``%``/space/CRLF, so a defense-in-depth
#: allow-list at both call sites is the correct level of paranoia for a
#: value that reaches the producer's routing layer either way.
#:
#: **Exactly 17, matching the producer.** T1.4 chose ``{1,17}`` on the stated
#: guess that "the producer may accept shorter values for test/demo VINs".
#: T2.1 read the producer's committed source and the guess is false:
#: ``subscription_scope/handler.py:97`` is ``^[A-HJ-NPR-Z0-9]{17}$`` — exactly
#: 17 — and both ``_extract_vins`` and ``remove_handler`` reject anything else
#: with a 400. Under the old ceiling a 16-char VIN passed this proxy, earned a
#: producer 400, and ``_call_producer_api`` mapped that to a **502
#: producer_unavailable** — a user input error reported as an upstream outage,
#: which is precisely the failure this module's R1 docstring exists to prevent.
#: Per T2.1's Constraints the fix belongs here, not in the producer's
#: already-reviewed contract.
_VIN_ALLOWED = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def _normalize_vin(vin: Any) -> Optional[str]:
    """``strip().upper()`` then allow-list, or ``None`` if unacceptable.

    Normalization mirrors the producer, which does ``v.strip().upper()`` before
    matching (``_extract_vins``, and ``remove_handler`` for the path
    parameter). Without it this proxy is *stricter than the producer on case*:
    a lowercase VIN the producer would happily accept was rejected here with
    "vin format invalid", so the two sides disagreed about the same input.
    Normalizing rather than merely widening the pattern also means the VIN
    interpolated into the DELETE URL is the same string the producer stores,
    so ``removed`` is truthful instead of accidentally reporting a miss.
    """
    if not isinstance(vin, str):
        return None
    candidate = vin.strip().upper()
    return candidate if _VIN_ALLOWED.match(candidate) else None

#: Timeout (seconds) for outbound calls to the producer. Matches the DMS
#: proxy's 8-10s range so a slow producer does not exceed the API Gateway's
#: 29s integration timeout even after two retries would apply — Group 1 does
#: not implement retries; the timeout alone is the deadline.
DEFAULT_TIMEOUT_SECONDS = 8

#: The Cognito ``AuthFlow`` T2.1's real ``TokenProvider`` must use, and the
#: ``cognito.AuthFlow`` CDK kwarg that has to stay enabled on the pool client
#: for it to work. Stated as constants rather than prose because the wrong
#: choice fails at *runtime*, on the first live call, with an opaque
#: ``InvalidParameterException`` — see ``TestCognitoAuthFlowContract``.
#:
#: **Why not the admin flow.** T1.3 originally chose ``AdminInitiateAuth``
#: with ``ADMIN_USER_PASSWORD_AUTH``, on the reasoning that a server-side
#: call is more auditable in CloudTrail. That flow is **not enabled** on
#: ``CMSUserPoolClient`` (``deployment/stacks/ui_stack.py``), which sets
#: ``cognito.AuthFlow(user_password=True, user_srp=True)`` and therefore
#: grants only ``ALLOW_USER_PASSWORD_AUTH`` / ``ALLOW_USER_SRP_AUTH`` /
#: ``ALLOW_REFRESH_TOKEN_AUTH``. Verified against the live staging client
#: on 2026-09-12, not inferred from the CDK source. Enabling the admin flow
#: would mean editing the app client that ~9 prod Federate admins and every
#: seeded persona authenticate through — a far larger blast radius than
#: this spec needs for a token.
#:
#: ``USER_PASSWORD_AUTH`` via ``initiate_auth`` is enabled today, needs no
#: client secret (``generate_secret=False`` on that client), and is callable
#: from the proxy Lambda. It does not require the Lambda role to hold any
#: ``cognito-idp:*`` IAM permission, because ``InitiateAuth`` is an
#: unauthenticated API keyed on ``ClientId`` — so the trade against the
#: admin flow is *less* IAM surface for *less* CloudTrail distinctness.
#:
#: The CDK kwarg is **derived**, not declared alongside. Review of FG2.1
#: found that two independently-declared constants can disagree without any
#: test noticing — set the flow to ``USER_PASSWORD_AUTH`` and the kwarg to
#: ``user_srp`` and the contract test still passes, because both flows happen
#: to be enabled on this client. Deriving makes that state unrepresentable
#: rather than merely detectable. The mapping deliberately contains only the
#: flows available on ``CMSUserPoolClient``, so naming an unavailable flow
#: (the FG2.1 regression) raises ``KeyError`` at import rather than
#: ``InvalidParameterException`` on the first live call.
_AVAILABLE_FLOW_CDK_KWARGS = {
    "USER_PASSWORD_AUTH": "user_password",
    "USER_SRP_AUTH": "user_srp",
}
COGNITO_AUTH_FLOW = "USER_PASSWORD_AUTH"
COGNITO_AUTH_FLOW_CDK_KWARG = _AVAILABLE_FLOW_CDK_KWARGS[COGNITO_AUTH_FLOW]

#: Marker returned by all three public functions when the producer response
#: shape is not what T1.4 assumed. T2.1 asserts the actual deployed shape
#: against these constants — a shape drift fails closed with a distinct
#: error rather than serving cached-or-partial data as if it were fresh.
ERROR_PRODUCER_UNAVAILABLE = "producer_unavailable"
ERROR_PRODUCER_UNAUTHORIZED = "producer_unauthorized"
ERROR_PRODUCER_SHAPE_DRIFT = "producer_shape_drift"
ERROR_CONFIG_MISSING = "config_missing"


# ── Response-shape contracts (T2.1) ────────────────────────────────────────
#
# Every expectation below was read off the **live deployed producer** on
# 2026-09-12 and is recorded verbatim in this spec's
# ``producer-live-shapes.json``. None of it is transcribed from ``spec.md``
# prose, per spec R3 — the prose predicted neither ``count`` nor ``quota`` on
# the records route, and predicted no shape at all for the two scope routes.
#
# Validation runs **only on a 2xx**. A non-2xx already has a defined mapping in
# ``_call_producer_api``; re-checking its body against a success contract would
# turn an honest 404 into a shape-drift error.
#
# The failure mode these guard is specific: the producer adds or renames a key,
# CMS reads ``.get()`` on the old name, gets ``None``, and renders an empty or
# partial card that looks like "no data" rather than "we can no longer read the
# feed". Failing closed with a distinct marker is the difference between a
# demo-day question with an answer and one without.

#: Top-level keys the records route must return. Live: exactly this set.
_RECORDS_REQUIRED_KEYS = frozenset(
    {"subscription_id", "records", "count", "vins_in_scope", "unresolved_vins", "quota"}
)

#: Per-record keys. Live capture: all 100 records carried all five. Only the
#: three the UI actually renders are *required* — ``vehicleId`` and
#: ``dataSourceRoute`` are asserted as present-in-practice by the live-shape
#: fixture test, not enforced here, so a producer that legitimately omits
#: ``vehicleId`` for an unresolved VIN does not black out the whole feed.
_RECORD_REQUIRED_KEYS = frozenset({"vin", "signals", "timestamp"})

#: Scope-mutation routes. ``added``/``already_present`` are lists of VIN
#: strings; ``removed`` is a bool; ``scope_size`` is an int.
_SCOPE_ADD_REQUIRED_KEYS = frozenset(
    {"subscription_id", "added", "already_present", "scope_size"}
)
_SCOPE_REMOVE_REQUIRED_KEYS = frozenset(
    {"subscription_id", "vin", "removed", "scope_size"}
)


class ShapeDrift(Exception):
    """A 2xx producer body did not match the live-captured contract.

    Carries a human-readable reason naming the specific expectation that
    failed. The reason is safe to log and to return to the CMS frontend: it
    names keys and types, never values, so a drifted payload cannot leak
    through the error path.
    """


def _require_mapping(body: Any, route: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ShapeDrift(f"{route}: expected a JSON object, got {type(body).__name__}")
    return body


def _require_keys(body: dict[str, Any], required: frozenset[str], route: str) -> None:
    missing = sorted(required - set(body))
    if missing:
        raise ShapeDrift(f"{route}: missing required key(s) {missing}")


def _require_type(body: dict[str, Any], key: str, typ: type, route: str) -> None:
    value = body[key]
    # bool is a subclass of int; an int where a bool is contracted (or the
    # reverse) is real drift, so check bool explicitly rather than relying on
    # isinstance's subclass behaviour.
    if typ is bool:
        ok = isinstance(value, bool)
    elif typ is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, typ)
    if not ok:
        raise ShapeDrift(
            f"{route}: key {key!r} expected {typ.__name__}, "
            f"got {type(value).__name__}"
        )


def validate_records_shape(body: Any) -> None:
    """``GET /subscriptions/{id}/records`` — raise ``ShapeDrift`` if drifted."""
    route = "GET /subscriptions/{id}/records"
    body = _require_mapping(body, route)
    _require_keys(body, _RECORDS_REQUIRED_KEYS, route)
    _require_type(body, "records", list, route)
    _require_type(body, "count", int, route)
    _require_type(body, "vins_in_scope", list, route)
    _require_type(body, "unresolved_vins", list, route)
    _require_type(body, "quota", dict, route)
    # `count` is the producer's own count of `records`; if they disagree, one of
    # the two is not what we think it is (a paginated response, a filtered
    # subset) and the card's "N records" line would be a lie.
    #
    # This is the one assertion here that would need revisiting if the producer
    # ever paginates: `count` would legitimately become the total rather than
    # the page size, and this check would then reject every page. That is the
    # correct failure — it would stop CMS rendering a page as if it were the
    # whole feed — but the fix would be to read the new pagination keys, not to
    # relax the check.
    if body["count"] != len(body["records"]):
        raise ShapeDrift(
            f"{route}: 'count' ({body['count']}) disagrees with "
            f"len(records) ({len(body['records'])})"
        )
    for index, record in enumerate(body["records"]):
        if not isinstance(record, dict):
            raise ShapeDrift(
                f"{route}: records[{index}] expected object, "
                f"got {type(record).__name__}"
            )
        record_missing = sorted(_RECORD_REQUIRED_KEYS - set(record))
        if record_missing:
            raise ShapeDrift(
                f"{route}: records[{index}] missing key(s) {record_missing}"
            )
        if not isinstance(record["signals"], dict):
            raise ShapeDrift(
                f"{route}: records[{index}]['signals'] expected object, "
                f"got {type(record['signals']).__name__}"
            )


def validate_scope_add_shape(body: Any) -> None:
    """``POST /subscriptions/{id}/scope`` — raise ``ShapeDrift`` if drifted."""
    route = "POST /subscriptions/{id}/scope"
    body = _require_mapping(body, route)
    _require_keys(body, _SCOPE_ADD_REQUIRED_KEYS, route)
    _require_type(body, "added", list, route)
    _require_type(body, "already_present", list, route)
    _require_type(body, "scope_size", int, route)


def validate_scope_remove_shape(body: Any) -> None:
    """``DELETE /subscriptions/{id}/scope/{vin}`` — raise if drifted."""
    route = "DELETE /subscriptions/{id}/scope/{vin}"
    body = _require_mapping(body, route)
    _require_keys(body, _SCOPE_REMOVE_REQUIRED_KEYS, route)
    _require_type(body, "removed", bool, route)
    _require_type(body, "scope_size", int, route)


# ── Interfaces ────────────────────────────────────────────────────────────

class TokenProvider(Protocol):
    """Yields CMS's subscriber-account access token (see spec D2)."""

    def get_token(self) -> str:
        """Return a currently-valid access token. Refresh if needed."""
        ...


@dataclass
class StaticTokenProvider:
    """Fixed token, for tests. Never used in production paths."""

    token: str

    def get_token(self) -> str:
        return self.token


class TokenAcquisitionError(RuntimeError):
    """Raised when CMS cannot obtain a usable subscriber token.

    Carries no credential material — see ``CognitoUserPasswordTokenProvider``
    for why every raise site in that class constructs its own message rather
    than interpolating the underlying exception.
    """


#: Seconds before nominal expiry at which a cached token is considered stale.
#: A demo-day request that lands 3 seconds before expiry must not be the one
#: that discovers the token aged out mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS = 120


@dataclass
class CognitoUserPasswordTokenProvider:
    """Real ``TokenProvider`` — CMS's subscriber-account token via Cognito.

    Spec T2.1 (Group 2). Uses ``InitiateAuth`` with
    ``AuthFlow=COGNITO_AUTH_FLOW`` (``USER_PASSWORD_AUTH``); see
    ``COGNITO_AUTH_FLOW`` for why not the admin flow.

    ## It returns the IdToken, deliberately

    The producer fronts its API with an
    ``apigateway.CognitoUserPoolsAuthorizer``
    (``deployment/stacks/subscriptions_stack.py:327``), i.e.
    ``AuthorizationType.COGNITO``. That authorizer validates the **IdToken**:
    it requires an ``aud`` claim matching the app client id. Cognito
    AccessTokens carry ``client_id`` instead of ``aud``, so passing the
    AccessToken yields a **401 from API Gateway before the producer's handler
    ever runs** — which reads exactly like a bad credential and sends you
    looking in the wrong place. The IdToken also carries the ``sub`` and
    ``cognito:groups`` claims the producer's handlers read.

    ## The account must not be in FORCE_CHANGE_PASSWORD

    The producer's ``POST /admin/subscribers`` creates the account with
    ``MessageAction="SUPPRESS"`` and does not set a permanent password
    (``admin_provision_subscriber/handler.py:337``); its own docstring notes
    that first sign-in returns ``NEW_PASSWORD_REQUIRED``. For a **human**
    subscriber that is correct and fine. For CMS's machine account it is not:
    ``InitiateAuth`` against such a user returns a ``ChallengeName`` and **no
    tokens at all**, and an unattended Lambda cannot answer a challenge
    without writing a new password back to Secrets Manager from inside the
    token path — a write path in a read path, which we are not doing.

    So the operator-run provisioning script must promote the password to
    permanent (``AdminSetUserPassword(Permanent=True)``) once, at
    provisioning time, where admin credentials legitimately exist. This class
    does not paper over it: a challenge response raises
    ``TokenAcquisitionError`` naming the exact remediation, because the
    alternative is a 401 at demo time whose cause is three services away.

    ## Caching

    One token is cached in the Lambda execution environment and reused until
    ``TOKEN_REFRESH_MARGIN_SECONDS`` before expiry, then refreshed via
    ``REFRESH_TOKEN_AUTH`` (enabled on this client — ``ALLOW_REFRESH_TOKEN_AUTH``
    is present) and finally by full re-auth if the refresh token itself has
    aged out. Spec R2: a token minted at Sunday rehearsal must not be the
    thing that breaks Monday.

    **There is no in-band invalidation seam, and rotating the secret is not
    one.** A warm container keeps serving its cached IdToken until the margin
    elapses, regardless of what happens to the password in Secrets Manager —
    so revoking CMS's access means ``AdminUserGlobalSignOut`` on the subscriber
    user (which invalidates the refresh token) and/or forcing new execution
    environments by publishing a new Lambda version. Rotating the secret alone
    changes only what the *next* full re-auth uses.
    """

    user_pool_client_id: str
    username: str
    #: Callable returning the current password. A callable rather than a str so
    #: the caller controls when Secrets Manager is read, and so the password is
    #: not sitting in this dataclass's ``repr`` for the life of the container.
    #:
    #: ``repr=False`` is belt-and-braces on top of that, per security-review
    #: cycle 1 Suggestion 2: a callable's repr is already safe (it prints the
    #: function object, not the closed-over value), but a future refactor that
    #: changed this field to a plain ``str`` would silently start printing the
    #: password in every ``repr`` of this dataclass. The annotation makes that
    #: refactor safe by default rather than dependent on whoever makes it
    #: remembering why the callable was there.
    password_provider: Callable[[], str] = field(repr=False)
    #: Injected for tests. Real callers pass ``None`` and get a boto3 client
    #: with explicit, short timeouts (see ``_build_client``).
    cognito_client: Optional[Any] = None

    _cached_token: Optional[str] = field(default=None, repr=False)
    _expires_at: float = field(default=0.0, repr=False)
    _refresh_token: Optional[str] = field(default=None, repr=False)

    @staticmethod
    def _build_client() -> Any:
        """boto3 ``cognito-idp`` client with bounded timeouts.

        Prior security review of Group 1 flagged that the outbound producer
        call has an 8s timeout while the token seam had none — boto3's
        defaults (60s connect + 60s read, 4 retries) can burn minutes and
        push the Lambda past API Gateway's 29s integration timeout, turning a
        slow Cognito into an opaque 504. Bounded here so the token step can
        never be the thing that exceeds the deadline.
        """
        import boto3  # imported lazily so unit tests need no AWS SDK
        from botocore.config import Config

        return boto3.client(
            "cognito-idp",
            config=Config(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 2},
            ),
        )

    def _client(self) -> Any:
        if self.cognito_client is None:
            self.cognito_client = self._build_client()
        return self.cognito_client

    def get_token(self) -> str:
        now = time.time()
        if self._cached_token and now < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._cached_token

        if self._refresh_token:
            try:
                self._auth(
                    {"REFRESH_TOKEN": self._refresh_token},
                    flow="REFRESH_TOKEN_AUTH",
                )
                return self._cached_token  # type: ignore[return-value]
            except TokenAcquisitionError:
                # Refresh token aged out (30d) or was revoked. Fall through to
                # a full re-auth rather than failing the request — this is the
                # expected path after a long idle period, not an error.
                logger.info(
                    "Refresh failed; falling back to full re-auth for %s",
                    self.username,
                )
                self._refresh_token = None

        self._auth(
            {"USERNAME": self.username, "PASSWORD": self.password_provider()},
            flow=COGNITO_AUTH_FLOW,
        )
        return self._cached_token  # type: ignore[return-value]

    def _auth(self, auth_parameters: dict[str, str], flow: str) -> None:
        """Call ``InitiateAuth`` and populate the cache, or raise.

        Every raise site constructs its own message. The underlying exception
        is deliberately **not** interpolated: on the full-auth path
        ``auth_parameters`` contains the password, and a botocore exception
        that echoed the request — or any future wrapping layer that did —
        would put it in CloudWatch. ``type(e).__name__`` is enough to
        distinguish the cases and cannot carry the value.
        """
        try:
            resp = self._client().initiate_auth(
                ClientId=self.user_pool_client_id,
                AuthFlow=flow,
                AuthParameters=auth_parameters,
            )
        except Exception as e:
            raise TokenAcquisitionError(
                f"InitiateAuth failed for flow {flow}: {type(e).__name__}"
            ) from None
        finally:
            # Defence in depth against frame-local capture. `from None` keeps
            # the password out of the exception chain, but on the full-auth
            # path `auth_parameters` still holds it as a local of this frame,
            # and a traceback formatter running with locals enabled
            # (`TracebackException(capture_locals=True)`, or an error reporter
            # configured to send them) reads frame locals at *format* time —
            # after this `finally` has run. Clearing the value here shortens
            # its lifetime to the call itself. `get_token` builds a fresh dict
            # per attempt, so this cannot break the retry or refresh paths.
            auth_parameters.pop("PASSWORD", None)

        if resp.get("ChallengeName"):
            raise TokenAcquisitionError(
                f"Cognito returned challenge {resp['ChallengeName']!r} instead "
                f"of tokens for {self.username}. A machine account cannot "
                f"answer a challenge. Remediation: the operator-run "
                f"provisioning script must call "
                f"AdminSetUserPassword(Permanent=True) for this user once — "
                f"the producer's POST /admin/subscribers creates it with "
                f"MessageAction=SUPPRESS, which leaves it in "
                f"FORCE_CHANGE_PASSWORD."
            )

        result = resp.get("AuthenticationResult") or {}
        # IdToken, not AccessToken — the producer's API Gateway authorizer is
        # COGNITO_USER_POOLS and validates `aud`, which only the IdToken has.
        token = result.get("IdToken")
        if not token:
            raise TokenAcquisitionError(
                f"InitiateAuth returned no IdToken for flow {flow} "
                f"(keys present: {sorted(result)}). Note the producer's "
                f"authorizer requires the IdToken specifically; an "
                f"AccessToken-only response is not usable."
            )

        self._cached_token = token
        # `.get(k, default)` rather than `get(k) or default`: the latter would
        # silently rewrite a Cognito-issued `0` (or any falsy value) into an
        # hour, turning a policy-driven short lifetime into a token we treat as
        # long-lived. Distinguish "absent" from "zero".
        expires_in = result.get("ExpiresIn", 3600)
        self._expires_at = time.time() + float(expires_in)
        # A REFRESH_TOKEN_AUTH response does not re-issue a refresh token;
        # keep the existing one rather than clearing it.
        if result.get("RefreshToken"):
            self._refresh_token = result["RefreshToken"]



def _parse_ttl_seconds(raw: Optional[str]) -> int:
    """Parse the TTL env var, falling back to the default on anything unusable.

    Returns the DEFAULT — not 0 — for a malformed value. The alternative was
    considered and rejected: 0 disables the cache, so a typo in an env var
    (``"60s"``, ``"sixty"``) would silently turn a feature off while every
    response stayed correct, which is the exact silent-degradation shape this
    spec keeps finding. A malformed value instead gets the documented default and
    a WARNING, so the feed stays fast and the mistake is visible in logs.

    A negative value IS honoured as "disabled": that is unambiguous intent rather
    than a typo.
    """
    if raw is None or not str(raw).strip():
        return DEFAULT_FEED_CACHE_TTL_SECONDS
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not an integer; falling back to %ds",
            ENV_FEED_CACHE_TTL_SECONDS,
            raw,
            DEFAULT_FEED_CACHE_TTL_SECONDS,
        )
        return DEFAULT_FEED_CACHE_TTL_SECONDS


class FeedCache(Protocol):
    """The feed cache's read/write surface.

    A Protocol so tests inject an in-memory double and the Lambda injects
    DynamoDB — matching how ``ProducerHttpClient`` and ``TokenProvider`` are
    already structured in this module.
    """

    def get_latest(self, subscription_id: str) -> Optional[dict[str, Any]]:
        """Return the cached payload, or None on miss / stale / error."""
        ...

    def put_latest(
        self, subscription_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> None:
        """Best-effort write. Must not raise."""
        ...


class DynamoFeedCache:
    """DynamoDB-backed ``FeedCache`` over ``cms-{stage}-storage-cs-feed-cache-*``.

    Single ``GetItem`` / single ``PutItem``, both covered by the main_api role's
    existing ``AppAccess`` grant on ``table/*``. **No new IAM statement**, and in
    particular no ``BatchWriteItem``: ``ui_stack.py`` asserts that action's
    ABSENCE, and this task keeps it absent by writing one row rather than
    batching. Granting an action with no reachable consumer is the shape CVX's
    Tier 2 agent shipped with live ``bedrock:InvokeModel`` and no caller.

    **Every failure is swallowed.** A cache is an optimisation; a cache outage
    that turned a working feed into a 502 would make the system strictly worse
    than having no cache at all. Errors log at WARNING and the caller falls
    through to the producer. This is the one place in this module where
    swallowing is correct, and it is bounded to the cache — producer failures
    still surface, because those an operator needs to know about.
    """

    def __init__(self, table_name: str, client: Any = None) -> None:
        self._table_name = table_name
        self._client = client

    def _dynamo(self) -> Any:
        if self._client is None:
            import boto3  # noqa: PLC0415 — deferred so unit tests need no boto3

            self._client = boto3.client("dynamodb")
        return self._client

    def get_latest(self, subscription_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = self._dynamo().get_item(
                TableName=self._table_name,
                Key={
                    "subscription_id": {"S": subscription_id},
                    "record_key": {"S": FEED_CACHE_LATEST_KEY},
                },
                ConsistentRead=False,
            )
        except Exception as err:  # noqa: BLE001 — see the class docstring
            logger.warning("feed cache read failed: %s", type(err).__name__)
            return None

        item = resp.get("Item")
        if not item:
            return None

        # TTL is enforced HERE, not left to DynamoDB. DynamoDB's TTL is
        # best-effort reclamation within ~48h of expiry, not a read guarantee —
        # trusting it would serve a two-day-old feed as current.
        try:
            expires_at = int(item["ttl"]["N"])
        except (KeyError, TypeError, ValueError):
            logger.warning("feed cache row has no usable ttl; treating as a miss")
            return None
        if expires_at <= int(time.time()):
            return None

        try:
            payload = json.loads(item["payload"]["S"])
        except (KeyError, TypeError, ValueError) as err:
            logger.warning("feed cache payload unreadable: %s", type(err).__name__)
            return None
        if not isinstance(payload, dict):
            logger.warning("feed cache payload is not an object; treating as a miss")
            return None

        cached_at = item.get("cached_at", {}).get("S")
        if cached_at:
            # Staleness is SURFACED, not hidden. The card renders "as of
            # <cached_at>" so an operator reading a cached feed can tell that it
            # is cached — the same reason a Tier 2 artifact carries `computed_at`.
            # A stale answer presented as live is a correctness bug.
            payload = dict(payload)
            payload["cached_at"] = cached_at
        return payload

    def put_latest(
        self, subscription_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> None:
        now = int(time.time())
        try:
            self._dynamo().put_item(
                TableName=self._table_name,
                Item={
                    "subscription_id": {"S": subscription_id},
                    "record_key": {"S": FEED_CACHE_LATEST_KEY},
                    "payload": {"S": json.dumps(payload)},
                    "cached_at": {
                        "S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
                    },
                    "ttl": {"N": str(now + ttl_seconds)},
                },
            )
        except Exception as err:  # noqa: BLE001 — see the class docstring
            logger.warning("feed cache write failed: %s", type(err).__name__)


class ProducerHttpClient(Protocol):
    """Transport for calls to the producer's REST API. Two implementations.

    ``UrllibHttpClient`` is the real one (used from T2.1 onwards).
    ``MockHttpClient`` is the test double (only used in unit tests).
    """

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Optional[dict[str, Any]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[int, dict[str, Any]]:
        """Return ``(status_code, parsed_json_body)``.

        Non-2xx responses are returned, not raised, so callers control the
        error surface. Network failures (connection error, timeout) MUST be
        translated to ``(0, {"error": "<reason>"})`` — a distinct
        ``status_code == 0`` marks a network failure vs. a producer HTTP
        error.
        """
        ...


@dataclass
class UrllibHttpClient:
    """Real HTTP client for the producer API. Ready for T2.1.

    Uses ``urllib.request`` to match the DMS-proxy pattern in ``index.py``
    (no new dependency), and refuses redirects for the same reason the DMS
    proxy does — a producer that responds with a 302 to an unexpected host
    would otherwise send the Authorization header there.
    """

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Optional[dict[str, Any]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[int, dict[str, Any]]:
        # Refuse redirects — same rationale as index.py's _RefuseRedirects.
        # A 3xx from the producer would otherwise carry the Authorization
        # header (CMS's subscriber token) to whichever host the producer
        # named in Location.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def http_error_302(self, req, fp, code, msg, headers):
                raise urllib.error.HTTPError(
                    req.full_url, code, "Redirect refused", headers, fp
                )

            http_error_301 = http_error_303 = http_error_307 = http_error_308 = (
                http_error_302
            )

        opener = urllib.request.build_opener(_NoRedirect)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                return (resp.status, parsed)
        except urllib.error.HTTPError as e:
            # Non-2xx from the producer — return, do not raise.
            try:
                raw = e.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"error": str(e)}
            return (e.code, parsed)
        except Exception as e:  # network / timeout / bad JSON
            logger.warning("Producer API network failure: %s", type(e).__name__)
            return (0, {"error": type(e).__name__})


@dataclass
class MockHttpClient:
    """Programmable test double for the producer API.

    Each ``register`` call teaches the mock what to return for a given
    ``(method, path)`` pair. Unknown pairs return ``(500, {"error":
    "unregistered"})`` — never a silent success, so a test that hits an
    unexpected route fails visibly.
    """

    #: Route table: ``{(method, path_prefix): (status_code, body)}``.
    responses: dict[tuple[str, str], tuple[int, dict[str, Any]]] = field(
        default_factory=dict
    )
    #: Call log — every request that reached this client, in order. Tests
    #: assert against this to prove auth headers were set correctly.
    calls: list[dict[str, Any]] = field(default_factory=list)

    def register(
        self, method: str, path_prefix: str, status: int, body: dict[str, Any]
    ) -> None:
        self.responses[(method.upper(), path_prefix)] = (status, body)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Optional[dict[str, Any]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {
                "method": method.upper(),
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        # Match longest path_prefix first — a caller registering
        # `/subscriptions/{id}/scope` should not be shadowed by a broader
        # `/subscriptions/` registration.
        candidates = sorted(
            [k for k in self.responses if k[0] == method.upper() and k[1] in url],
            key=lambda k: -len(k[1]),
        )
        if not candidates:
            return (500, {"error": "unregistered", "method": method, "url": url})
        return self.responses[candidates[0]]


# ── Config ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProxyConfig:
    """Immutable config bundle. All fields required — no defaults.

    ``from_env`` is the single supported construction path for the Lambda;
    Group 1 tests construct the dataclass directly.
    """

    producer_endpoint: str  #: e.g. ``https://api.subscriptions.example/prod``
    cms_subscription_id: str  #: CMS's own subscription_id, from Secrets Manager

    #: Feed cache (T2.6). Empty table name = no cache; the feed is served live.
    #: See ``ENV_FEED_CACHE_TABLE`` for why this is optional rather than required.
    feed_cache_table: str = ""
    feed_cache_ttl_seconds: int = DEFAULT_FEED_CACHE_TTL_SECONDS

    @property
    def cache_enabled(self) -> bool:
        """A cache is usable only with a table AND a positive window.

        A non-positive TTL is treated as "disabled" rather than "always stale":
        both behave identically on the read path, but only one of them also skips
        the pointless write.
        """
        return bool(self.feed_cache_table) and self.feed_cache_ttl_seconds > 0

    def __post_init__(self) -> None:
        # A misconfigured endpoint is one of the two most likely R1 causes;
        # fail closed at construction time so a downstream network error
        # cannot be misread as a config error (or vice versa).
        if not self.producer_endpoint or not self.producer_endpoint.startswith(
            "https://"
        ):
            raise ValueError(
                "producer_endpoint must be a non-empty https:// URL "
                "(got %r)" % (self.producer_endpoint,)
            )
        if not self.cms_subscription_id:
            raise ValueError("cms_subscription_id must be non-empty")

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "ProxyConfig":
        """Build from the Lambda environment. Raises ``KeyError`` if unset.

        ``KeyError`` rather than a default: a proxy pointed at the wrong
        producer is worse than a proxy that refuses to start, and T2.3's route
        handler maps the raise to a 502 ``config_missing`` so the UI can say
        "configuration issue" rather than "no data".
        """
        source = env if env is not None else os.environ
        try:
            endpoint = source[ENV_PRODUCER_ENDPOINT]
            subscription_id = source[ENV_CMS_SUBSCRIPTION_ID]
        except KeyError as missing:
            raise KeyError(
                f"{missing.args[0]} is not set; the Connected Services proxy "
                f"requires both {ENV_PRODUCER_ENDPOINT} and "
                f"{ENV_CMS_SUBSCRIPTION_ID}"
            ) from None
        return cls(
            producer_endpoint=endpoint,
            cms_subscription_id=subscription_id,
            feed_cache_table=(source.get(ENV_FEED_CACHE_TABLE) or "").strip(),
            feed_cache_ttl_seconds=_parse_ttl_seconds(
                source.get(ENV_FEED_CACHE_TTL_SECONDS)
            ),
        )


# ── Public interface ──────────────────────────────────────────────────────

@dataclass
class ProxyResult:
    """Uniform return shape for the three public functions.

    ``status_code`` is the HTTP status this proxy returns to its own caller
    (the CMS API Gateway → CMS frontend), NOT necessarily the producer's
    status_code. Producer failures are mapped:

    - Producer 2xx     -> proxy 200 + payload
    - Producer 401/403 -> proxy 502 (this is a CMS-side config problem —
                           CMS's subscriber credential was rejected; the
                           end user did not do anything wrong)
    - Producer 404     -> proxy 404 + not-enrolled state (the "VIN is not
                           in scope" case is a normal user-facing result,
                           not an error)
    - Producer 5xx     -> proxy 502 + producer_unavailable
    - Network failure  -> proxy 503 + producer_unavailable
    """

    status_code: int
    body: dict[str, Any]

    #: Response headers every proxy response carries, on top of the caller's
    #: CORS headers. Security-review SG5 (``security-review-t21-cycle1.md``)
    #: recommended setting them HERE rather than in ``index.py``'s route wiring,
    #: "so the invariant is visible in one place" — a route that forgets them
    #: cannot exist if the only way to build a response stamps them.
    #:
    #: ``no-store`` rather than ``no-cache``: the hazard SG5 names is a browser
    #: replaying a 502 from a transient producer outage after the outage has
    #: cleared, and ``no-cache`` permits storing the response (it only forces
    #: revalidation), which is a weaker guarantee than this needs. The feed is
    #: also per-operator — it has already been filtered to the caller's fleet
    #: scope by the time it reaches here — so it must not be stored by any shared
    #: cache either.
    #:
    #: ``nosniff`` because the error path interpolates a producer-supplied
    #: ``detail`` string into the body; content-type sniffing on an error
    #: response is how a JSON body gets interpreted as something executable.
    SECURITY_HEADERS = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Type": "application/json",
    }

    def to_lambda_response(
        self, cors_headers: dict[str, str]
    ) -> dict[str, Any]:
        """Convert to the ``{'statusCode','headers','body'}`` shape index.py returns.

        ``SECURITY_HEADERS`` is applied LAST so it cannot be silently dropped by
        a caller passing a ``cors_headers`` dict that happens to carry one of the
        same keys. The CORS keys are disjoint from these three, so nothing the
        caller legitimately needs is displaced — asserted by
        ``test_security_headers_do_not_displace_cors``.
        """
        headers = dict(cors_headers)
        headers.update(self.SECURITY_HEADERS)
        return {
            "statusCode": self.status_code,
            "headers": headers,
            "body": json.dumps(self.body),
        }


def get_subscription_feed(
    *,
    config: ProxyConfig,
    token_provider: TokenProvider,
    http_client: ProducerHttpClient,
    cache: Optional[FeedCache] = None,
) -> ProxyResult:
    """Proxy for ``GET /subscriptions/{id}/records``, cache-then-producer (D3).

    ``index.py`` (T2.3) applies its own fleet-scope filter to the returned
    payload before responding to the CMS frontend — this function does NOT
    filter, so a call site that forgets that filter would ship the raw
    producer response. That is the same discipline the DMS proxy uses
    (``index.py:7986`` — filter on the cache read, not on the raw response).

    **THE CACHE STORES THE UNFILTERED PAYLOAD, AND THAT IS LOAD-BEARING.**
    Caching happens here, upstream of every fleet-scope decision, so a cached row
    is producer-shaped and operator-agnostic and ``index.py`` filters it per
    caller on the way out — exactly as it filters a live response. The tempting
    alternative, caching what the caller sees, would key one operator's filtered
    view under a subscription-scoped primary key and serve it to the next
    operator: a cross-fleet leak with a cache-hit-rate justification. The primary
    key is ``(subscription_id, 'latest')`` with no caller component, which is
    correct precisely BECAUSE the value is unfiltered — and would be the bug if
    it were not. ``test_cached_payload_is_never_the_filtered_view`` pins this.

    Cache absent or disabled -> behaves exactly as T2.3 shipped: live every call.
    """
    if cache is not None and config.cache_enabled:
        cached = cache.get_latest(config.cms_subscription_id)
        if cached is not None:
            return ProxyResult(status_code=200, body=cached)

    url = f"{config.producer_endpoint.rstrip('/')}/subscriptions/{config.cms_subscription_id}/records"
    result = _call_producer_api(
        method="GET",
        url=url,
        token_provider=token_provider,
        http_client=http_client,
        body=None,
        validate=validate_records_shape,
    )

    # Only a validated 200 is cacheable. Caching an error body would let a
    # transient producer outage pin a 502 for the whole TTL window — and a 502
    # `producer_shape_drift` body has no `records` key, so a cached one would
    # later be filtered by `_cs_filter_feed` into something that reads as "this
    # vehicle has no telemetry". That is the substitution this spec's every layer
    # refuses.
    if (
        cache is not None
        and config.cache_enabled
        and result.status_code == 200
        and isinstance(result.body, dict)
    ):
        cache.put_latest(
            config.cms_subscription_id, result.body, config.feed_cache_ttl_seconds
        )

    return result


def enroll_vin(
    *,
    vin: str,
    config: ProxyConfig,
    token_provider: TokenProvider,
    http_client: ProducerHttpClient,
) -> ProxyResult:
    """Proxy for ``POST /subscriptions/{id}/scope``.

    ``vin`` is the VIN to add. ``index.py`` MUST have already
    fleet-scope-checked this VIN against the caller's ``get_allowed_vehicle_ids()``
    before calling this — this function trusts the VIN it is given.
    """
    normalized = _normalize_vin(vin)
    if normalized is None:
        return ProxyResult(
            status_code=400,
            body={"error": "vin format invalid"},
        )
    url = (
        f"{config.producer_endpoint.rstrip('/')}"
        f"/subscriptions/{config.cms_subscription_id}/scope"
    )
    return _call_producer_api(
        method="POST",
        url=url,
        token_provider=token_provider,
        http_client=http_client,
        body={"vin": normalized},
        validate=validate_scope_add_shape,
    )


def unenroll_vin(
    *,
    vin: str,
    config: ProxyConfig,
    token_provider: TokenProvider,
    http_client: ProducerHttpClient,
) -> ProxyResult:
    """Proxy for ``DELETE /subscriptions/{id}/scope/{vin}``."""
    # ISO 3779 allow-list — see ``_VIN_ALLOWED`` and ``_normalize_vin`` above.
    # Applied here BEFORE URL construction, so a VIN containing ``?``, ``#``,
    # ``%``, whitespace, or CRLF cannot mutate the URL the producer receives.
    # The prior deny-list (``/`` and ``..``) covered only two of those cases.
    normalized = _normalize_vin(vin)
    if normalized is None:
        return ProxyResult(
            status_code=400,
            body={"error": "vin format invalid"},
        )
    url = (
        f"{config.producer_endpoint.rstrip('/')}"
        f"/subscriptions/{config.cms_subscription_id}/scope/{normalized}"
    )
    return _call_producer_api(
        method="DELETE",
        url=url,
        token_provider=token_provider,
        http_client=http_client,
        body=None,
        validate=validate_scope_remove_shape,
    )


# ── Internal ──────────────────────────────────────────────────────────────

def _call_producer_api(
    *,
    method: str,
    url: str,
    token_provider: TokenProvider,
    http_client: ProducerHttpClient,
    body: Optional[dict[str, Any]],
    validate: Optional[Callable[[Any], None]] = None,
) -> ProxyResult:
    """Single point where a producer HTTP call happens.

    Token acquisition, header assembly, error-shape mapping — all here so a
    future audit that asks "where does CMS's subscriber credential go on
    the wire" has one answer.

    ``validate`` is the caller's route-specific shape contract, applied **only
    to a 2xx body**. It is a parameter rather than a lookup inside this function
    because the route semantics live with the public function that knows them,
    while the network call stays in one place. Passing ``None`` skips validation
    and exists only for the Group 1 tests that predate T2.1's contracts;
    production paths all pass one.
    """
    try:
        token = token_provider.get_token()
    except Exception as e:
        # A failure to acquire a token is a CMS-side config problem, not a
        # producer problem. Return 502 with a distinct marker so the UI can
        # say "feed unavailable — configuration issue" if it wants.
        logger.error("TokenProvider failure: %s", type(e).__name__)
        return ProxyResult(
            status_code=502,
            body={"error": ERROR_CONFIG_MISSING, "detail": "token acquisition failed"},
        )

    if not token:
        return ProxyResult(
            status_code=502,
            body={
                "error": ERROR_CONFIG_MISSING,
                "detail": "empty token from provider",
            },
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    status, parsed = http_client.request(method=method, url=url, headers=headers, body=body)

    if status == 0:
        # Network failure — 503 (server temporarily unable to fulfil the
        # request), matching the DMS proxy's error surface for unreachable
        # upstream.
        return ProxyResult(
            status_code=503,
            body={
                "error": ERROR_PRODUCER_UNAVAILABLE,
                "detail": parsed.get("error", "unknown"),
            },
        )

    if 200 <= status < 300:
        if validate is not None:
            try:
                validate(parsed)
            except ShapeDrift as drift:
                # Fail closed. A 502 rather than passing the body through: the
                # producer answered, but CMS can no longer prove it understands
                # the answer, and serving a half-understood payload is how a
                # missing key becomes a card that reads "no telemetry" when the
                # truth is "100 records we failed to parse".
                logger.error("Producer response shape drift: %s", drift)
                return ProxyResult(
                    status_code=502,
                    body={
                        "error": ERROR_PRODUCER_SHAPE_DRIFT,
                        "detail": str(drift),
                    },
                )
        return ProxyResult(status_code=200, body=parsed)

    if status == 404:
        # Not-enrolled is a normal state, not an error.
        return ProxyResult(status_code=404, body=parsed)

    if status in (401, 403):
        # CMS's subscriber credential was rejected. The end user did not
        # cause this; it is a CMS-side config/rotation issue.
        return ProxyResult(
            status_code=502,
            body={
                "error": ERROR_PRODUCER_UNAUTHORIZED,
                "detail": parsed.get("error", "producer rejected CMS credential"),
            },
        )

    # All other producer errors — 400 (malformed request from CMS), 5xx,
    # etc. — treat as producer_unavailable rather than proxying through the
    # raw status. A 400 here means CMS built a bad request, which is a bug
    # to fix in this module, not to leak to the frontend as a client error.
    return ProxyResult(
        status_code=502,
        body={
            "error": ERROR_PRODUCER_UNAVAILABLE,
            "producer_status": status,
            "detail": parsed.get("error") if isinstance(parsed, dict) else None,
        },
    )

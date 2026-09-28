# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Subscription ownership scoping from the caller's Cognito list claim.

Spec `2026-09-10-cms-connected-services-subscriptions`, T1.3 (Group 1).

## Relationship to `oem1/_lib/fleet_membership.py` — replicated, not imported

Per spec D1, this module **replicates the idiom** of
`services/connectors/oem1/_lib/fleet_membership.py:49-54` (`parse_fleet_ids`)
and deliberately does **not** import or extend it. The two solve the same shape
of problem — "is this caller in the list named by a Cognito custom attribute?" —
for two unrelated authorization models:

  * `custom:fleetIds`        → a vehicle joining a *fleet* for OEM connectivity
  * `custom:subscriptionIds` → a vehicle's *data* joining a *feed* for a
                               third-party subscriber

Extending `fleet_membership.py` to serve both would couple those models and is
the higher-risk path D1 rejects. The duplication is intentional and small.

## One deliberate divergence from `parse_fleet_ids`

`parse_fleet_ids` returns an empty set for *both* "claim absent" and "claim
blank", which collapses two states a subscription-scoped route must distinguish:

  * a caller with **no subscriptions** is a legitimate 403 — nothing is wrong,
    they simply do not own the thing they asked for;
  * a token that reached an authenticated route carrying a **structurally
    broken** claim is a *misconfiguration*, and silently treating it as "owns
    nothing" hides a provisioning bug behind a plausible-looking denial.

T1.3's Accept therefore requires a **typed exception, not a bare `False`**, so a
caller can tell the two apart. `is_owner()` returns `False` for the first and
raises `MalformedSubscriptionClaimError` for the second.

Both outcomes still **deny access** — this is fail-closed. The distinction is
about what the handler *reports* (403 vs 500) and therefore what an operator
sees in the logs, not about whether the request proceeds.
"""
from __future__ import annotations

#: The Cognito custom attribute carrying the caller's subscription list.
#: Cognito custom attributes are scalar strings, so a "list" claim is by
#: convention comma-joined — the same encoding `custom:fleetIds` uses.
SUBSCRIPTION_IDS_CLAIM = "custom:subscriptionIds"


class SubscriberScopeError(Exception):
    """Base class for scope-resolution failures.

    Handlers that do not care *why* resolution failed can catch this one type.
    """


class MalformedSubscriptionClaimError(SubscriberScopeError):
    """The `custom:subscriptionIds` claim is present but not usable.

    Raised when the claim exists and is non-empty yet cannot be parsed into a
    list of subscription ids — i.e. a provisioning or token-shaping bug, not an
    ordinary "caller does not own this" denial.

    Deliberately distinct from "claim absent" and from "claim present but
    empty", both of which are ordinary states that yield an empty scope. See
    the module docstring for why this distinction is load-bearing.
    """


class MissingSubscriptionClaimError(SubscriberScopeError):
    """The `custom:subscriptionIds` claim is absent or blank.

    Callers that require a subscriber principal (rather than merely checking
    ownership of one id) can use `require_scope()` to turn this state into an
    explicit error. `is_owner()` does **not** raise this — an absent claim is a
    legitimate "owns nothing", which is a `False`, not a fault.
    """


def parse_subscription_ids(claims: dict) -> set[str]:
    """Return the set of subscription ids from the caller's list claim.

    Mirrors `fleet_membership.parse_fleet_ids`' contract: comma-joined string,
    per-element `strip()`, empty elements dropped, absent/blank claim yields the
    empty set.

    Args:
        claims: Cognito authorizer claims dict from the API Gateway event, i.e.
            ``event["requestContext"]["authorizer"]["claims"]``.

    Returns:
        Set of non-empty, stripped subscription ids. Empty set when the claim is
        absent or blank.

    Raises:
        MalformedSubscriptionClaimError: The claim is present and non-empty but
            is not a string (e.g. a dict or a number, which means something
            upstream is shaping the token wrongly), or is a string that contains
            only delimiters and whitespace (e.g. ``",,"``) — non-empty input
            that yields nothing is a broken value, not an empty list.
        MalformedSubscriptionClaimError: ``claims`` itself is not a dict.
    """
    if not isinstance(claims, dict):
        raise MalformedSubscriptionClaimError(
            f"claims must be a dict, got {type(claims).__name__}"
        )

    raw = claims.get(SUBSCRIPTION_IDS_CLAIM)

    # Absent claim → owns nothing. Not an error: an operator-provisioned
    # subscriber starts with no subscriptions until they create one.
    if raw is None:
        return set()

    # A list would be a *reasonable* shape, but it is not the shape Cognito
    # produces for a custom attribute, and silently accepting it would let a
    # future caller pass a shape this codebase never actually receives — hiding
    # the mismatch rather than surfacing it. Reject explicitly.
    if not isinstance(raw, str):
        raise MalformedSubscriptionClaimError(
            f"{SUBSCRIPTION_IDS_CLAIM} must be a comma-joined string "
            f"(Cognito custom attributes are scalar strings), got "
            f"{type(raw).__name__}"
        )

    # Blank claim → owns nothing, same as absent. Cognito renders an unset
    # custom attribute as "" in some token paths, so "" must not be an error.
    if not raw.strip():
        return set()

    parsed = {s.strip() for s in raw.split(",") if s.strip()}

    # Non-empty input that parses to nothing means the value was all delimiters
    # (",", ", ,"). The caller *has* a claim, so this is a broken value rather
    # than an empty list — the exact case the typed exception exists for.
    if not parsed:
        raise MalformedSubscriptionClaimError(
            f"{SUBSCRIPTION_IDS_CLAIM} contained no parseable ids: {raw!r}"
        )

    return parsed


def is_owner(subscription_id: str, claims: dict) -> bool:
    """DEPRECATED 2026-09-12 — NOT the ownership authority. Do not call.

    **No handler calls this.** Ownership is resolved from the Subscription row's
    `consumer_id` (read paths) or from the write's
    `consumer_id = :caller` ConditionExpression (write paths). See
    `issues/2026-09-12-subscription-ownership-claim-has-no-writer/`.

    Why it was retired: this reads `custom:subscriptionIds`, and **nothing writes
    that claim**. `POST /subscriptions` creates the row without touching the
    caller's Cognito attributes, so this function returned False for every
    legitimate owner, on every request, permanently — re-authenticating did not
    help. Meanwhile `GET /subscriptions` authorized from the row and returned the
    same subscriptions happily. Two authorities, one unimplemented.

    It is kept, unused, only because its tests document the claim-parsing rules
    that `parse_subscription_ids` still provides for `vehicles_available`. If you
    are reaching for an ownership check, use the row.

    Returns:
        True when `subscription_id` appears in the caller's claim list — which,
        given the above, is currently never in production.

    Raises:
        MalformedSubscriptionClaimError: The claim is unusable, or
            `subscription_id` is not a non-empty string.
    """
    if not isinstance(subscription_id, str) or not subscription_id.strip():
        raise MalformedSubscriptionClaimError(
            f"subscription_id must be a non-empty string, got {subscription_id!r}"
        )

    # Exact match on the stripped id. No prefix/substring matching, no
    # case-folding: subscription ids are ULIDs and an inexact comparison here
    # would be an ownership bypass, not a convenience.
    return subscription_id.strip() in parse_subscription_ids(claims)


def require_scope(claims: dict) -> set[str]:
    """Return the caller's subscription ids, raising if they hold none.

    For routes that need a subscriber principal at all (e.g. "list my
    subscriptions") rather than ownership of one specific id.

    Raises:
        MissingSubscriptionClaimError: The caller's claim is absent or blank.
        MalformedSubscriptionClaimError: The claim is unusable.
    """
    scope = parse_subscription_ids(claims)
    if not scope:
        raise MissingSubscriptionClaimError(
            f"caller holds no {SUBSCRIPTION_IDS_CLAIM} entries"
        )
    return scope

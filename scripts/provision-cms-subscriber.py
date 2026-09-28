#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Provision CMS's own subscriber account against the producer's Connected Services API.

Spec: ``.kiro/specs/2026-09-10-cms-connected-services-consumer/`` T1.1 (Group 1).
Extended: ``.kiro/specs/2026-09-11-cms-cs-meridian-ingestion/`` T3.4 (multi-product).

## What this script does

1. Calls the producer's ``POST /admin/subscribers`` to create a Cognito user in
   the producer's pool (which is CMS's own pool — see docs/tech.md T1.3
   finding), with the ``subscriber`` group.
2. Signs in as that user to obtain a token, then calls the producer's
   ``POST /subscriptions`` to create CMS's telemetry subscription(s).
3. Writes the resulting ``subscription_id`` + credential reference into AWS
   Secrets Manager (one entry per product — see Secrets Manager naming below).

## Extension note

Task ``tasks.md`` T1.1 names ``scripts/provision-cms-subscriber.sh``. This
script is Python, not bash, for three reasons captured in ``decisions.md``:
JSON handling, boto3 idempotency, and testability of the dry-run mode. It
lives at ``scripts/provision-cms-subscriber.py`` for those reasons and is
still an operator tool run once per environment (NOT wired into deploy).

## Secrets Manager naming

Single-product default (backward-compatible secret name, unchanged since T1.1)::

    cms-{stage}-connected-services-subscriber-{region}-{account}

This name is used when no ``--product-id`` flag is passed, OR when the sole
product is ``telemetry-hifi-v1``.

Per-product namespaced name (used for any product other than telemetry-hifi-v1)::

    cms-{stage}-connected-services-{product-id}-{region}-{account}

Examples::

    # default / telemetry-hifi-v1:
    cms-staging-connected-services-subscriber-us-west-2-123456789012

    # meridian-telemetry-v1:
    cms-staging-connected-services-meridian-telemetry-v1-us-west-2-123456789012

This naming convention ensures the CMS puller Lambda can look up each
product's subscription credentials independently via a predictable, scoped
secret name.

## Idempotency

Running the script twice against the same environment does not create a
second subscriber or a second subscription. The check-then-create logic:

1. Read the Secrets Manager entry. If it exists AND references a
   ``subscription_id`` that is still valid at the producer (verified by a
   GET), the script exits 0 with "already provisioned".
2. If the secret is missing but a subscriber account already exists in the
   producer's pool with the well-known username, sign in as that account
   and check for an existing subscription. Reuse if present.
3. Only create when neither side has evidence of prior provisioning.

## Dry-run mode

``--dry-run`` executes the whole flow against an in-process mock server
(returning the producer's documented response shapes from spec.md Design
table) — no network calls, no AWS calls. Confirms the script's
Secrets-Manager write logic works independent of a live producer.

## Usage

Single product (default — backward-compatible)::

    export DEPLOYMENT_STAGE=staging
    export AWS_REGION=us-west-2
    export PRODUCER_API_ENDPOINT=https://<producer-api-gw-url>
    python3 scripts/provision-cms-subscriber.py

Multiple products::

    python3 scripts/provision-cms-subscriber.py \\
        --product-id telemetry-hifi-v1 \\
        --product-id meridian-telemetry-v1

Dry-run (safe to run now, before the producer exists)::

    python3 scripts/provision-cms-subscriber.py --dry-run
    python3 scripts/provision-cms-subscriber.py --dry-run --product-id meridian-telemetry-v1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import string
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    """Return ``os.environ[name]`` or raise with the remediation.

    Added in T2.0c: two call sites referenced this helper before it existed.
    The module still imported cleanly — `from __future__ import annotations`
    defers annotation evaluation and a NameError inside a function body is not
    raised until the body runs — so the failure was reserved for the first live
    provisioning run, which is the one path with no test coverage. Hence the
    two tests that call through it.
    """
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"{name} is required for a live run. Source it from "
            f"deployment/config/<stage>.env, which defines both "
            f"COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID."
        )
    return value

# ── Product identifier (spec D4) ──────────────────────────────────────────

#: The one telemetry product CMS subscribes to. Must exactly match a
#: ``product_id`` in the producer's ``products.json`` (producer T1.4) — pinned
#: by ``TestProductIdContract``, because a value that is merely plausible
#: fails only at live provisioning time (consumer T2.2), against staging.
#: Hardcoded rather than sourced from an env var because CMS's own
#: subscription is a fixed operational fact — see spec D4 ("one, telemetry,
#: seeded once"), and a typo in the env value would silently subscribe CMS
#: to the wrong product.
#:
#: **Corrected 2026-09-12 (FG2.2).** This read ``"telemetry"``, which is not
#: a product id at all — it is the value of the ``source`` *field* on the
#: telemetry product. The producer's catalog ids are ``telemetry-hifi-v1``
#: and ``meridian-telemetry-v1``, so ``POST /subscriptions`` would have named
#: a product the catalog does not contain, first failing at T2.2 against live
#: staging.
#:
#: Secondary effect, on the multi-product path only: ``_provision_one_product``
#: derives its secret name via ``product_secret_name(product_id)``, so a
#: ``product_id`` outside the legacy branch relocates the credential to
#: ``cms-{stage}-connected-services-telemetry-{region}-{account}``. The
#: single-product ``run()`` path is unaffected — it uses the
#: ``Provisioner.secret_name`` property, which is independent of
#: ``product_id`` and always returns the subscriber-level name T1.1's Accept
#: criteria mandate.
TELEMETRY_PRODUCT_ID = "telemetry-hifi-v1"

#: The well-known username for CMS's subscriber account. Deterministic so
#: the check-then-create step in idempotency has something to look up
#: against the producer, even if the Secrets Manager entry is missing.
#: **Must be an email address.** The CMS pool is configured with
#: ``UsernameAttributes: ["email"]``, so Cognito requires the username to be a
#: valid email; and the producer's admin route derives the username from the
#: body's ``email`` field outright (`admin_provision_subscriber/handler.py:452`,
#: ``subscriber_username = sanitised["email"]``) — it ignores any
#: ``subscriber_username`` the caller sends.
#:
#: Corrected 2026-09-12 on the first live run. This read
#: ``"cms-{stage}-subscriber"``, which is not an email and therefore was never a
#: creatable username in this pool. Nothing before a live call could reveal it:
#: ``MockProducerAdmin`` accepts any string as a username, so all 59 tests
#: passed against an identity Cognito would have rejected.
CMS_SUBSCRIBER_USERNAME_TEMPLATE = "cms-{stage}-subscriber@example.com"

#: Product id that continues to use the original (backward-compatible)
#: single-subscriber secret name. Any other product id gets a namespaced
#: secret (see ``product_secret_name()`` below).
_LEGACY_PRODUCT_ID = "telemetry-hifi-v1"


def product_secret_name(stage: str, region: str, account: str, product_id: str) -> str:
    """Return the Secrets Manager secret name for a given product subscription.

    Naming convention (see module docstring for examples):

    * ``product_id == "telemetry-hifi-v1"`` — returns the backward-compatible
      subscriber-level secret name so that the existing puller Lambda and
      any provisioned environments are not disturbed.
    * Any other ``product_id`` — returns a namespaced, product-specific name
      so multiple products can be looked up independently.

    All names are region + account suffixed per cross-region-namespace-discipline.md
    check 1.
    """
    if product_id == _LEGACY_PRODUCT_ID:
        return (
            f"cms-{stage}-connected-services-subscriber-{region}-{account}"
        )
    return f"cms-{stage}-connected-services-{product_id}-{region}-{account}"


# ── Interfaces (for real vs mock swap) ────────────────────────────────────

class ProducerAdmin:
    """Interface for the producer's admin API. Two implementations below."""

    def create_subscriber(self, username: str, password: str) -> dict[str, Any]:
        """Create the subscriber account at the producer.

        Returns ``{'username': ..., 'sub': ...}`` and **may** additionally
        return ``'temp_password'``.

        The ``password`` argument is a *proposal*, not a mandate. The
        producer's real route ignores it: `admin_provision_subscriber` calls
        `_generate_temp_password()` itself and returns the value it chose under
        `temp_password`, exactly once, with
        `TemporaryPasswordValidityDays=7` bounding it. So callers MUST prefer
        `result['temp_password']` when present, and fall back to the proposed
        value only for implementations that honour it (the mock).

        Discovered at T2.0c: `run()` previously generated a password, passed
        it here, and then signed in with the value it generated. Against the
        mock that works, because the mock honours the argument. Against the
        real route it cannot — the account's password is whatever the producer
        chose — so sign-in would fail with `NotAuthorizedException` on the
        first live run and look like a Cognito problem.
        """
        raise NotImplementedError

    def find_subscriber(self, username: str) -> Optional[dict[str, Any]]:
        """Return None if not found, else {'username': ..., 'sub': ...}."""
        raise NotImplementedError

    def sign_in(self, username: str, password: str) -> str:
        """Return a token usable as `Authorization: Bearer` at the producer.

        Must be the **IdToken**: the producer fronts its API with a
        `CognitoUserPoolsAuthorizer`, which validates `aud` — a claim only the
        IdToken carries. See `connected_services_proxy.COGNITO_AUTH_FLOW`.
        """
        raise NotImplementedError

    def list_subscriptions(self, access_token: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_subscription(
        self, access_token: str, product_id: str
    ) -> dict[str, Any]:
        raise NotImplementedError


class SecretsWriter:
    """Interface for the Secrets Manager write. Real vs in-memory."""

    def get(self, secret_name: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def put(self, secret_name: str, value: dict[str, Any]) -> None:
        raise NotImplementedError


# ── Mock implementations (used by --dry-run) ──────────────────────────────

@dataclass
class MockProducerAdmin(ProducerAdmin):
    """In-process mock — returns the producer's documented response shapes.

    Shape assumptions are drawn from the consumer spec's Design table AND
    the producer's ``spec.md`` D3 / API surface table. If the producer
    changes those shapes during its Phase 2, T2.1 (Group 2) will fail with
    ``ERROR_PRODUCER_SHAPE_DRIFT`` and this mock is updated.
    """

    subscribers: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions_by_user: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict
    )

    def create_subscriber(self, username: str, password: str) -> dict[str, Any]:
        if username in self.subscribers:
            # Producer's admin route should reject a duplicate — mock does the same.
            raise RuntimeError("subscriber already exists")
        sub = f"mock-sub-{len(self.subscribers) + 1:03d}"
        self.subscribers[username] = {"username": username, "sub": sub}
        self.subscriptions_by_user[sub] = []
        # Echo the proposal back under the same key the real route uses, so
        # callers exercise the `result['temp_password']` path in dry-run too.
        # Without this the mock would be the only implementation whose return
        # value lacks the key, and the branch that reads it would go untested.
        return {"username": username, "sub": sub, "temp_password": password}

    def find_subscriber(self, username: str) -> Optional[dict[str, Any]]:
        return self.subscribers.get(username)

    def sign_in(self, username: str, password: str) -> str:
        if username not in self.subscribers:
            raise RuntimeError("no such subscriber")
        return f"mock-token-for-{username}"

    def list_subscriptions(self, access_token: str) -> list[dict[str, Any]]:
        # The token embeds the username in the mock; producer's real
        # `list_subscriptions` reads `sub` from the JWT.
        username = access_token.removeprefix("mock-token-for-")
        sub = self.subscribers[username]["sub"]
        return list(self.subscriptions_by_user.get(sub, []))

    def create_subscription(
        self, access_token: str, product_id: str
    ) -> dict[str, Any]:
        username = access_token.removeprefix("mock-token-for-")
        sub = self.subscribers[username]["sub"]
        subscription_id = f"mock-sub-id-{len(self.subscriptions_by_user[sub]) + 1:03d}"
        row = {
            "subscription_id": subscription_id,
            "consumer_id": sub,
            "product_id": product_id,
            "state": "active",
        }
        self.subscriptions_by_user[sub].append(row)
        return row


@dataclass
class MockSecretsWriter(SecretsWriter):
    """In-memory secrets — for dry-run only."""

    store: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, secret_name: str) -> Optional[dict[str, Any]]:
        return self.store.get(secret_name)

    def put(self, secret_name: str, value: dict[str, Any]) -> None:
        self.store[secret_name] = dict(value)


# ── Real implementations ──────────────────────────────────────────────────

class RealSecretsWriter(SecretsWriter):
    """Real Secrets Manager writes. Import-guarded to keep dry-run free of boto3.

    The real write PUTs an existing secret's new version or creates the
    secret on first run. Idempotent by design — a second run with the same
    payload is a no-op update (Secrets Manager tolerates that).
    """

    def __init__(self, region: str) -> None:
        try:
            import boto3
        except ImportError:  # pragma: no cover - deferred to real-mode users
            raise RuntimeError(
                "boto3 not available — run in --dry-run mode, or install "
                "boto3 in the environment where you run this script."
            )
        self._client = boto3.client("secretsmanager", region_name=region)

    def get(self, secret_name: str) -> Optional[dict[str, Any]]:
        try:
            resp = self._client.get_secret_value(SecretId=secret_name)
        except self._client.exceptions.ResourceNotFoundException:
            return None
        return json.loads(resp["SecretString"])

    def put(self, secret_name: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value)
        try:
            self._client.put_secret_value(SecretId=secret_name, SecretString=payload)
        except self._client.exceptions.ResourceNotFoundException:
            self._client.create_secret(
                Name=secret_name,
                Description=(
                    "CMS-owned Connected Services subscriber credential — "
                    "spec 2026-09-10-cms-connected-services-consumer T1.1."
                ),
                SecretString=payload,
            )


# ── Orchestration ─────────────────────────────────────────────────────────

@dataclass
class Provisioner:
    stage: str
    region: str
    account: str
    producer_admin: ProducerAdmin
    secrets: SecretsWriter
    #: Called as ``(username, password)`` immediately after the account is
    #: created and **before** ``sign_in``, to clear FORCE_CHANGE_PASSWORD.
    #:
    #: Wired rather than left as a documented manual step (FG3.2): review of
    #: T2.0c noted ``ensure_permanent_password`` had zero call sites, so a
    #: refactor could silently drop it with no test failing. It is also the step
    #: this spec's own handoff called "mandatory and easy to forget" — which is
    #: an argument for automating it, not for documenting it harder. A forgotten
    #: promotion does not fail here; it fails later, in the proxy Lambda, as an
    #: unanswerable Cognito challenge.
    #:
    #: ``None`` in dry-run, where there is no real account to promote.
    password_promoter: Optional[Callable[[str, str], None]] = None

    @property
    def username(self) -> str:
        return CMS_SUBSCRIBER_USERNAME_TEMPLATE.format(stage=self.stage)

    @property
    def secret_name(self) -> str:
        # Region-suffixed per cross-region-namespace-discipline.md check 1.
        # Secrets Manager secret names are account-region scoped, so this
        # suffix is not strictly required for AWS-level uniqueness — applied
        # for the same discipline reason the DDB tables in this repo carry
        # it.
        return (
            f"cms-{self.stage}-connected-services-subscriber-"
            f"{self.region}-{self.account}"
        )

    def _generate_password(self) -> str:
        """Random 32-char password satisfying Cognito's default policy."""
        # Cognito default: >= 8 chars, uppercase + lowercase + digit + symbol.
        # 32 chars from a broad alphabet gives ~192 bits of entropy.
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
        # Guarantee each class is represented (rejection-sample rather than
        # constructing to preserve entropy uniformity).
        while True:
            candidate = "".join(secrets.choice(alphabet) for _ in range(32))
            if (
                any(c.isupper() for c in candidate)
                and any(c.islower() for c in candidate)
                and any(c.isdigit() for c in candidate)
                and any(c in "!@#$%^&*()-_=+" for c in candidate)
            ):
                return candidate

    def _promote_password(self, password: str) -> None:
        """Clear FORCE_CHANGE_PASSWORD on the freshly-created account.

        No-op when no promoter is injected (dry-run). Deliberately not
        swallowing failures: an unpromoted account looks fine here and fails
        later in the proxy Lambda as an unanswerable Cognito challenge, which
        is much harder to attribute.
        """
        if self.password_promoter is None:
            logger.info("No password promoter injected (dry-run) — skipping")
            return
        logger.info("Promoting temp password to permanent for %s", self.username)
        self.password_promoter(self.username, password)

    def run(self) -> dict[str, Any]:
        """Provision (or confirm already-provisioned). Return the final state.

        Return payload deliberately excludes the password — the caller can
        print / log the returned dict without leaking the credential. Only
        the Secrets Manager write carries the password.
        """
        # ── Step 1: check Secrets Manager for an existing entry. ──────────
        existing = self.secrets.get(self.secret_name)
        if existing and existing.get("subscription_id"):
            # Confirm the referenced subscription is still valid at the
            # producer. This costs one round trip but catches the
            # degraded-state case where the secret survives but the
            # subscription was revoked out-of-band.
            try:
                token = self.producer_admin.sign_in(
                    existing["username"], existing["password"]
                )
                subs = self.producer_admin.list_subscriptions(token)
                for s in subs:
                    if s["subscription_id"] == existing["subscription_id"]:
                        logger.info(
                            "Already provisioned — secret + subscription both live"
                        )
                        return {
                            "action": "noop",
                            "subscription_id": existing["subscription_id"],
                            "username": existing["username"],
                            "secret_name": self.secret_name,
                        }
                logger.warning(
                    "Secret references subscription_id=%s but producer has no "
                    "matching subscription — re-provisioning subscription only",
                    existing["subscription_id"],
                )
                # Reuse the credential (same user, same password), create a
                # new subscription. Fall through.
                subscription = self.producer_admin.create_subscription(
                    token, TELEMETRY_PRODUCT_ID
                )
                payload = dict(existing)
                payload["subscription_id"] = subscription["subscription_id"]
                self.secrets.put(self.secret_name, payload)
                return {
                    "action": "recreated_subscription",
                    "subscription_id": subscription["subscription_id"],
                    "username": existing["username"],
                    "secret_name": self.secret_name,
                }
            except Exception as e:
                logger.error(
                    "Secret exists but producer verification failed: %s. "
                    "Not overwriting — inspect manually.",
                    e,
                )
                raise

        # ── Step 2: no secret. Check producer for an orphaned account. ────
        existing_subscriber = self.producer_admin.find_subscriber(self.username)
        if existing_subscriber:
            # A previous partial run left a subscriber account without a
            # corresponding Secrets Manager entry. That means the password
            # was NOT persisted, so we cannot sign in. This is a
            # human-decision point — orphaned account must be cleaned up
            # by an operator before this script can proceed.
            raise RuntimeError(
                f"Subscriber '{self.username}' already exists at producer but "
                f"secret '{self.secret_name}' is missing. This is an "
                f"orphaned-account state from a previous partial run. "
                f"Delete the producer-side account manually and re-run this "
                f"script, OR find the original password and hand-write the "
                f"Secrets Manager entry."
            )

        # ── Step 3: neither side has state — create both. ─────────────────
        password = self._generate_password()
        logger.info("Creating subscriber %s at producer", self.username)
        subscriber = self.producer_admin.create_subscriber(self.username, password)

        # The producer's real route generates its own temp password and returns
        # it once (see ProducerAdmin.create_subscriber). Prefer it over the
        # value we proposed — signing in with our own would fail against the
        # real route, and it is the returned value that the account actually
        # has. The mock honours the proposal and echoes it back, so this is a
        # no-op there.
        password = subscriber.get("temp_password") or password

        # Promote BEFORE sign_in, not after: the account is in
        # FORCE_CHANGE_PASSWORD until this runs, and sign_in against that state
        # returns a challenge with no tokens. Ordering is pinned by
        # TestPasswordPromotion.
        self._promote_password(password)

        logger.info("Signing in as new subscriber")
        token = self.producer_admin.sign_in(self.username, password)

        logger.info(
            "Creating telemetry subscription for product_id=%s", TELEMETRY_PRODUCT_ID
        )
        subscription = self.producer_admin.create_subscription(
            token, TELEMETRY_PRODUCT_ID
        )

        payload = {
            "username": self.username,
            "password": password,  # only written to Secrets Manager
            "consumer_id": subscriber["sub"],
            "subscription_id": subscription["subscription_id"],
            "product_id": TELEMETRY_PRODUCT_ID,
        }
        logger.info("Writing Secrets Manager entry %s", self.secret_name)
        self.secrets.put(self.secret_name, payload)

        # Never return the password in the summary — the Secrets Manager
        # write is the authoritative sink; a printed CLI log could leak it.
        return {
            "action": "created",
            "subscription_id": subscription["subscription_id"],
            "username": self.username,
            "secret_name": self.secret_name,
        }

    def _provision_product(
        self, token: str, password: str, product_id: str
    ) -> dict[str, Any]:
        """Provision a single product subscription for an already-authenticated subscriber.

        Called by ``run_multi()`` after the subscriber account is confirmed to
        exist and a valid ``token`` has been obtained.  Returns a per-product
        result dict (mirrors the shape returned by ``run()``).

        Idempotency: checks the per-product secret first; if a valid
        subscription already exists at the producer the method returns
        ``{"action": "noop", ...}``.
        """
        sec_name = product_secret_name(
            self.stage, self.region, self.account, product_id
        )

        # Check whether the product's secret already references a live subscription.
        existing = self.secrets.get(sec_name)
        if existing and existing.get("subscription_id"):
            subs = self.producer_admin.list_subscriptions(token)
            for s in subs:
                if s["subscription_id"] == existing["subscription_id"]:
                    logger.info(
                        "Product %s already provisioned — subscription still live",
                        product_id,
                    )
                    return {
                        "action": "noop",
                        "product_id": product_id,
                        "subscription_id": existing["subscription_id"],
                        "secret_name": sec_name,
                    }
            # Secret exists but subscription is gone — recreate.
            logger.warning(
                "Secret for product %s references subscription_id=%s but "
                "producer has no matching subscription — recreating",
                product_id,
                existing["subscription_id"],
            )
            subscription = self.producer_admin.create_subscription(token, product_id)
            payload = dict(existing)
            payload["subscription_id"] = subscription["subscription_id"]
            self.secrets.put(sec_name, payload)
            return {
                "action": "recreated_subscription",
                "product_id": product_id,
                "subscription_id": subscription["subscription_id"],
                "secret_name": sec_name,
            }

        # No existing secret — create the subscription and write the secret.
        logger.info(
            "Creating subscription for product_id=%s", product_id
        )
        subscription = self.producer_admin.create_subscription(token, product_id)
        payload = {
            "username": self.username,
            "password": password,
            "consumer_id": self.producer_admin.find_subscriber(self.username)["sub"],
            "subscription_id": subscription["subscription_id"],
            "product_id": product_id,
        }
        self.secrets.put(sec_name, payload)
        return {
            "action": "created",
            "product_id": product_id,
            "subscription_id": subscription["subscription_id"],
            "secret_name": sec_name,
        }

    def run_multi(self, product_ids: list[str]) -> list[dict[str, Any]]:
        """Provision subscriptions for one or more products.

        Creates (or confirms) the CMS subscriber account once, then iterates
        over ``product_ids`` to provision a subscription + Secrets Manager
        entry per product.

        The subscriber-account creation step re-uses the same check-then-create
        idempotency as ``run()``.  If the subscriber already exists and
        credentials were persisted to at least one product's secret, the
        existing password is recovered from that secret — so the script remains
        idempotent even when called with a superset of products on the second
        run.

        Returns a list of per-product result dicts.
        """
        if not product_ids:
            raise ValueError("run_multi requires at least one product_id")

        # ── Resolve subscriber credentials (idempotent) ───────────────────
        password: Optional[str] = None

        # Try to recover the password from any existing product secret.
        for pid in product_ids:
            existing = self.secrets.get(
                product_secret_name(self.stage, self.region, self.account, pid)
            )
            if existing and existing.get("password"):
                password = existing["password"]
                break

        # Also check the legacy subscriber secret as a fallback.
        if password is None:
            legacy_secret = self.secrets.get(self.secret_name)
            if legacy_secret and legacy_secret.get("password"):
                password = legacy_secret["password"]

        existing_subscriber = self.producer_admin.find_subscriber(self.username)

        if existing_subscriber and password is None:
            # Orphaned subscriber — same guard as in run().
            raise RuntimeError(
                f"Subscriber '{self.username}' already exists at producer but "
                f"no secret with a recoverable password was found. This is an "
                f"orphaned-account state from a previous partial run. "
                f"Delete the producer-side account manually and re-run, OR "
                f"hand-write the Secrets Manager entry."
            )

        if existing_subscriber is None:
            # Fresh provisioning — create subscriber and persist the password.
            password = self._generate_password()
            logger.info(
                "Creating subscriber %s at producer (multi-product run)",
                self.username,
            )
            created = self.producer_admin.create_subscriber(self.username, password)
            # Same correction as run(): the real route chooses the password and
            # returns it once. Using our proposal here would persist a password
            # the account does not have.
            password = created.get("temp_password") or password
            self._promote_password(password)

        # Authenticate once; reuse the token for all products in this run.
        token = self.producer_admin.sign_in(self.username, password)

        # ── Per-product provisioning ──────────────────────────────────────
        results: list[dict[str, Any]] = []
        for pid in product_ids:
            result = self._provision_product(token, password, pid)
            results.append(result)

        return results


# ── CLI ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision CMS's own Connected Services subscriber account."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run against an in-process mock producer + in-memory secrets. "
            "No network calls, no AWS calls. Use this before the producer's "
            "Group 3 gate lands on staging."
        ),
    )
    parser.add_argument(
        "--stage",
        default=os.environ.get("DEPLOYMENT_STAGE", "dev"),
        help="Deployment stage (default: $DEPLOYMENT_STAGE or 'dev')",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-west-2"),
        help="AWS region (default: $AWS_REGION or 'us-west-2')",
    )
    parser.add_argument(
        "--account",
        default=os.environ.get("AWS_ACCOUNT_ID", ""),
        help="AWS account ID (default: $AWS_ACCOUNT_ID; required unless --dry-run)",
    )
    parser.add_argument(
        "--product-id",
        dest="product_ids",
        action="append",
        metavar="PRODUCT_ID",
        default=None,
        help=(
            "Product id to subscribe to.  May be repeated to provision multiple "
            "subscriptions in a single run: "
            "``--product-id telemetry-hifi-v1 --product-id meridian-telemetry-v1``. "
            "When omitted the script uses the single-product default path (backward "
            "compatible with existing invocations)."
        ),
    )
    return parser.parse_args()


def _build_real_producer_admin() -> ProducerAdmin:
    """Construct the live ``ProducerAdmin``. T2.0c.

    Two credentials are in play and they are not interchangeable:

    * The ``/admin/*`` routes are Cognito-authorized and require an
      **operator's** IdToken (a caller in the producer's admin group). That is
      supplied out-of-band via ``CS_OPERATOR_ID_TOKEN`` — this script is
      operator-run, so the operator already has a session; minting one here
      would mean handling the operator's own credentials, which we do not want
      in a provisioning script.
    * The subscription routes are called as **CMS's own subscriber**, using the
      token ``sign_in`` returns.

    Shapes verified against the producer's committed handler, NOT against a
    live call — this script has never run against the deployed route (T2.2 is
    the task that does that, and it is blocked). Response parsing therefore
    fails **loudly** on an unexpected shape rather than defaulting, so a drift
    surfaces as a named error and not as a mysterious downstream failure.
    """
    endpoint = os.environ.get("CS_PRODUCER_ENDPOINT", "").rstrip("/")
    operator_token = os.environ.get("CS_OPERATOR_ID_TOKEN", "")
    if not endpoint:
        raise RuntimeError(
            "CS_PRODUCER_ENDPOINT is required for a live run. Get it from the "
            "producer stack: aws cloudformation describe-stacks --stack-name "
            "cms-<stage>-subscriptions --query "
            "'Stacks[0].Outputs[?OutputKey==`SubscriptionsApiUrl`].OutputValue'"
        )
    if not operator_token:
        raise RuntimeError(
            "CS_OPERATOR_ID_TOKEN is required for a live run — the producer's "
            "/admin/subscribers route is Cognito-authorized and needs an "
            "operator IdToken (NOT an AccessToken: the producer's authorizer "
            "validates `aud`, which only the IdToken carries)."
        )
    return RealProducerAdmin(
        endpoint=endpoint,
        operator_id_token=operator_token,
        user_pool_client_id=_required_env("COGNITO_CLIENT_ID"),
    )


def ensure_permanent_password(
    username: str,
    password: str,
    user_pool_id: str,
    cognito_client: Any = None,
) -> None:
    """Promote a temp password to permanent, clearing FORCE_CHANGE_PASSWORD.

    **Why this exists.** The producer's ``POST /admin/subscribers`` creates the
    account with ``MessageAction="SUPPRESS"`` and a temp password bounded by
    ``TemporaryPasswordValidityDays=7``. That leaves the user in
    ``FORCE_CHANGE_PASSWORD``, where ``InitiateAuth`` returns a
    ``ChallengeName`` and **no tokens** — see
    ``connected_services_proxy.CognitoUserPasswordTokenProvider``. A human
    subscriber answers that challenge in a browser. CMS's machine account has
    no browser, and answering it from the proxy Lambda would mean writing a new
    password back to Secrets Manager from inside the token path.

    **Why not the route the producer suggests.** The producer's own response
    ``next_step`` says to sign in with
    ``AdminInitiateAuth (ADMIN_USER_PASSWORD_AUTH)`` and answer the challenge
    via ``AdminRespondToAuthChallenge``. That flow is **not enabled** on
    ``CMSUserPoolClient`` (see FG2.1), so an operator following the producer's
    guidance verbatim gets ``InvalidParameterException``.
    ``AdminSetUserPassword(Permanent=True)`` sidesteps it: one admin call,
    keyed on pool + username, requiring no client auth flow at all.

    Idempotent — setting an already-permanent password to the same value is a
    no-op from the caller's perspective.
    """
    if cognito_client is None:
        import boto3
        from botocore.config import Config

        cognito_client = boto3.client(
            "cognito-idp",
            config=Config(connect_timeout=3, read_timeout=5,
                          retries={"max_attempts": 2}),
        )
    try:
        cognito_client.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=username,
            Password=password,
            Permanent=True,
        )
    except Exception as e:
        # `type(e).__name__` only — `password` is a local of this frame and the
        # boto3 request carries it; interpolating the exception risks echoing it.
        raise RuntimeError(
            f"admin_set_user_password failed for {username}: "
            f"{type(e).__name__}. Without it the account stays in "
            f"FORCE_CHANGE_PASSWORD and the proxy Lambda cannot obtain a token."
        ) from None
    logger.info("Password promoted to permanent for %s", username)


@dataclass
class RealProducerAdmin(ProducerAdmin):
    """Live producer admin/subscription client. T2.0c.

    **Unverified against the deployed routes.** Request and response shapes
    here are read from the producer's committed handlers, not from a live call
    — T2.2 is the task that exercises them, and it is blocked on the producer's
    ownership fix reaching staging. Every parse therefore raises on an
    unexpected shape instead of defaulting, so the first live run reports which
    assumption was wrong rather than failing three steps later.
    """

    endpoint: str
    operator_id_token: str = field(repr=False)
    user_pool_client_id: str = ""
    #: Injected in tests. Real callers get urllib + boto3.
    http: Optional[Callable[..., tuple[int, dict[str, Any]]]] = None
    cognito_client: Any = None

    def _call(
        self,
        method: str,
        path: str,
        token: str,
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if self.http is not None:
            status, parsed = self.http(method, url, headers, body)
        else:
            import urllib.error
            import urllib.request

            # Refuse redirects. `urllib.request.urlopen` at defaults FOLLOWS
            # 3xx and, unlike `requests`, preserves the `Authorization` header
            # across a host change — CPython's
            # `HTTPRedirectHandler.redirect_request` copies every header except
            # content-length/content-type. The token here is an OPERATOR
            # IdToken with admin scope on the producer's pool, so a redirect to
            # an attacker-influenced or misconfigured host hands over
            # `/admin/subscribers` access. Mirrors the same guard in
            # `connected_services_proxy.UrllibHttpClient`, which this
            # implementation should have carried over and did not — found by
            # security review of T2.0c.
            class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
                def http_error_302(self, req, fp, code, msg, hdrs):
                    raise urllib.error.HTTPError(
                        req.full_url, code,
                        "Redirect refused — would forward the Authorization "
                        "header to another host",
                        hdrs, fp,
                    )

                http_error_301 = http_error_303 = http_error_307 = (
                    http_error_308
                ) = http_error_302

            opener = urllib.request.build_opener(_RefuseRedirects)
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(
                url, method=method, headers=headers, data=data
            )
            try:
                with opener.open(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8")
                    status, parsed = resp.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8")
                try:
                    parsed = json.loads(raw) if raw else {}
                except Exception:
                    parsed = {"error": raw[:200]}
                status = e.code
            except Exception as e:
                raise RuntimeError(
                    f"{method} {path} network failure: {type(e).__name__}"
                ) from None
        if 300 <= status < 400:
            # Reachable only via the _RefuseRedirects handler above, which
            # raises HTTPError(3xx) — caught below as a status, not an
            # exception. Without this branch a refused redirect fell between
            # `== 403` and `>= 400` and was returned as if it had succeeded,
            # so the caller reported "response missing 'username'" and the
            # actual event — a security control firing — was invisible.
            # Same wrong-diagnosis class as the _required_env hoist in FG2/T2.0c.
            raise RuntimeError(
                f"{method} {path} was redirected ({status}) and the redirect "
                f"was refused, because following it would forward the "
                f"Authorization header to another host. Check the producer "
                f"endpoint: a trailing-slash or stage-path mismatch on "
                f"API Gateway is the usual cause."
            )
        if status == 403:
            raise RuntimeError(
                f"{method} {path} returned 403. For /admin/* this means the "
                f"CS_OPERATOR_ID_TOKEN's caller is not in the producer's admin "
                f"group; for subscription routes it means the subscriber does "
                f"not own the subscription."
            )
        if status >= 400:
            raise RuntimeError(
                f"{method} {path} returned {status}: "
                f"{parsed.get('error') or parsed}"
            )
        return parsed

    def create_subscriber(self, username: str, password: str) -> dict[str, Any]:
        # `password` is deliberately NOT sent: the producer's route rejects any
        # body attempting to set privileged fields and generates the temp
        # password itself (handler docstring, "## Temp password"). Sending our
        # proposal would at best be ignored and at worst trip the
        # body-injection guard.
        # The route takes `email` and derives the username from it
        # (handler.py:452). Sending `subscriber_username` achieved nothing and
        # produced `400 email is required` on the first live call.
        resp = self._call(
            "POST", "/admin/subscribers", self.operator_id_token,
            {"email": username},
        )
        if resp.get("username") and resp["username"].lower() != username.lower():
            raise RuntimeError(
                f"asked the producer to create {username!r} but it created "
                f"{resp['username']!r}. The route derives the username from the "
                f"email, so a divergence here means our identity assumption is "
                f"wrong and every later lookup would miss."
            )
        for required in ("username", "sub"):
            if required not in resp:
                raise RuntimeError(
                    f"POST /admin/subscribers response missing {required!r} "
                    f"(got keys {sorted(resp)}). Shape drift — update "
                    f"RealProducerAdmin rather than guessing downstream."
                )
        if "temp_password" not in resp:
            raise RuntimeError(
                "POST /admin/subscribers returned no 'temp_password'. The "
                "route returns it exactly once and it cannot be recovered; "
                "without it this account is unusable and must be deleted "
                "producer-side before re-running."
            )
        return resp

    def find_subscriber(self, username: str) -> Optional[dict[str, Any]]:
        # No producer read-route for a single subscriber exists (T5.2, the
        # roster view, is deferred). Ask Cognito directly — this script is
        # operator-run and already needs admin permissions for
        # ensure_permanent_password.
        client = self.cognito_client
        if client is None:
            import boto3

            client = boto3.client("cognito-idp")
        # Resolved BEFORE the try: a missing env var is a configuration error,
        # and raising it inside the block below meant the broad
        # `except Exception` rewrote it as "admin_get_user failed ...
        # RuntimeError" — reporting an API failure for a config mistake and
        # discarding the remediation. Caught by
        # test_required_env_raises_with_remediation_when_unset.
        pool_id = _required_env("COGNITO_USER_POOL_ID")
        try:
            resp = client.admin_get_user(
                UserPoolId=pool_id,
                Username=username,
            )
        except Exception as e:
            if type(e).__name__ == "UserNotFoundException":
                return None
            raise RuntimeError(
                f"admin_get_user failed for {username}: {type(e).__name__}"
            ) from None
        sub = next(
            (a["Value"] for a in resp.get("UserAttributes", [])
             if a.get("Name") == "sub"),
            None,
        )
        if sub is None:
            raise RuntimeError(
                f"{username} exists but has no 'sub' attribute — cannot "
                f"correlate it to producer subscription rows."
            )
        return {"username": username, "sub": sub}

    def sign_in(self, username: str, password: str) -> str:
        """Sign in as CMS's subscriber and return the **IdToken**."""
        client = self.cognito_client
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "cognito-idp",
                config=Config(connect_timeout=3, read_timeout=5,
                              retries={"max_attempts": 2}),
            )
        try:
            resp = client.initiate_auth(
                ClientId=self.user_pool_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": username, "PASSWORD": password},
            )
        except Exception as e:
            raise RuntimeError(
                f"initiate_auth failed for {username}: {type(e).__name__}"
            ) from None
        if resp.get("ChallengeName"):
            raise RuntimeError(
                f"initiate_auth returned challenge "
                f"{resp['ChallengeName']!r} — call ensure_permanent_password() "
                f"before sign_in(). The producer creates accounts in "
                f"FORCE_CHANGE_PASSWORD."
            )
        token = (resp.get("AuthenticationResult") or {}).get("IdToken")
        if not token:
            raise RuntimeError(
                "initiate_auth returned no IdToken. The producer's authorizer "
                "validates `aud`, so an AccessToken is not usable here."
            )
        return token

    def list_subscriptions(self, access_token: str) -> list[dict[str, Any]]:
        resp = self._call("GET", "/subscriptions", access_token)
        rows = resp.get("subscriptions", resp if isinstance(resp, list) else None)
        if not isinstance(rows, list):
            raise RuntimeError(
                f"GET /subscriptions did not return a list (got "
                f"{type(rows).__name__}, keys {sorted(resp) if isinstance(resp, dict) else '-'}). "
                f"Shape drift."
            )
        return rows

    def create_subscription(
        self, access_token: str, product_id: str
    ) -> dict[str, Any]:
        resp = self._call(
            "POST", "/subscriptions", access_token, {"product_id": product_id}
        )
        if "subscription_id" not in resp:
            raise RuntimeError(
                f"POST /subscriptions response missing 'subscription_id' "
                f"(got keys {sorted(resp)}). Shape drift."
            )
        return resp



def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = _parse_args()

    if args.dry_run:
        producer_admin: ProducerAdmin = MockProducerAdmin()
        secrets_writer: SecretsWriter = MockSecretsWriter()
        account = args.account or "000000000000"
        promoter: Optional[Callable[[str, str], None]] = None
        logger.info("DRY RUN — using in-process mocks")
    else:
        if not args.account:
            logger.error(
                "AWS_ACCOUNT_ID must be set (or --account passed) for real run."
            )
            return 2
        producer_admin = _build_real_producer_admin()
        secrets_writer = RealSecretsWriter(region=args.region)
        account = args.account
        # Resolved eagerly so a missing COGNITO_USER_POOL_ID fails here, before
        # any producer-side account is created — a half-provisioned account is
        # the orphaned state the run()/run_multi guards have to recover from.
        _pool_id = _required_env("COGNITO_USER_POOL_ID")

        def promoter(username: str, password: str) -> None:
            ensure_permanent_password(username, password, _pool_id)

    prov = Provisioner(
        stage=args.stage,
        region=args.region,
        account=account,
        producer_admin=producer_admin,
        secrets=secrets_writer,
        password_promoter=promoter,
    )

    if args.product_ids:
        # Multi-product path: provision one subscription per requested product.
        results = prov.run_multi(args.product_ids)
        print(json.dumps(results, indent=2))
    else:
        # Default single-product path — byte-identical to the original behavior.
        result = prov.run()
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())

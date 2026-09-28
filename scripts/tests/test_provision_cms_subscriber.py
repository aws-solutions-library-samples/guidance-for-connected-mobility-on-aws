#!/usr/bin/env python3
"""Tests for ``provision-cms-subscriber.py`` — spec T1.1.

Spec: ``.kiro/specs/2026-09-10-cms-connected-services-consumer/`` T1.1 (Group 1).

Verify path per ``tasks.md`` T1.1: "dry-run against a local mock server
returning the producer's documented response shapes ... and confirm the
script's Secrets Manager write logic independent of the live call."

Run from repo root::

    python3 -m pytest scripts/tests/test_provision_cms_subscriber.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

# Load the script as a module. The filename contains hyphens so the normal
# `import provision-cms-subscriber` doesn't work — importlib.util is the
# supported way to load a file with a hyphenated name.
_SCRIPT_PATH = Path(__file__).parent.parent / "provision-cms-subscriber.py"
_spec = importlib.util.spec_from_file_location("provision_cms_subscriber", _SCRIPT_PATH)
assert _spec and _spec.loader
provision = importlib.util.module_from_spec(_spec)
# Python 3.14 @dataclass requires the module to be registered in sys.modules
# before class definitions run when loaded via importlib.util — register
# before exec_module to avoid AttributeError on cls.__module__ lookup.
sys.modules["provision_cms_subscriber"] = provision
_spec.loader.exec_module(provision)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_producer():
    return provision.MockProducerAdmin()


@pytest.fixture
def mock_secrets():
    return provision.MockSecretsWriter()


@pytest.fixture
def provisioner(mock_producer, mock_secrets):
    return provision.Provisioner(
        stage="staging",
        region="us-west-2",
        account="123456789012",
        producer_admin=mock_producer,
        secrets=mock_secrets,
    )


# ── Test: happy path first run ───────────────────────────────────────────

class TestFirstRun:
    def test_creates_subscriber_subscription_and_secret(
        self, provisioner, mock_producer, mock_secrets
    ):
        result = provisioner.run()
        assert result["action"] == "created"
        assert result["subscription_id"] == "mock-sub-id-001"
        assert result["username"] == "cms-staging-subscriber@example.com"
        assert result["secret_name"] == (
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        )

    def test_subscriber_exists_at_producer(self, provisioner, mock_producer):
        provisioner.run()
        assert "cms-staging-subscriber@example.com" in mock_producer.subscribers

    def test_secret_contains_password(self, provisioner, mock_secrets):
        provisioner.run()
        secret = mock_secrets.store[
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        ]
        # The password is in Secrets Manager but MUST NOT appear in the
        # returned summary — verified in a separate test below.
        assert "password" in secret
        assert len(secret["password"]) == 32

    def test_result_summary_never_includes_password(
        self, provisioner
    ):
        result = provisioner.run()
        # An operator running this via `| tee log.txt` should not leak the
        # credential — the password lives only in Secrets Manager.
        assert "password" not in result

    def test_secret_stores_product_id(self, provisioner, mock_secrets):
        provisioner.run()
        secret = mock_secrets.store[
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        ]
        # Asserted against the constant rather than a duplicated literal.
        # FG2.2: this test previously hardcoded "telemetry", so the stale
        # constant had a second home here and looked corroborated. The
        # constant's *value* is pinned by TestProductIdContract, against the
        # producer's catalog — which is the only thing that can actually
        # decide whether the value is right.
        assert secret["product_id"] == provision.TELEMETRY_PRODUCT_ID


# ── Test: idempotency ────────────────────────────────────────────────────

class TestIdempotency:
    """Task T1.1 Accept: "Idempotent — running it twice does not create a
    second subscriber account or a second subscription (check-then-create,
    per the producer's own idempotency conventions)."
    """

    def test_second_run_is_noop_when_both_sides_valid(
        self, provisioner, mock_producer, mock_secrets
    ):
        first = provisioner.run()
        assert first["action"] == "created"

        second = provisioner.run()
        assert second["action"] == "noop"
        assert second["subscription_id"] == first["subscription_id"]

    def test_second_run_does_not_create_second_subscriber(
        self, provisioner, mock_producer
    ):
        provisioner.run()
        provisioner.run()
        assert len(mock_producer.subscribers) == 1

    def test_second_run_does_not_create_second_subscription(
        self, provisioner, mock_producer
    ):
        provisioner.run()
        provisioner.run()
        # Only one subscription across all users.
        total = sum(len(v) for v in mock_producer.subscriptions_by_user.values())
        assert total == 1


# ── Test: partial-state recovery ─────────────────────────────────────────

class TestPartialStateRecovery:
    def test_secret_exists_but_producer_missing_subscription_recreates(
        self, provisioner, mock_producer, mock_secrets
    ):
        # Simulate: first run succeeded, then the subscription was
        # revoked out-of-band at the producer (secret still references it).
        first = provisioner.run()

        # Wipe subscriptions but keep the subscriber account.
        for k in mock_producer.subscriptions_by_user:
            mock_producer.subscriptions_by_user[k] = []

        result = provisioner.run()
        assert result["action"] == "recreated_subscription"
        # The recreation must actually re-populate the producer — proves
        # the script called create_subscription, not just re-read state.
        total = sum(len(v) for v in mock_producer.subscriptions_by_user.values())
        assert total == 1
        # Secret must now reference the recreated subscription (whichever
        # id the producer minted).
        secret = mock_secrets.store[
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        ]
        assert secret["subscription_id"] == result["subscription_id"]
        # Same subscriber, same secret name — nothing else duplicated.
        assert len(mock_producer.subscribers) == 1

    def test_orphaned_producer_account_raises(
        self, provisioner, mock_producer, mock_secrets
    ):
        # Simulate: a previous partial run created the subscriber at the
        # producer but never wrote the secret. The password is unrecoverable,
        # so the script must fail loudly — not silently create a second
        # subscriber under a different username.
        mock_producer.create_subscriber("cms-staging-subscriber@example.com", "lost-password")

        with pytest.raises(RuntimeError, match="orphaned-account state"):
            provisioner.run()


# ── Test: password strength ──────────────────────────────────────────────

class TestPasswordGeneration:
    def test_password_length_32(self, provisioner):
        for _ in range(20):
            p = provisioner._generate_password()
            assert len(p) == 32

    def test_password_meets_cognito_default_policy(self, provisioner):
        # Cognito default: >= 8 chars, upper, lower, digit, symbol.
        for _ in range(20):
            p = provisioner._generate_password()
            assert any(c.isupper() for c in p)
            assert any(c.islower() for c in p)
            assert any(c.isdigit() for c in p)
            assert any(c in "!@#$%^&*()-_=+" for c in p)

    def test_passwords_are_not_deterministic(self, provisioner):
        # Two consecutive calls MUST return different values — otherwise
        # every environment shares a password.
        passwords = {provisioner._generate_password() for _ in range(10)}
        assert len(passwords) == 10


# ── Test: naming discipline ──────────────────────────────────────────────

class TestNaming:
    def test_secret_name_region_suffixed(self, provisioner):
        assert provisioner.secret_name.endswith("-us-west-2-123456789012")

    def test_secret_name_deterministic_function_of_stage_region_account(self):
        # Same stage + region + account => same secret name every time.
        # cross-region-namespace-discipline.md check 1.
        p1 = provision.Provisioner(
            stage="staging", region="us-west-2", account="111",
            producer_admin=provision.MockProducerAdmin(),
            secrets=provision.MockSecretsWriter(),
        )
        p2 = provision.Provisioner(
            stage="staging", region="us-west-2", account="111",
            producer_admin=provision.MockProducerAdmin(),
            secrets=provision.MockSecretsWriter(),
        )
        assert p1.secret_name == p2.secret_name

    def test_secret_name_length_budget(self):
        # Worst case across 34 commercial regions (14-char max) + 12-digit account.
        p = provision.Provisioner(
            stage="production", region="ap-northeast-1", account="123456789012",
            producer_admin=provision.MockProducerAdmin(),
            secrets=provision.MockSecretsWriter(),
        )
        # Secrets Manager name limit is 512 chars; must fit with headroom.
        assert len(p.secret_name) < 128



# ── T3.4 new tests: multi-product --product-id support ───────────────────
# Spec: .kiro/specs/2026-09-11-cms-cs-meridian-ingestion/ T3.4

class TestMultiProductBackwardCompat:
    """T3.4 new test 1 — default invocation (no --product-id) keeps
    single-product behavior byte-identical.

    Asserts that ``run()`` (no product_ids argument) still:
    * creates one subscriber, one subscription, one secret
    * writes to the original subscriber-level secret name
    * stores product_id == TELEMETRY_PRODUCT_ID (unchanged constant)
    """

    def test_default_run_uses_original_secret_name(
        self, provisioner, mock_secrets
    ):
        """The secret written by ``run()`` MUST use the original name."""
        result = provisioner.run()
        original_name = (
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        )
        assert result["secret_name"] == original_name
        assert original_name in mock_secrets.store

    def test_default_run_creates_exactly_one_subscription(
        self, provisioner, mock_producer
    ):
        provisioner.run()
        total = sum(len(v) for v in mock_producer.subscriptions_by_user.values())
        assert total == 1

    def test_default_run_result_shape_unchanged(self, provisioner):
        """run() return keys are unchanged — callers parsing the JSON are not broken."""
        result = provisioner.run()
        assert set(result.keys()) == {"action", "subscription_id", "username", "secret_name"}

    def test_product_secret_name_legacy_maps_to_subscriber_name(self):
        """product_secret_name() with telemetry-hifi-v1 returns the subscriber-level name."""
        name = provision.product_secret_name(
            "staging", "us-west-2", "123456789012", "telemetry-hifi-v1"
        )
        assert name == (
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        )


class TestMultiProductSingleMeridian:
    """T3.4 new test 2 — ``--product-id meridian-telemetry-v1`` writes a
    namespaced secret distinct from the legacy subscriber secret.
    """

    def test_meridian_produces_namespaced_secret(
        self, provisioner, mock_secrets
    ):
        results = provisioner.run_multi(["meridian-telemetry-v1"])
        assert len(results) == 1
        result = results[0]
        assert result["action"] == "created"
        assert result["product_id"] == "meridian-telemetry-v1"
        expected_name = (
            "cms-staging-connected-services-meridian-telemetry-v1"
            "-us-west-2-123456789012"
        )
        assert result["secret_name"] == expected_name
        assert expected_name in mock_secrets.store

    def test_meridian_secret_does_not_use_legacy_name(
        self, provisioner, mock_secrets
    ):
        """Namespaced secret MUST be separate from the subscriber-level secret."""
        provisioner.run_multi(["meridian-telemetry-v1"])
        legacy_name = (
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        )
        assert legacy_name not in mock_secrets.store

    def test_meridian_secret_stores_correct_product_id(
        self, provisioner, mock_secrets
    ):
        provisioner.run_multi(["meridian-telemetry-v1"])
        secret_name = (
            "cms-staging-connected-services-meridian-telemetry-v1"
            "-us-west-2-123456789012"
        )
        secret = mock_secrets.store[secret_name]
        assert secret["product_id"] == "meridian-telemetry-v1"

    def test_meridian_result_never_includes_password(self, provisioner):
        results = provisioner.run_multi(["meridian-telemetry-v1"])
        for r in results:
            assert "password" not in r

    def test_product_secret_name_non_legacy_is_namespaced(self):
        """product_secret_name() for a non-legacy product uses the namespaced pattern."""
        name = provision.product_secret_name(
            "staging", "us-west-2", "123456789012", "meridian-telemetry-v1"
        )
        assert name == (
            "cms-staging-connected-services-meridian-telemetry-v1"
            "-us-west-2-123456789012"
        )


class TestMultiProductBothProducts:
    """T3.4 new test 3 — provisioning both telemetry-hifi-v1 AND
    meridian-telemetry-v1 in a single invocation produces both subscriptions
    and both secrets.
    """

    def test_both_products_create_two_subscriptions(
        self, provisioner, mock_producer
    ):
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        total = sum(len(v) for v in mock_producer.subscriptions_by_user.values())
        assert total == 2

    def test_both_products_write_two_secrets(
        self, provisioner, mock_secrets
    ):
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        legacy_name = (
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        )
        meridian_name = (
            "cms-staging-connected-services-meridian-telemetry-v1"
            "-us-west-2-123456789012"
        )
        assert legacy_name in mock_secrets.store
        assert meridian_name in mock_secrets.store

    def test_both_products_create_exactly_one_subscriber(
        self, provisioner, mock_producer
    ):
        """Subscriber account is created once, not twice."""
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        assert len(mock_producer.subscribers) == 1

    def test_both_products_return_two_results(self, provisioner):
        results = provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        assert len(results) == 2
        product_ids = {r["product_id"] for r in results}
        assert product_ids == {"telemetry-hifi-v1", "meridian-telemetry-v1"}

    def test_both_products_actions_are_created(self, provisioner):
        results = provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        for r in results:
            assert r["action"] == "created"


class TestMultiProductIdempotency:
    """T3.4 new test 4 — running the same multi-product invocation twice does
    not create duplicate subscriptions or duplicate secrets.
    """

    def test_second_run_returns_noop_for_both_products(
        self, provisioner
    ):
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        second = provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        for r in second:
            assert r["action"] == "noop"

    def test_second_run_does_not_create_second_subscriber(
        self, provisioner, mock_producer
    ):
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        assert len(mock_producer.subscribers) == 1

    def test_second_run_does_not_create_duplicate_subscriptions(
        self, provisioner, mock_producer
    ):
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        total = sum(len(v) for v in mock_producer.subscriptions_by_user.values())
        assert total == 2  # one per product, not doubled

    def test_second_run_does_not_overwrite_secrets(
        self, provisioner, mock_secrets
    ):
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        first_ids = {
            k: v["subscription_id"] for k, v in mock_secrets.store.items()
        }
        provisioner.run_multi(["telemetry-hifi-v1", "meridian-telemetry-v1"])
        second_ids = {
            k: v["subscription_id"] for k, v in mock_secrets.store.items()
        }
        assert first_ids == second_ids

    def test_second_run_of_meridian_only_is_noop(
        self, provisioner, mock_producer
    ):
        """Idempotency also holds for a single-product multi-product invocation."""
        provisioner.run_multi(["meridian-telemetry-v1"])
        second = provisioner.run_multi(["meridian-telemetry-v1"])
        assert len(second) == 1
        assert second[0]["action"] == "noop"
        total = sum(len(v) for v in mock_producer.subscriptions_by_user.values())
        assert total == 1



# ── Product-id contract against the producer's real catalog (FG2.2) ──────

class TestProductIdContract:
    """``TELEMETRY_PRODUCT_ID`` must name a product the producer actually
    serves, and must route to the secret name T1.1's Accept criteria mandate.

    FG2.2 (2026-09-12): this constant read ``"telemetry"``, which is the
    ``source`` *field* of the telemetry product, not any product's id. The
    real ids are ``telemetry-hifi-v1`` and ``meridian-telemetry-v1``. Nothing
    in Group 1 could catch it — every test passes a product id in explicitly
    or uses ``MockProducerAdmin``, which accepts any string, so the one value
    that reaches the live producer was the one value no test asserted on.

    Both failures would have surfaced for the first time at T2.2, against
    staging: a `POST /subscriptions` naming a product outside the catalog, and
    a Secrets Manager write to a name nothing reads.
    """

    _CATALOG = (
        Path(__file__).parent.parent.parent
        / "services" / "connectors" / "subscriptions" / "products.json"
    )

    def _catalog_ids(self) -> list[str]:
        import json

        assert self._CATALOG.is_file(), (
            f"Producer catalog not found at {self._CATALOG}. This guard cannot "
            f"verify the product-id contract without it — fix the path rather "
            f"than deleting the test."
        )
        with open(self._CATALOG, encoding="utf-8") as fh:
            catalog = json.load(fh)
        ids = [p["product_id"] for p in catalog["products"]]
        assert ids, (
            "Parsed the producer catalog but found no products — the schema "
            "changed. Failing rather than passing on an empty set."
        )
        return ids

    def test_telemetry_product_id_is_in_the_producer_catalog(self):
        ids = self._catalog_ids()
        assert provision.TELEMETRY_PRODUCT_ID in ids, (
            f"TELEMETRY_PRODUCT_ID={provision.TELEMETRY_PRODUCT_ID!r} is not a "
            f"product the producer serves (catalog: {ids}). POST /subscriptions "
            f"would name a product that does not exist. Note that 'telemetry' "
            f"is the telemetry product's `source` FIELD, not its id — that is "
            f"the exact mistake FG2.2 corrected."
        )

    def test_telemetry_product_id_routes_to_the_documented_secret_name(self):
        """On the multi-product path, the credential's location depends on
        this constant.

        ``_provision_one_product`` derives its secret name from
        ``product_secret_name(product_id)``, which returns the subscriber-level
        name T1.1's Accept criteria mandate **only** for ``_LEGACY_PRODUCT_ID``.
        So a ``TELEMETRY_PRODUCT_ID`` that drifts off that value relocates
        CMS's credential to a name nothing reads, and the symptom at T2.2 is
        "the secret was never written".

        Scope note, because it is easy to overstate this: the single-product
        ``run()`` path is **not** affected — it uses the
        ``Provisioner.secret_name`` property, which ignores ``product_id``
        entirely. This test guards the multi-product path.
        """
        expected = "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        actual = provision.product_secret_name(
            "staging", "us-west-2", "123456789012", provision.TELEMETRY_PRODUCT_ID
        )
        assert actual == expected, (
            f"TELEMETRY_PRODUCT_ID={provision.TELEMETRY_PRODUCT_ID!r} routes "
            f"CMS's credential to {actual!r}, but T1.1's Accept criteria "
            f"mandate {expected!r}. Either the constant drifted from "
            f"_LEGACY_PRODUCT_ID={provision._LEGACY_PRODUCT_ID!r}, or the "
            f"naming convention changed and T1.1's Accept criteria need "
            f"updating alongside it — do not just change this assertion."
        )



# ── RealProducerAdmin + permanent-password step (T2.0c) ──────────────────

class _FakeCognitoAdmin:
    """Records admin calls; programmable per-method outcome."""

    def __init__(self, get_user=None, initiate=None, raise_on_set=None):
        self._get_user = get_user
        self._initiate = initiate
        self._raise_on_set = raise_on_set
        self.set_password_calls = []

    def admin_get_user(self, **kw):
        if isinstance(self._get_user, Exception):
            raise self._get_user
        return self._get_user

    def initiate_auth(self, **kw):
        if isinstance(self._initiate, Exception):
            raise self._initiate
        return self._initiate

    def admin_set_user_password(self, **kw):
        self.set_password_calls.append(kw)
        if self._raise_on_set:
            raise self._raise_on_set


class _UserNotFoundException(Exception):
    pass


def _real_admin(http=None, cognito=None):
    return provision.RealProducerAdmin(
        endpoint="https://producer.example/prod",
        operator_id_token="operator-id-token",
        user_pool_client_id="client-123",
        http=http,
        cognito_client=cognito,
    )


class TestEnsurePermanentPassword:
    """The step that stops the machine account sitting in FORCE_CHANGE_PASSWORD."""

    def test_sets_permanent_true(self):
        c = _FakeCognitoAdmin()
        provision.ensure_permanent_password(
            "cms-staging-subscriber@example.com", "pw", "us-west-2_pool", cognito_client=c
        )
        assert len(c.set_password_calls) == 1
        call = c.set_password_calls[0]
        assert call["Permanent"] is True, (
            "Permanent=False would leave the account in FORCE_CHANGE_PASSWORD, "
            "which is the entire condition this function exists to clear"
        )
        assert call["Username"] == "cms-staging-subscriber@example.com"
        assert call["UserPoolId"] == "us-west-2_pool"

    def test_failure_does_not_echo_the_password(self):
        secret = "SuperSecret-Passw0rd"
        c = _FakeCognitoAdmin(raise_on_set=Exception(f"boom PASSWORD={secret}"))
        with pytest.raises(RuntimeError) as ei:
            provision.ensure_permanent_password(
                "u", secret, "pool", cognito_client=c
            )
        assert secret not in str(ei.value)
        assert ei.value.__cause__ is None


class TestRealProducerAdmin:
    """Shapes are read from the producer's committed handler, not a live call.
    These tests pin the parsing decisions so the first live run reports which
    assumption broke rather than failing somewhere downstream.
    """

    def test_create_subscriber_does_not_send_our_password(self):
        seen = {}

        def http(method, url, headers, body):
            seen.update(method=method, url=url, headers=headers, body=body)
            return 200, {"username": "u", "sub": "s", "temp_password": "producer-chose"}

        out = _real_admin(http=http).create_subscriber("u", "our-proposal")
        assert "our-proposal" not in json.dumps(seen["body"] or {}), (
            "the route generates its own temp password and rejects privileged "
            "body fields — sending ours risks tripping its injection guard"
        )
        assert out["temp_password"] == "producer-chose"
        assert seen["headers"]["Authorization"] == "Bearer operator-id-token"

    def test_create_subscriber_raises_when_temp_password_absent(self):
        """The route returns it exactly once. Losing it means the account is
        unusable, so this must fail loudly at creation, not at sign-in.
        """
        http = lambda *a, **k: (200, {"username": "u", "sub": "s"})
        with pytest.raises(RuntimeError, match="temp_password"):
            _real_admin(http=http).create_subscriber("u", "p")

    def test_403_names_which_credential_is_wrong(self):
        http = lambda *a, **k: (403, {"error": "Forbidden"})
        with pytest.raises(RuntimeError, match="admin group"):
            _real_admin(http=http).create_subscriber("u", "p")

    def test_sign_in_returns_id_token_not_access_token(self):
        c = _FakeCognitoAdmin(initiate={
            "AuthenticationResult": {"IdToken": "the-id", "AccessToken": "the-access"}
        })
        assert _real_admin(cognito=c).sign_in("u", "p") == "the-id"

    def test_sign_in_on_challenge_points_at_the_permanent_password_step(self):
        c = _FakeCognitoAdmin(initiate={"ChallengeName": "NEW_PASSWORD_REQUIRED"})
        with pytest.raises(RuntimeError, match="ensure_permanent_password"):
            _real_admin(cognito=c).sign_in("u", "p")

    def test_find_subscriber_returns_none_on_user_not_found(self, monkeypatch):
        c = _FakeCognitoAdmin(get_user=_UserNotFoundException("nope"))
        c.admin_get_user = lambda **kw: (_ for _ in ()).throw(
            type("UserNotFoundException", (Exception,), {})()
        )
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-west-2_pool")
        assert _real_admin(cognito=c).find_subscriber("u") is None

    def test_find_subscriber_extracts_sub_and_exercises_required_env(self, monkeypatch):
        """Also the regression test for `_required_env`, which was referenced
        by two call sites before it was defined — a NameError reserved for the
        first live run.
        """
        c = _FakeCognitoAdmin(get_user={
            "UserAttributes": [{"Name": "sub", "Value": "abc-123"},
                               {"Name": "email", "Value": "x@y.z"}]
        })
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-west-2_pool")
        assert _real_admin(cognito=c).find_subscriber("u") == {
            "username": "u", "sub": "abc-123"
        }

    def test_required_env_raises_with_remediation_when_unset(self, monkeypatch):
        # monkeypatch.delenv, not os.environ.pop: the bare pop leaked across
        # tests and left this case order-dependent — if the ambient shell
        # exported COGNITO_USER_POOL_ID the raise path became unreachable and
        # this test passed vacuously. Review W1 of T2.0c.
        monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
        c = _FakeCognitoAdmin(get_user={"UserAttributes": []})
        with pytest.raises(RuntimeError, match="config/<stage>.env"):
            _real_admin(cognito=c).find_subscriber("u")

    def test_build_real_producer_admin_requires_endpoint_and_operator_token(
        self, monkeypatch
    ):
        for var in ("CS_PRODUCER_ENDPOINT", "CS_OPERATOR_ID_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError, match="CS_PRODUCER_ENDPOINT"):
            provision._build_real_producer_admin()
        monkeypatch.setenv("CS_PRODUCER_ENDPOINT", "https://producer.example/prod/")
        with pytest.raises(RuntimeError, match="CS_OPERATOR_ID_TOKEN"):
            provision._build_real_producer_admin()
        # Full construction path — exercises `_required_env("COGNITO_CLIENT_ID")`.
        monkeypatch.setenv("CS_OPERATOR_ID_TOKEN", "tok")
        monkeypatch.setenv("COGNITO_CLIENT_ID", "client-123")
        admin = provision._build_real_producer_admin()
        assert isinstance(admin, provision.RealProducerAdmin)
        assert admin.endpoint == "https://producer.example/prod", "trailing / stripped"



class TestPasswordPromotion:
    """FG3.2 — the promotion is wired, and its *ordering* is the contract.

    Review W2 of T2.0c: `ensure_permanent_password` existed with zero call
    sites, so deleting its body survived all 48 tests. It is now injected into
    `Provisioner` and invoked on both creation paths. These tests pin the two
    things that make it correct: it runs with the password the *producer*
    chose, and it runs BEFORE sign_in.
    """

    def _recording_provisioner(self, calls, mock_producer, mock_secrets):
        def promoter(username, password):
            calls.append(("promote", username, password))

        real_sign_in = mock_producer.sign_in

        def traced_sign_in(username, password):
            calls.append(("sign_in", username, password))
            return real_sign_in(username, password)

        mock_producer.sign_in = traced_sign_in
        return provision.Provisioner(
            stage="staging", region="us-west-2", account="123456789012",
            producer_admin=mock_producer, secrets=mock_secrets,
            password_promoter=promoter,
        )

    def test_promotes_before_sign_in(self, mock_producer, mock_secrets):
        calls = []
        self._recording_provisioner(calls, mock_producer, mock_secrets).run()
        kinds = [c[0] for c in calls]
        assert "promote" in kinds, (
            "the promotion never ran — an unpromoted account fails later, in "
            "the proxy Lambda, as an unanswerable Cognito challenge"
        )
        assert kinds.index("promote") < kinds.index("sign_in"), (
            f"promotion must precede sign_in (got {kinds}): the account is in "
            f"FORCE_CHANGE_PASSWORD until it runs, and sign_in against that "
            f"state returns a challenge with no tokens"
        )

    def test_promotes_with_the_producer_chosen_password(
        self, mock_producer, mock_secrets
    ):
        calls = []
        prov = self._recording_provisioner(calls, mock_producer, mock_secrets)
        prov.run()
        promoted = next(c[2] for c in calls if c[0] == "promote")
        signed_in_with = next(c[2] for c in calls if c[0] == "sign_in")
        assert promoted == signed_in_with, (
            "promoting one password and signing in with another leaves the "
            "account unusable in the most confusing possible way"
        )
        stored = mock_secrets.store[
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        ]
        assert stored["password"] == promoted, (
            "the persisted secret must be the promoted password, or the proxy "
            "Lambda reads a value the account does not have"
        )

    def test_multi_product_path_also_promotes(self, mock_producer, mock_secrets):
        calls = []
        prov = self._recording_provisioner(calls, mock_producer, mock_secrets)
        prov.run_multi([provision.TELEMETRY_PRODUCT_ID])
        assert any(c[0] == "promote" for c in calls), (
            "run_multi creates the account on its own code path — the "
            "promotion has to be wired there too, not only in run()"
        )

    def test_dry_run_without_a_promoter_is_a_noop_not_a_crash(
        self, provisioner
    ):
        """The default `password_promoter=None` must not break dry-run, which
        is the only mode Group 1 could ever execute.
        """
        assert provisioner.password_promoter is None
        assert provisioner.run()["action"] == "created"

    def test_promotion_failure_aborts_rather_than_continuing(
        self, mock_producer, mock_secrets
    ):
        """A swallowed promotion failure is the worst outcome: provisioning
        reports success and the account is unusable.
        """
        def failing(username, password):
            raise RuntimeError("admin_set_user_password failed: AccessDenied")

        prov = provision.Provisioner(
            stage="staging", region="us-west-2", account="123456789012",
            producer_admin=mock_producer, secrets=mock_secrets,
            password_promoter=failing,
        )
        with pytest.raises(RuntimeError, match="admin_set_user_password"):
            prov.run()



class TestPromotionUsesProducerPasswordNotOurs:
    """review-t20c-cycle2 Suggestion: `test_promotes_with_the_producer_chosen_password`
    could not actually distinguish the two, because `MockProducerAdmin` echoes
    the proposal — so the two values are identical there and a mutation moving
    the promotion *above* the `temp_password` reassignment passed all five
    tests. This double returns a deliberately DIFFERENT password, which is the
    only way to tell the values apart.
    """

    class _DivergentProducer(provision.MockProducerAdmin):
        PRODUCER_CHOSE = "producer-chose-something-else-9Z"

        def create_subscriber(self, username, password):
            out = super().create_subscriber(username, password)
            out["temp_password"] = self.PRODUCER_CHOSE  # ignore the proposal
            return out

        def sign_in(self, username, password):
            # The real route only accepts the password it chose.
            if password != self.PRODUCER_CHOSE:
                raise RuntimeError(
                    f"NotAuthorizedException: signed in with {password!r}, "
                    f"account has the producer-chosen password"
                )
            return super().sign_in(username, password)

    def test_promotion_and_signin_both_use_the_producer_password(self):
        calls = []
        producer = self._DivergentProducer()
        secrets = provision.MockSecretsWriter()
        prov = provision.Provisioner(
            stage="staging", region="us-west-2", account="123456789012",
            producer_admin=producer, secrets=secrets,
            password_promoter=lambda u, p: calls.append(p),
        )
        prov.run()
        assert calls == [self._DivergentProducer.PRODUCER_CHOSE], (
            f"promotion used {calls!r}, not the producer-chosen password. "
            f"Promoting our proposal sets the account's password to a value "
            f"the producer never issued — which happens to 'work' but silently "
            f"diverges from what the producer's records reflect."
        )
        stored = secrets.store[
            "cms-staging-connected-services-subscriber-us-west-2-123456789012"
        ]
        assert stored["password"] == self._DivergentProducer.PRODUCER_CHOSE



class TestRealProducerAdminRedirectRefusal:
    """FG3.4 — security review W1 of T2.0c.

    `urllib.request.urlopen` at defaults follows 3xx and preserves the
    `Authorization` header across a host change (CPython's
    `HTTPRedirectHandler.redirect_request` copies all headers except
    content-length/content-type; `requests` strips Authorization on host change,
    urllib does not). The token on /admin/* calls is an operator IdToken with
    admin scope on the producer's pool, so following a redirect hands it over.
    """

    def _admin(self):
        return provision.RealProducerAdmin(
            endpoint="https://producer.example/prod",
            operator_id_token="operator-id-token",
            user_pool_client_id="client-123",
        )

    def test_opener_installs_a_redirect_refusing_handler(self):
        """Structural: the real transport must build its own opener with a
        redirect-refusing handler rather than calling `urlopen` directly.
        """
        import inspect

        src = inspect.getsource(provision.RealProducerAdmin._call)
        assert "build_opener" in src, (
            "the real transport must use its own OpenerDirector — plain "
            "urlopen() follows redirects with the Authorization header attached"
        )
        assert "urllib.request.urlopen(" not in src, (
            "found a direct urlopen() call, which bypasses the redirect guard"
        )
        assert "HTTPRedirectHandler" in src

    def test_redirect_handler_raises_on_every_3xx_code(self):
        """All five redirect codes must be refused, not just 302 — a producer
        misconfiguration is as likely to emit 301 or 307.
        """
        import inspect
        import urllib.error
        import urllib.request

        # Recover the locally-defined handler class from the method's source by
        # executing just its class body in a namespace with urllib available.
        src = inspect.getsource(provision.RealProducerAdmin._call)
        start = src.index("class _RefuseRedirects")
        end = src.index("opener = urllib.request.build_opener")
        body = "\n".join(
            line[12:] if line.startswith(" " * 12) else line.lstrip()
            for line in src[start:end].rstrip().splitlines()
        )
        ns: dict = {"urllib": urllib}
        exec(body, ns)
        handler = ns["_RefuseRedirects"]()

        for attr in ("http_error_301", "http_error_302", "http_error_303",
                     "http_error_307", "http_error_308"):
            assert hasattr(handler, attr), f"{attr} not refused"
        # Compare the underlying functions, not the bound methods: attribute
        # access creates a fresh bound-method object each time, so `is` on
        # `handler.http_error_301 is handler.http_error_302` is always False
        # regardless of whether they share an implementation.
        cls = type(handler)
        assert (
            cls.http_error_301
            is cls.http_error_302
            is cls.http_error_303
            is cls.http_error_307
            is cls.http_error_308
        ), "all five redirect codes must route to the same refusal"

    def test_operator_token_absent_from_repr(self):
        assert "operator-id-token" not in repr(self._admin()), (
            "the operator IdToken carries admin scope — it must not sit in a "
            "dataclass repr that any log line could interpolate"
        )



    def test_refused_redirect_reports_itself_rather_than_a_shape_error(self):
        """Security-review cycle 2 non-finding, taken anyway.

        A refused 3xx arrives as a *status*, not an exception, because the
        handler's HTTPError is caught by the `except HTTPError` branch. 302 fell
        between `== 403` and `>= 400`, so it was returned as success and the
        caller then complained "response missing 'username'" — reporting a shape
        problem for a firing security control.
        """
        http = lambda *a, **k: (302, {})
        with pytest.raises(RuntimeError, match="redirect was refused"):
            provision.RealProducerAdmin(
                endpoint="https://producer.example/prod",
                operator_id_token="tok",
                user_pool_client_id="c",
                http=http,
            ).create_subscriber("u", "p")

    def test_refused_redirect_error_does_not_leak_the_token(self):
        http = lambda *a, **k: (302, {})
        with pytest.raises(RuntimeError) as ei:
            provision.RealProducerAdmin(
                endpoint="https://producer.example/prod",
                operator_id_token="OPERATOR-SECRET",
                user_pool_client_id="c",
                http=http,
            ).create_subscriber("u", "p")
        assert "OPERATOR-SECRET" not in str(ei.value)



class TestUsernameMustBeAnEmail:
    """T2.2 live finding: the CMS pool has `UsernameAttributes: ["email"]`, and
    the producer's admin route derives the username from the body's `email`
    (handler.py:452). A non-email username is therefore uncreatable — but
    `MockProducerAdmin` accepts any string, so all 59 tests passed against an
    identity Cognito would have rejected. This pins the shape.
    """

    _EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def test_template_produces_an_email(self):
        for stage in ("dev", "staging", "prod"):
            u = provision.CMS_SUBSCRIBER_USERNAME_TEMPLATE.format(stage=stage)
            assert self._EMAIL.match(u), (
                f"username {u!r} for stage {stage} is not an email. The pool's "
                f"UsernameAttributes=['email'] makes a non-email username "
                f"uncreatable, and the producer's route derives the username "
                f"from the email field regardless."
            )

    def test_provisioner_username_property_is_an_email(self, provisioner):
        assert self._EMAIL.match(provisioner.username)

    def test_create_subscriber_sends_email_not_subscriber_username(self):
        seen = {}

        def http(method, url, headers, body):
            seen.update(body=body or {})
            return 200, {"username": body["email"], "sub": "s",
                         "temp_password": "p"}

        provision.RealProducerAdmin(
            endpoint="https://p.example/prod", operator_id_token="t",
            user_pool_client_id="c", http=http,
        ).create_subscriber("cms-staging-subscriber@example.com", "pw")

        assert "email" in seen["body"], (
            "the route requires `email` — omitting it returned "
            "`400 email is required` on the first live call"
        )
        assert "subscriber_username" not in seen["body"], (
            "the route ignores `subscriber_username`; sending it implied a "
            "contract that does not exist"
        )

    def test_create_subscriber_rejects_a_username_the_producer_changed(self):
        """The route derives the username itself, so a mismatch means our
        identity assumption is wrong and every later lookup would miss.
        """
        http = lambda m, u, h, b: (
            200, {"username": "something-else@example.com", "sub": "s",
                  "temp_password": "p"}
        )
        with pytest.raises(RuntimeError, match="but it created"):
            provision.RealProducerAdmin(
                endpoint="https://p.example/prod", operator_id_token="t",
                user_pool_client_id="c", http=http,
            ).create_subscriber("cms-staging-subscriber@example.com", "pw")

    def test_divergence_check_is_case_insensitive(self):
        """Cognito normalises email usernames to lowercase, so the producer can
        legitimately return a different case than we sent. Comparing
        case-sensitively would reject a correct response.

        review-t22-cycle1 Suggestion: every other test passed both sides in the
        same case, so removing `.lower()` from the check survived them all.
        """
        http = lambda m, u, h, b: (
            200, {"username": "CMS-Staging-Subscriber@Example.COM", "sub": "s",
                  "temp_password": "p"}
        )
        out = provision.RealProducerAdmin(
            endpoint="https://p.example/prod", operator_id_token="t",
            user_pool_client_id="c", http=http,
        ).create_subscriber("cms-staging-subscriber@example.com", "pw")
        assert out["temp_password"] == "p", (
            "a case difference must not be treated as the producer creating a "
            "different identity"
        )

    def test_sign_in_failure_does_not_echo_the_password(self):
        """Symmetric to TestEnsurePermanentPassword's guard.

        security-review-t22-cycle1 Suggestion (i): `ensure_permanent_password`
        had this test and `sign_in` did not, even though both handle the
        password. An asymmetric discipline is one a refactor drops silently.
        """
        secret = "SuperSecret-SignIn-Passw0rd"
        c = _FakeCognitoAdmin(initiate=Exception(f"boom PASSWORD={secret}"))
        with pytest.raises(RuntimeError) as ei:
            _real_admin(cognito=c).sign_in("u", secret)
        assert secret not in str(ei.value)
        assert ei.value.__cause__ is None, "raise ... from None is load-bearing"

"""Tests for the PRE_SIGN_UP disposable-mailbox denylist trigger.

Two failure directions are asserted, because getting either wrong is a real incident:

  * a denied domain that gets through — the `inbox.testmail.app` accounts this control
    exists to stop;
  * a pass-through flow that gets blocked. A pool has ONE pre-sign-up slot, so first
    federated sign-in (`PreSignUp_ExternalProvider`) and operator account creation
    (`PreSignUp_AdminCreateUser`) hit this same function. Policing them would lock out
    Amazon-employee federation, and no amount of testing the denial path would reveal it.

The bypass cases (5-7 below) are the ones with teeth: they fail if someone "simplifies"
the handler by dropping the trigger-source check.

Run with:
  cd deployment/lambdas/cognito_triggers/pre_sign_up && python3 -m pytest test_handler.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Import the module under test by PATH, under a unique module name.
#
# Not `import handler`: the sibling `provisioning/` package also contains a
# `handler.py`, so registering the generic name `handler` in sys.modules makes
# whichever test file is collected first win for both — which produced 17 spurious
# failures in the provisioning suite the moment this file was added. Loading by path
# under a unique name keeps `pytest lambdas/` honest, and a suite that only passes
# when run one directory at a time is a suite that gets skipped.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "pre_sign_up_handler", os.path.join(_HERE, "handler.py")
)
assert _spec and _spec.loader
h = importlib.util.module_from_spec(_spec)
sys.modules["pre_sign_up_handler"] = h
_spec.loader.exec_module(h)

_DENIED = "inbox.testmail.app"
_ALLOWED = "operator@example.com"


def _event(trigger_source: str, email: str = "", username: str = "") -> dict:
    """A pre-sign-up event. `userName` defaults to the email, as an alias pool sends.

    `userPoolId` is an angle-bracket placeholder, not a realistic-looking region-prefixed
    pool id: this file ships in the public template and a realistic shape matches the
    publish scanner's `cognito_user_pool_id` pattern as a critical finding. Writing the
    unsafe shape here to explain the rule would trip it too, so it is described rather
    than shown. The handler never reads this field.
    """
    return {
        "triggerSource": trigger_source,
        "userPoolId": "<user-pool-id>",
        "userName": username or email,
        "request": {"userAttributes": {"email": email} if email else {}},
        "response": {},
    }


# ---------------------------------------------------------------------------
# The denylist asset itself
# ---------------------------------------------------------------------------

class TestDenylistAsset:
    def test_ships_beside_the_handler(self) -> None:
        """Bundled, not fetched: this trigger is on the critical path of every signup."""
        path = os.path.join(os.path.dirname(os.path.abspath(h.__file__)),
                            "disposable_domains.json")
        assert os.path.isfile(path)

    def test_contains_the_concrete_failure_case(self) -> None:
        assert _DENIED in h._DENYLIST

    def test_has_a_meaningful_number_of_entries(self) -> None:
        assert len(h._DENYLIST) >= 50

    def test_entries_are_normalised(self) -> None:
        """A stray capital or trailing dot in the asset is a silent hole."""
        for domain in h._DENYLIST:
            assert domain == domain.strip().rstrip(".").lower(), domain
            assert "@" not in domain and "." in domain, domain

    def test_missing_asset_fails_open_not_closed(self, monkeypatch) -> None:
        """A packaging mistake must not stop every signup AND every federation.

        Failing closed here would convert a bundling error into a total sign-up and
        federation outage; failing open loses one defence-in-depth layer that email
        verification, the scoped `fleet-guest` group and the pool WAF all sit behind.
        The condition is logged at ERROR rather than passing silently.
        """
        monkeypatch.setattr(h, "_DENYLIST_FILENAME", "no_such_file.json")
        assert h._load_denylist() == frozenset()


# ---------------------------------------------------------------------------
# Self-registration — the flow this control polices
# ---------------------------------------------------------------------------

class TestSelfSignupDenial:
    def test_case_1_denied_domain_raises(self) -> None:
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{_DENIED}"), None)

    def test_case_2_normal_email_passes_through_unchanged(self) -> None:
        event = _event(h._SELF_SIGNUP_TRIGGER, _ALLOWED)
        assert h.handler(event, None) is event

    def test_case_3_denied_domain_is_case_folded(self) -> None:
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{_DENIED.upper()}"), None)

    def test_case_4_email_without_at_sign_passes_through(self) -> None:
        event = _event(h._SELF_SIGNUP_TRIGGER, "not-an-email")
        assert h.handler(event, None) is event

    def test_case_5_denial_message_does_not_name_the_domain(self) -> None:
        """Otherwise the denylist is enumerable one registration at a time."""
        with pytest.raises(Exception) as exc:
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{_DENIED}"), None)
        message = str(exc.value)
        assert _DENIED not in message
        assert "testmail" not in message.lower()
        assert message == h._DENIAL_MESSAGE

    def test_missing_email_attribute_entirely_passes_through(self) -> None:
        event = _event(h._SELF_SIGNUP_TRIGGER)
        assert h.handler(event, None) is event

    def test_does_not_auto_confirm_or_auto_verify(self) -> None:
        """Auto-confirmation would remove the proof-of-mailbox control Phase B needs."""
        event = _event(h._SELF_SIGNUP_TRIGGER, _ALLOWED)
        result = h.handler(event, None)
        assert result["response"].get("autoConfirmUser") is not True
        assert result["response"].get("autoVerifyEmail") is not True


# ---------------------------------------------------------------------------
# Pass-through flows — the lockout direction
# ---------------------------------------------------------------------------

class TestOtherTriggerSourcesBypass:
    @pytest.mark.parametrize(
        "trigger_source",
        ["PreSignUp_ExternalProvider", "PreSignUp_AdminCreateUser"],
    )
    def test_cases_6_and_7_denied_domain_is_ignored_off_the_signup_path(
        self, trigger_source: str
    ) -> None:
        """The regression this exists to prevent.

        These flows share the single pre-sign-up slot. Applying the denylist to them
        blocks Amazon-employee federation and operator account creation.
        """
        event = _event(trigger_source, f"probe@{_DENIED}")
        assert h.handler(event, None) is event

    def test_unknown_trigger_source_also_bypasses(self) -> None:
        """A future Cognito trigger source must not be policed by accident."""
        event = _event("PreSignUp_SomethingNew", f"probe@{_DENIED}")
        assert h.handler(event, None) is event

    def test_absent_trigger_source_bypasses(self) -> None:
        event = {"userName": f"probe@{_DENIED}", "request": {}, "response": {}}
        assert h.handler(event, None) is event


# ---------------------------------------------------------------------------
# Matching semantics — the one-character bypasses
# ---------------------------------------------------------------------------

class TestMatchingSemantics:
    def test_trailing_dot_does_not_bypass(self) -> None:
        """`mailinator.com.` is the same host as `mailinator.com`."""
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{_DENIED}."), None)

    def test_surrounding_whitespace_does_not_bypass(self) -> None:
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@ {_DENIED} "), None)

    def test_subdomain_of_a_denied_domain_is_denied(self) -> None:
        """mailinator and others deliver arbitrary subdomains to the same inbox."""
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@throwaway.{_DENIED}"), None)

    def test_lookalike_suffix_is_not_denied(self) -> None:
        """The false-positive direction: suffix matching must require a dot boundary.

        `notinbox.testmail.app` is a subdomain and stays denied, but a domain that
        merely *ends with the same characters* — `xinbox.testmail.appliance.example` —
        must not be caught by a substring test.
        """
        event = _event(h._SELF_SIGNUP_TRIGGER, "user@xinbox.testmail.appliance.example")
        assert h.handler(event, None) is event

    def test_denied_domain_supplied_only_via_username(self) -> None:
        """An alias pool puts the address in userName; checking one field leaves a hole."""
        event = _event(h._SELF_SIGNUP_TRIGGER, username=f"probe@{_DENIED}")
        with pytest.raises(Exception):
            h.handler(event, None)

    def test_denied_domain_in_username_while_email_is_clean(self) -> None:
        event = _event(h._SELF_SIGNUP_TRIGGER, _ALLOWED, username=f"probe@{_DENIED}")
        with pytest.raises(Exception):
            h.handler(event, None)


# ---------------------------------------------------------------------------
# Observability — Group 9's alarm is a metric filter on these lines
# ---------------------------------------------------------------------------

class TestLogging:
    def test_denial_logs_outcome_denied_with_the_domain(self, caplog) -> None:
        caplog.set_level("INFO")
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{_DENIED}"), None)
        lines = [json.loads(r.message) for r in caplog.records]
        denied = [l for l in lines if l.get("outcome") == "denied"]
        assert denied, f"no outcome=denied line: {lines}"
        assert denied[0]["domain"] == _DENIED

    def test_allow_path_does_not_log_the_address(self, caplog) -> None:
        """The local part is PII; a domain on the deny path is not an identity."""
        caplog.set_level("INFO")
        h.handler(_event(h._SELF_SIGNUP_TRIGGER, _ALLOWED), None)
        blob = " ".join(r.message for r in caplog.records)
        assert "operator@" not in blob
        assert "example.com" not in blob

    def test_denial_emits_the_metric_filter_token(self, caplog) -> None:
        """The token ui_stack's PreSignUpDenials metric filter matches on.

        Renaming it would leave the denial alarm permanently quiet with no error
        anywhere — the alarm would exist, report OK, and mean nothing. That is the
        "looks deployed, protects nothing" shape, so the coupling is pinned here rather
        than left to a comment.
        """
        caplog.set_level("INFO")
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{_DENIED}"), None)
        assert h._DENIAL_METRIC_TOKEN in " ".join(r.message for r in caplog.records)

    def test_metric_token_shape_survives_log_prefixing(self) -> None:
        """A bare word, so the filter is immune to Lambda's log prefix and json spacing.

        Metric filters read raw log text. The Python runtime's default format prefixes
        logger output, so a `{ $.outcome = "denied" }` JSON filter cannot be relied on;
        a token containing a space or quote would also break the `all_terms` pattern.
        """
        token = h._DENIAL_METRIC_TOKEN
        assert token and token.isascii()
        assert token == token.strip()
        assert not any(c in token for c in ' \t"\'{}[],:')

    def test_allow_path_does_not_emit_the_denial_token(self, caplog) -> None:
        """Otherwise the denial metric counts successful registrations."""
        caplog.set_level("INFO")
        h.handler(_event(h._SELF_SIGNUP_TRIGGER, _ALLOWED), None)
        assert h._DENIAL_METRIC_TOKEN not in " ".join(r.message for r in caplog.records)

    def test_bypass_path_does_not_emit_the_denial_token(self, caplog) -> None:
        """A federated first sign-in must not register as a signup denial."""
        caplog.set_level("INFO")
        h.handler(_event("PreSignUp_ExternalProvider", f"probe@{_DENIED}"), None)
        assert h._DENIAL_METRIC_TOKEN not in " ".join(r.message for r in caplog.records)


class TestEncodingBypasses:
    """Security review Cycle 1 Suggestion 3 (2026-08-11) — encoding-level bypasses.

    Scope claim, stated so a future reader does not over-trust this: these cover the
    ENCODING class (the same registrable domain written differently). They do NOT cover the
    ENUMERATION class — a lookalike domain the attacker controls, such as a different TLD,
    is simply not on the denylist and no normalisation can find it. The denylist is
    defence-in-depth behind email verification, fleet-guest scoping and the pool WAF.
    """

    def test_fullwidth_form_does_not_bypass(self) -> None:
        """A real bypass before the change: `.lower()` alone left full-width unfolded.

        Note which mechanism does this: IDNA's nameprep, not the explicit NFKC call —
        verified by removing NFKC and watching this test still pass. NFKC is load-bearing
        only on the IDNA-failure path, covered separately below.
        """
        fullwidth = "\uff4d\uff41\uff49\uff4c\uff49\uff4e\uff41\uff54\uff4f\uff52.com"
        assert h._normalise_domain(fullwidth) == "mailinator.com"
        with pytest.raises(Exception):
            h.handler(_event(h._SELF_SIGNUP_TRIGGER, f"probe@{fullwidth}"), None)

    def test_cyrillic_homoglyph_is_a_DIFFERENT_domain_and_is_allowed(self) -> None:
        """Asserts the correct behaviour, which is NOT "fold it onto the ASCII form".

        Cyrillic-o mailinator is a different registrable domain: it cannot receive mail
        sent to the ASCII one, and it is not on the denylist. Denying it would mean denying
        a domain nobody listed. IDNA encoding makes the difference explicit rather than
        invisible, which is the property wanted at a security boundary — the domain is
        allowed through to email verification, which is what actually proves control.
        """
        homoglyph = "mailinat\u043er.com"
        out = h._normalise_domain(homoglyph)
        assert out.startswith("xn--"), f"expected punycode, got {out!r}"
        assert out != "mailinator.com"

    def test_normalisation_never_raises_on_hostile_input(self) -> None:
        """A normaliser that throws is a denial-of-service on the signup path."""
        for hostile in ("", ".", "..", "a" * 300 + ".com", "\x00.com", "-.-", "xn--"):
            h._normalise_domain(hostile)  # must not raise

    def test_nfkc_matters_when_idna_encoding_fails(self) -> None:
        """The case that makes the explicit NFKC call load-bearing.

        This test exists because removing `unicodedata.normalize("NFKC", ...)` left all 31
        other tests green — IDNA's nameprep covers the success path, so nothing failed and
        the call looked redundant. It is not: when IDNA REJECTS the input (here, an empty
        label from a doubled dot) the `except` branch returns the pre-encode string, and
        without NFKC that string is still full-width and would bypass a listed domain.

        A guard whose removal breaks no test is indistinguishable from dead code. This is
        the test that tells the difference.
        """
        # Full-width "mailinator" + an empty label, which idna rejects.
        raw = "\uff4d\uff41\uff49\uff4c\uff49\uff4e\uff41\uff54\uff4f\uff52..com"
        with pytest.raises(Exception):
            raw.encode("idna")  # precondition: this input really does take the fallback
        out = h._normalise_domain(raw)
        assert "mailinator" in out, (
            f"fallback path returned unfolded text ({out!r}); the NFKC normalisation "
            "before the idna encode has been removed or bypassed"
        )

import unicodedata
"""
Cognito PRE_SIGN_UP trigger — disposable-mailbox denylist for external self-signup.

This is the control that would have stopped the three `inbox.testmail.app` accounts
found holding privilege in the prod pool on 2026-08-07. Two of them held explicit
`platform-admin`, and with pool MFA off plus `verified_email` recovery, control of a
throwaway mailbox converted to a password reset into an administrative account on a
publicly reachable surface. See
`issues/2026-08-05-main-api-fail-open-authz-defaults/prod-account-deletion-2026-08-07.md`.

THE ONE THING THAT MUST NOT BE GOT WRONG
----------------------------------------
A pool has exactly ONE pre-sign-up slot, and three different flows route to it:

    PreSignUp_SignUp             self-registration          ← the only flow we police
    PreSignUp_ExternalProvider   first federated sign-in     ← must pass through
    PreSignUp_AdminCreateUser    operator account creation   ← must pass through

Applying the denylist unconditionally would block Amazon-employee federation and
operator account creation — a self-inflicted lockout on paths Phase B has no business
policing, and one that no test of the denial path would notice. The trigger source is
therefore checked FIRST, before any email is even parsed. See `decisions.md` 2026-08-10
and `docs/tech.md` claim (d).

Matching rules
--------------
* Domains are matched case-insensitively, after stripping whitespace and any trailing
  dot (`Mailinator.COM.` is the same host as `mailinator.com`).
* A denied domain also denies its subdomains. Several providers in the list — mailinator
  among them — accept arbitrary subdomains, so exact-only matching would be trivially
  bypassed by `anything.mailinator.com`.
* Both `request.userAttributes.email` and `userName` are inspected. For an
  email-alias pool the username usually *is* the email, and checking only the attribute
  would leave a bypass for a request that supplies the address in the other field.

Denial contract
---------------
Raising from a pre-sign-up trigger makes Cognito return `UserLambdaValidationException`
with the exception's message, which is shown to the caller. The message is therefore
deliberately generic: it names no domain and does not distinguish "this domain is
denied" from any other validation failure, so the denylist cannot be enumerated one
registration at a time. The domain IS recorded in CloudWatch, which is operator-side.

Auto-confirmation is NOT set. A pre-sign-up trigger *can* set `autoConfirmUser` /
`autoVerifyEmail` to skip the verification code; doing so would remove the one control
that proves the registrant controls the mailbox, which is a Phase B requirement.

Python 3.13 runtime. stdlib only — no boto3 calls, so the execution role needs nothing
beyond basic Lambda logging. This handler mutates nothing; it either passes the event
through or refuses.
"""

import json
import logging
import os
import time
from typing import Any

# Only self-registration is policed. See module docstring.
_SELF_SIGNUP_TRIGGER = "PreSignUp_SignUp"

# Shown to the caller verbatim by Cognito. Names no domain, on purpose.
_DENIAL_MESSAGE = "Registration is not allowed from this email domain"

_DENYLIST_FILENAME = "disposable_domains.json"

# Stable single-word token emitted on every denial. A CloudWatch metric filter matches on
# it (see ui_stack's PreSignUpDenials filter), which is why it is a bare word rather than a
# JSON path: metric filters inspect the RAW log event text, and the Lambda Python runtime's
# default text format prefixes logger output with `[LEVEL]\t<time>\t<request-id>\t`, so a
# `{ $.outcome = "denied" }` JSON filter is not safe to assume. A token with no spaces,
# quotes or punctuation survives any prefixing and any change to json.dumps separators.
# If this value changes, the metric filter stops matching and the denial alarm goes quiet
# with no error anywhere — hence the test that pins both the value and its shape.
_DENIAL_METRIC_TOKEN = "presignup_denied"

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)


def _load_denylist() -> frozenset[str]:
    """Load the denylist from the Lambda bundle at cold start.

    Bundled as a file rather than fetched from S3 or a network list: a pre-sign-up
    trigger is on the critical path of every registration and every first federated
    sign-in, so it must not depend on another service being reachable. Refreshing the
    list is a deploy.

    A missing or unreadable list yields an EMPTY denylist, which fails OPEN by
    construction. That is deliberate and is the lesser evil here: failing closed would
    turn a packaging mistake into "nobody can sign up and no Amazon employee can
    federate", while failing open loses one defence-in-depth layer that email
    verification, the scoped `fleet-guest` group and the pool WAF all sit behind. The
    condition is logged at ERROR so it is visible rather than silent.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _DENYLIST_FILENAME)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        _logger.error(
            json.dumps(
                {
                    "outcome": "denylist_unavailable",
                    "detail": f"could not load {_DENYLIST_FILENAME}; denylist is empty",
                }
            )
        )
        return frozenset()
    if not isinstance(raw, list):
        _logger.error(
            json.dumps(
                {
                    "outcome": "denylist_unavailable",
                    "detail": f"{_DENYLIST_FILENAME} is not a JSON array; denylist is empty",
                }
            )
        )
        return frozenset()
    return frozenset(_normalise_domain(str(d)) for d in raw if str(d).strip())


def _normalise_domain(domain: str) -> str:
    """Fold a domain to a canonical ASCII form before denylist comparison.

    `Mailinator.COM.` and ` mailinator.com ` are the same host as `mailinator.com`;
    without normalisation each is a one-character bypass.

    Unicode handling closes security review Cycle 1 Suggestion 3 (2026-08-11). Before this
    change the function was `.strip().rstrip(".").lower()`, and a full-width
    `ｍａｉｌｉｎａｔｏｒ.com` survived it unfolded — a real bypass of a listed domain.

    Which mechanism does what, verified empirically rather than assumed (an earlier draft of
    this docstring credited NFKC for the main path and was wrong):

    * **`.encode("idna")` does the folding on the success path.** Python's IDNA codec applies
      nameprep, which itself includes NFKC — so full-width forms fold to ASCII even with the
      explicit `normalize` call removed.
    * **The explicit NFKC is load-bearing on the FAILURE path only.** When IDNA rejects the
      input (empty label, over-long label) the `except` branch returns `folded`. Without
      NFKC that fallback returns the *unfolded* text, so `ｍａｉｌｉｎａｔｏｒ..com` would bypass a
      listed domain. Tested — see `test_nfkc_matters_when_idna_encoding_fails`.
    * **Homoglyphs are kept DISTINCT, not folded.** Cyrillic `о` and Latin `o` are separate
      characters, not compatibility variants, so neither NFKC nor nameprep merges them.
      `mailinatоr.com` encodes to `xn--mailinatr-l4g.com`. That is correct: it is a
      different registrable domain and cannot receive mail sent to the ASCII one, so denying
      it would mean denying a domain nobody listed. Email verification is what proves
      control there.

    Deliberately NOT claimed: this does not make the denylist authoritative. A lookalike
    domain the attacker actually controls (`mailinator.co`, a different TLD) is simply not on
    the list, and no amount of normalisation finds it. The denylist is defence-in-depth
    behind email verification, `fleet-guest` scoping, and the pool WAF — see the module
    docstring. This closes the *encoding* bypass class, not the *enumeration* one.

    Never raises: a domain that cannot be encoded folds back to the NFKC-lowercased form so
    the caller still gets a usable string to compare. A normalisation helper that throws on
    hostile input becomes a denial-of-service on the signup path.
    """
    folded = unicodedata.normalize("NFKC", domain).strip().rstrip(".").lower()
    try:
        # encode() applies IDNA (punycode) per label; decode back to str for comparison.
        return folded.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        # Malformed or empty labels — IDNA rejects e.g. "" and over-long labels.
        return folded


def _extract_domains(event: dict) -> list[str]:
    """Return the normalised domain(s) this registration could be using.

    Both `request.userAttributes.email` and `userName` are considered: for an
    email-alias pool the username usually *is* the address, so inspecting only one
    field leaves the other as a bypass. Values without an `@` are skipped rather than
    guessed at.
    """
    candidates = [
        event.get("request", {}).get("userAttributes", {}).get("email", ""),
        event.get("userName", ""),
    ]
    domains = []
    for value in candidates:
        if not isinstance(value, str) or "@" not in value:
            continue
        domain = _normalise_domain(value.rsplit("@", 1)[-1])
        if domain:
            domains.append(domain)
    return domains


def _is_denied(domain: str, denylist: frozenset[str]) -> bool:
    """Exact match, or any subdomain of a denied domain.

    Subdomain matching is required, not defensive tidiness: mailinator and several
    others accept arbitrary subdomains, so `anything.mailinator.com` delivers to the
    same throwaway inbox that `mailinator.com` does.
    """
    if domain in denylist:
        return True
    return any(domain.endswith("." + denied) for denied in denylist)


# Loaded once per container.
_DENYLIST = _load_denylist()


def _log(*, trigger_source: str, outcome: str, domain: str = "", **extra: Any) -> None:
    """Emit one structured JSON line.

    `domain` is recorded only on denial. It is operator-side telemetry, and the deny
    decision was made against a published list of disposable providers, so it carries
    no user identity — unlike the local part of the address, which is never logged.
    Group 9's `PreSignUp-Denials` alarm is a metric filter on `outcome=denied`.
    """
    payload = {"trigger_source": trigger_source, "outcome": outcome, **extra}
    if domain:
        payload["domain"] = domain
    _logger.info(json.dumps(payload))


def handler(event: dict, context: Any) -> dict:  # noqa: ARG001
    """Pre-sign-up trigger entry point.

    Returns the event unchanged for every flow except a self-registration from a
    denied domain, which raises.
    """
    t_start = time.monotonic()
    trigger_source: str = event.get("triggerSource", "")

    # Trigger-source check comes FIRST, before the email is parsed at all — the
    # federation and admin-create flows must be untouched by anything below.
    if trigger_source != _SELF_SIGNUP_TRIGGER:
        _log(
            trigger_source=trigger_source,
            outcome="bypass",
            duration_ms=round((time.monotonic() - t_start) * 1000, 1),
        )
        return event

    for domain in _extract_domains(event):
        if _is_denied(domain, _DENYLIST):
            _log(
                trigger_source=trigger_source,
                outcome="denied",
                domain=domain,
                # Bare token the CloudWatch metric filter matches on — see
                # _DENIAL_METRIC_TOKEN. Do not rename without updating ui_stack.
                event=_DENIAL_METRIC_TOKEN,
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
            )
            # Cognito surfaces this message to the caller. Generic on purpose.
            raise Exception(_DENIAL_MESSAGE)

    _log(
        trigger_source=trigger_source,
        outcome="allowed",
        duration_ms=round((time.monotonic() - t_start) * 1000, 1),
    )
    # autoConfirmUser / autoVerifyEmail are deliberately NOT set — email verification
    # is the control that proves the registrant holds the mailbox.
    return event

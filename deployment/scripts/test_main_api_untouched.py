#!/usr/bin/env python3
"""Guard: ``modules/cms_ui/source/handlers/main_api/index.py`` is untouched.

Purpose
-------
Three concurrent specs are actively editing parts of the CMS codebase as of
this spec's start (2026-09-02-cms-fleet-intelligence-v1).  Two of them —
``2026-08-07-cms-account-provisioning-model`` and the ongoing diagnostics-
platform work — legitimately edit ``main_api/index.py``.

The fleet-intelligence v1 spec explicitly **does not** edit that file.  This
test pins the file's SHA-256 at the moment the spec was authored and asserts
that it has not drifted under this spec's changes.

Digest capture
--------------
  File   : modules/cms_ui/source/handlers/main_api/index.py
  Size   : 469,556 bytes
  SHA-256: 7a1ba7ed1d92d29a2a0b1b0ec740a5dca77b3bf8752ab626dcd28e66f60161f6
  Commit : b5466d5c  (spec(diagnostics-platform): Stage 1 gate PASS/PASS — close
                       Stage 1, hand off Group 4)
  Captured: 2026-09-03

Re-pin history (append-only)
----------------------------
  2026-09-04 — 246810a3c8f6884419b08dd65eef36c9f9de6f78707d96ed51aea37574a359c5
    Reason : Fix Group 7a in spec 2026-08-07-cms-account-provisioning-model
             added the missing authz guard on
             POST /api/v1/fleet-actions/{id}/{approve|reject}.
    Rebase recorded in
      .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/decisions.md.

  2026-09-05 — e2dff67a435df921602b6e87e620e22eeccaf3143abd27fe4d664e09b9eb95ea
    Size   : 475,440 bytes
    Reason : Authz guards added to POST /api/v1/fleet-campaigns/assign and
             POST /api/v1/fleet-campaigns/status — the third and fourth
             members of the same ungated-write family as the 2026-09-04
             re-pin above.  Both routes previously ran route-match -> body
             presence check -> DynamoDB write with no authorization call.
             Closes issues/2026-09-04-fleet-campaigns-post-routes-missing-authz/.
    Scope  : Two guard blocks only.  No other change to the file — the diff
             adds lines and deletes none.  Verified by
             `git diff --numstat` showing 0 deletions on this path.
    Tests  : modules/cms_ui/source/handlers/main_api/test_fleet_campaigns_authz.py
             (13 cases, incl. two positive controls so a blanket deny cannot
             pass).  Whole main_api suite: 260 passed.
    Note   : Applied as an ad-hoc fix rather than under a spec, at the user's
             direction.  Not attributable to either sibling spec, so unlike the
             2026-09-04 entry there is no spec decisions.md to point at — this
             entry plus the issue's summary.md ARE the record.

  2026-09-09 — 76c8cc165aae70fb6c1e3c0b9c84bc483a6743b1bda83044e5bb6caa576613a6
    Size   : 432,125 bytes
    Reason : Spec ``2026-09-05-cms-vfo-teardown`` retired the Virtual Fleet
             Operator.  Commit 82968f29 ("spec(vfo-teardown): groups 1-5 code +
             tests — VFO retired") removed six route handlers from this file:
             GET /api/v1/daily-briefing, /fleet-health, /fleet-actions and
             /decision-journal, the POST /fleet-actions/{id}/{approve|reject}
             subroute, and the GET /api/v1/documents VFO KB reader.
    Scope  : +63 / -781 lines; file shrank 475,440 -> 432,125 bytes.  Net
             deletion, consistent with route removal.  Attributable to the
             teardown spec, not to fleet-intelligence.
    Tests  : main_api/test_vfo_routes_absent.py (7 params + negative control)
             pins the removal.  Whole main_api suite: 253 passed.
    Note   : Reviewing this diff before re-pinning surfaced a defect that the
             teardown's own review.md and security-review.md both missed — the
             removed GET /api/v1/documents is still called by
             DocumentBrowser.tsx:34 and DocumentViewer.tsx:28, both reachable
             (App.tsx:1425 route, and VehicleDetailView.tsx:2002).  Filed as
             issues/2026-09-09-documents-route-removed-with-live-ui-callers/.
             Re-pinning records drift; it does not certify the change is
             defect-free.  The pin advances because the change is attributable
             and intended, and the defect is tracked separately.

  2026-09-09 — 5819aa68400810348dfcac3fe43a9b7ed17303756621a547bf979788ab9069d7
    Size   : 438,350 bytes
    Reason : Spec ``2026-09-02-cms-dms-service-convergence`` T4.2 added
             GET /api/v1/dealers — a server-side read-through to DMS's
             fleet-audience dealer roster, replacing the hardcoded
             six-entry service-centre array the scheduling pickers embedded
             (which had already been emptied to ``[]``, so the pickers were
             offering zero options).  Server-side rather than browser-direct
             per that spec's D3.
    Scope  : +122 / -0 lines; one new route branch inserted immediately above
             the ``# Service History Endpoints`` marker.  Nothing else in the
             file is touched — verified by ``git diff --numstat`` showing 0
             deletions on this path.  Deliberately does NOT touch the
             service-history routes: those are that spec's Group 5, gated
             separately.
    Tests  : modules/cms_ui/source/handlers/main_api/test_dealers_endpoint.py
             (16 cases).  Ten are failure paths, each asserting the response is
             an error AND carries no ``dealers`` key — a 200-with-empty-list on
             a failure would render as "there are no service centres", which is
             a claim about the world where the truth is "we could not ask".
             Five mutations applied and each caught: return an empty roster when
             the endpoint is unset; drop the https guard; call the
             dealer-audience path; substitute a service credential for the
             caller's token; flatten every DMS status to 500.
             Whole main_api suite re-run — see decisions.md.
    Note   : Attributable to the convergence spec, not to fleet-intelligence.
             Recorded here because this guard's own protocol names this exact
             case; the convergence spec's decisions.md carries the matching
             entry.  Two properties worth knowing without reading the diff:
             CMS makes NO authorization decision in the new route — it forwards
             the caller's Cognito token so DMS authorizes the end user (that
             spec's D2/D3, and the shape the fail-open P0 taught) — and the
             route refuses a non-``https://`` endpoint before any network call,
             because the caller's bearer token travels over that URL.

  2026-09-09 — 54871c3ff5882a828408a8d98b41dc46aba4af2344678da680d585b62dafdf4a
    Size   : 440,001 bytes
    Reason : Same spec, same route, security review Cycle 6 Suggestion 1.  The
             ``https://`` guard recorded in the entry above runs exactly once,
             before the first byte.  ``urlopen``'s default opener then follows
             any ``Location`` it is handed, re-sending the ``Authorization``
             header — so a compromised or misconfigured DMS could answer
             ``302 Location: http://attacker/`` and receive a live Cognito
             token that the one-shot scheme check never re-examined.  The route
             now builds its own opener with a redirect handler that refuses
             every redirect, and the 3xx lands on the existing error path.
    Scope  : +30 / -1 lines, all inside the ``GET /api/v1/dealers`` branch —
             the deleted line is ``urllib.request.urlopen(...)`` becoming
             ``_opener.open(...)``.  Verified by ``git diff --numstat``.  Still
             does NOT touch the service-history routes (that spec's Group 5).
    Tests   : test_dealers_endpoint.py 16 -> 20 cases; new
             ``RedirectsDoNotCarryTheTokenTests`` (4).  Whole main_api suite:
             273 passed.
    Note   : Two findings from mutating this, both recorded in the convergence
             spec's decisions.md as D-G4l.  (1) Reverting only
             ``build_opener(_RefuseRedirects)`` -> ``build_opener()`` fails 3 of
             the 4 new tests, and the failure output IS the leak — the mutant
             tries to resolve ``attacker.example.invalid``.  (2) That mutation
             also exposed a vacuous assertion in my own new test: patching only
             ``HTTPSHandler.https_open`` cannot observe a follow-up hop to an
             ``http://`` target, so ``len(seen) == 1`` held while the token was
             in flight.  Both schemes are now patched into one list.  The fix
             changed the seam the pre-existing tests patched, so all 13
             ``patch('urllib.request.urlopen')`` targets moved to
             ``OpenerDirector.open`` — a better seam regardless, since it
             exercises the real ``build_opener`` construction rather than
             stubbing past it.

  2026-09-10 — 41ab2a39f4388da39a0e558d8223325257277bdea84a90f0034b7caaf4d38646
    Size   : 464,290 bytes
    Reason : Spec ``2026-09-02-cms-dms-service-convergence`` T5.2 (Group 5).
             GET /api/v1/service-history and POST /api/v1/service-history become
             DMS-backed when DMS_API_ENDPOINT is set with an https:// URL.
             Legacy behaviour preserved when the env var is unset (inert on deploy
             until an operator opts in, per D-G5b).  _RefuseRedirects hoisted from
             inside the GET /api/v1/dealers branch to module level so the same
             redirect-refusal is shared by all three token-bearing outbound calls
             (per D-G5f).  _DMS_OWNED_SERVICE_FIELDS frozenset added at module
             level for the merge-on-read semantics (D-G5c).
             VIN→vehicleId two-step resolver added inside the handler (D-G5e):
             GetItem first (backfilled case), then Query on vin-index (real VIN).
             No scan fallback — an unresolvable value yields (id, None) and logs.
    Scope  : +423 / -75 lines.  Changes: (1) import hashlib, import json,
             import urllib.error/request moved to module level; (2) _DMS_OWNED_
             SERVICE_FIELDS frozenset + _RefuseRedirects class added after
             _project_service_records; (3) inline ``import urllib...`` and
             ``class _RefuseRedirects`` removed from dealers branch (uses
             module-level class now); (4) GET and POST service-history handlers
             replaced with DMS-backed implementations.
             PATH-GET (/api/v1/service-history/{id}) unchanged.
    Tests  : modules/cms_ui/source/handlers/main_api/test_service_history_dms_backed.py
             (27 cases, one per D-G5b..h decision; 8 mutations applied, 8 caught).
             Also: deployment/stacks/test_main_api_vin_index_grant.py (4 cases —
             pins the vin-index IAM statement and the no-wildcard-index invariant).
             ui_stack.py: new AppAccess PolicyStatement for dynamodb:Query on
             {vehicles}/index/vin-index, per D-G5e.
             Whole main_api suite: 273 → 300 passed.
    Note   : D-G5a (gate outcome): ``2026-08-07-cms-account-provisioning-model``
             is absent from CMS currentspec.md; T5.2 proceeded.  The concurrent
             ``2026-09-09-cms-publish-scanner-baseline-cleanup`` session claims
             this file for comment-only scrubs; its Verify cannot see the digest
             pin it will break.  Resolution: the scanner-baseline session must
             re-pin when it lands.  Recorded in this entry per the protocol.
             D-G5f acceptance criterion (redirect tests stay green): all 20
             test_dealers_endpoint.py cases including the 4 redirect tests pass
             with no edits to that file — the hoist is behaviour-preserving.
             Commit does not push; push authority is the operator's.

  2026-09-10 — b9d5267e372ad28958fb3a64789671cb27d85a3d7180ba86fa4fbbf644128326
    Size   : 472,283 bytes
    Reason : Spec ``2026-09-02-cms-dms-service-convergence`` Fix Group 4 (D-G5i).
             Ten defects found by architect verification of ec73c367. The live path
             was a silent no-op: envelope key fixed ("repairOrders" → "items" per
             DMS ok_response). _ro_to_service_record mapper added implementing the
             D-G5i field table (ro_id→serviceId, dealer_id→dealerId,
             dealer_name→provider, opened_at||created_at→serviceDate, etc.).
             _DMS_OWNED_SERVICE_FIELDS corrected to CMS camelCase names.
             POST: {entry} envelope unwrap, serviceDate preserved (sort key),
             no VIN substitution on unresolvable (explicit 502), cache fidelity
             (all cache-supplied fields written). GET: ?vin= removed from DMS URL
             (handler reads no query params), vehicleId client-side filter added,
             limit/serviceType applied to merged list, sort newest-first, asOf
             prefers cachedAt. cache_read_ok removed (unused).
    Scope  : +161 / -83 lines in main handler section.  _DMS_OWNED_SERVICE_FIELDS
             and _ro_to_service_record() added at module level (+101 lines);
             GET/POST handler fixed (+60/-83).
    Tests  : test_service_history_dms_backed.py: 27→44 (+17 new: TestF4_1_
             EnvelopeAndMapper 9 cases, TestF4_2_PostPath 3, TestF4_3_GetQuery
             Semantics 4). test_dms_contract_pin.py NEW (2 cases: cross-repo pin
             reads DMS source, envelope key assertion; three-way demo verified).
             Whole main_api suite: 300→319 passed.  test_dealers_endpoint.py and
             test_service_history_projection.py: 49 passed, no edits (F4.3).
    Note   : Fix Group 4 addresses D-G5i lesson: fixtures must match DMS's real
             shape, not invented shapes. Mutation testing verifies load-bearing
             assertions; it cannot verify they are about the real contract.
             Only reading the other side's source answers that question.

  2026-09-10 — d98a25c58469ec0854b355d82d95b71af5aba7952df53afe9558664f69796e64
    Size   : 474,116 bytes
    Reason : Spec ``2026-09-02-cms-dms-service-convergence`` Fix Group 5 (D-G5j).
             Two defects in the GET merge, both in the same three lines, both
             the *composition* failing while each part is right.
             F5.1: removed the unconditional _cache_non_dms exclusion.  The
             old code stripped any cache key in _DMS_OWNED_SERVICE_FIELDS
             regardless of whether the mapper supplied a value for that key —
             discarding vin/status/serviceDate from the cache when DMS omitted
             them.  Merge is now {**_cache_extras, **_mapped}: _mapped wins
             where it has a key, cache contributes where _mapped is silent.
             This also restores the merge order as the load-bearing property
             (a reversal now changes the output, so the seam test catches it).
             F5.2: added a per-row memoized resolver for the unscoped GET
             (GET /api/v1/service-history?limit=500, the dashboard call).
             Previously resolved_vehicle_id='' was passed to every row on this
             path.  Now each distinct vehicle_vin is resolved via the existing
             two-step resolver (GetItem, then vin-index Query), memoized per
             request so N rows on M vehicles cost M lookups.  No scan fallback.
    Scope  : +25 / -8 lines in the DMS-backed GET handler section.  The
             _cache_non_dms intermediate dict is removed; _vin_resolve_memo
             dict and _resolve_for_row() closure are added before the
             dms_records loop; _ro_to_service_record call updated to use
             per-row vehicleId.
    Tests  : test_service_history_dms_backed.py: 319->325 passed (+6 new seam
             tests in TestF5_1_MergeSeam and TestF5_2_UnscopedGetVehicleId).
             Seam tests verified by mutation: merge-order reversal caught by
             TestF5_1_MergeSeam::test_seam_status_survives_from_cache_when_dms_omits_it
             (Case A); memoization removal caught by
             TestF5_2_UnscopedGetVehicleId::test_two_ros_same_vehicle_cost_one_resolver_call.
             test_dealers_endpoint.py, test_service_history_projection.py,
             test_dms_contract_pin.py: 49 passed, no edits.
    Note   : Fix Group 5 closes the final two defects in the T5.2 merge path.
             The merge-order mutation was previously MISSED (Fix Group 4
             reported it as structurally unreachable because the two sets were
             disjoint). After F5.1 the sets overlap, so the mutation is now
             reachable and is caught.

    Why    : F5.1 comment correction (architect verification of `69678a03`).
             The F5.1 comment's first line read "merge is {**_mapped,
             **_cache_extras}" while the code correctly does the reverse. A
             future reader trusting the comment over the code would have
             reinstated the exact defect F5.1 removed, so the comment is the
             kind of wrong that regenerates a bug.
    Scope  : one comment line. No behaviour change.
    Tests  : main_api 325 passed, unchanged. Merge-order mutation
             independently re-run by the architect: CAUGHT by 3 tests
             (TestD_G5c_MergeSemantics x2, TestF5_1_MergeSeam x1).

  2026-09-10 — da8d8c26524bddb0277f82e942bdb728e2504559a1f9357d8dcdf61df0d391ff
    Size   : 474,930 bytes
    Reason : Spec ``2026-09-02-cms-dms-service-convergence`` Fix Group 6
             (security review Cycle 8).
             F6.2: Three resolver print() sites in _resolve_vin_for_vehicle_id
             that emitted vehicle_id_value verbatim now redact to ***<last6>
             via a byte-for-byte copy of _redact_vin from
             services/data_processing/lambda/dms_ro_cache_invalidator/handler.py.
             F6.3: The DMS-path POST outer except block interpolated str(e) into
             the 500 response body; a boto3 AccessDeniedException carries the
             full role ARN.  The body now returns a generic message; the detail
             is logged to CloudWatch (stdout) instead.  The legacy branch
             (DMS_API_ENDPOINT unset) is untouched.
    Scope  : +14 / -3 lines.  Changes: (1) _redact_vin() helper added as a
             local nested function before _resolve_vin_for_vehicle_id; (2) the
             three print() calls inside that resolver updated to call it; (3)
             the DMS-path outer except body changed from str(e) interpolation
             to a print() + generic message.  Nothing else touched.
    Tests  : F6.2 mutation-verified against the vin-index miss branch (full VIN
             appeared in stdout when reverted; TestF6_2_VinRedaction).
             F6.3 mutation-verified (ARN appeared in body when str(e) restored;
             TestF6_3_Post500BodyRedaction).
             Whole main_api suite: 328 passed.

    Why    : Fix Group 7 (F7.1 + F7.2) — closes security review Cycle 9.
             F7.1: the caller-supplied identifier must not reach CloudWatch by
             ANY channel. F6.2 redacted the format field and left the adjacent
             exception interpolating raw, and a botocore ValidationException
             echoes the offending value — so one line both redacted and
             published the same VIN. New _scrub_identifier() replaces every
             occurrence in the exception text; the exception CLASS is still
             named so a broken index does not look merely slow.
             F7.2: the generic 500 gained a correlationId (getattr fallback,
             because context is None in every test in this suite and a bare
             attribute access would raise inside the handler's own error path).
    Scope  : +63 / -4 in index.py — _scrub_identifier helper, two resolver
             exception log lines, the DMS-path POST 500 branch.
    Tests  : 328 -> 334. Four mutations, all CAUGHT:
               step1 exception back to raw {_e}  -> test_step1_exception_text_is_scrubbed
               step2 exception back to raw {_e}  -> test_step2_exception_text_is_scrubbed
               correlationId dropped from body   -> both TestF7_2 tests
               bare context.aws_request_id       -> TestF7_2 + TestF6_3
    Note   : Cycle 9 established that of F6.2's three sites only the miss branch
             had coverage — reverting either EXCEPTION site produced no failure.
             The F7.1 tests are therefore written per exception site, and the
             property is stated as "does not reach stdout" rather than as a list
             of sites. A test derived from a site list cannot see a channel that
             is not on the list, which is how {_e} survived the fix meant to
             close it.

    Why    : Cycle 10 Suggestions 1 and 2, both comment-only.
             S1: _scrub_identifier's justification for the < 6 branch stated the
             OPPOSITE of the code — it said _redact_vin "discloses the whole
             value", where _redact_vin actually returns a bare "***". The real
             reason to decline is message corruption, not disclosure. Fixed
             rather than deferred because a comment that inverts its own code is
             the defect class F5.1 already had to fix once on this same file,
             and it regenerates bugs.
             S2: narrowed the "any channel" claim to the caller-supplied vehicle
             identifier, so it cannot be misread as module-wide while the
             DMS-path POST 500 logs its exception raw by design.
    Scope  : two comment blocks. No behaviour change; 334 passed, unchanged.

    Why    : removed a function-local `import hashlib` from inside handler().
             It shadowed the module-level import for the ENTIRE function, so the
             nested _bfsh_key closure referenced an unbound local and every live
             GET /api/v1/service-history returned 500 with "free variable
             'hashlib' referenced before assignment". Found by the first real
             call against staging; 334 unit tests missed it because every
             fixture carried roId/ro_id, so both `or` short-circuits took the
             left branch and the bfsh- fallback never executed.
    Scope    : one deleted line + 3 tests that reach the fallback.
    Tests    : 334 -> 337. Mutation (restore the local import) fails all 3.

    Why    : SAME BUG CLASS, THIRD INSTANCE — `Decimal` this time. Live staging
             returned 200 at ?limit=3 and 500 at ?limit=200 with "free variable
             'Decimal' referenced before assignment in enclosing scope".
             `Decimal` is imported at module level AND re-imported ~30 times
             inside handler(), making it a handler-local; none of those execute
             on the service-history path, so `_sh_decimal_default` closed over an
             unbound local. It only fired at the larger limit because
             json.dumps(default=...) invokes the callback ONLY on meeting a value
             it cannot serialise, and only the bigger page contained one.
             `_sh_decimal_default`'s own docstring described this very trap for
             the FUNCTION name (it was renamed to dodge the `decimal_default`
             collision) while having it on the TYPE name one level down.
             The hashlib guard added hours earlier did not catch it: it asserted
             one literal string. Replaced with an AST guard over the MECHANISM —
             any nested def in handler() referencing a name that is both
             module-imported and re-imported inside handler() must import it in
             its own body. That guard also fails the original hashlib mutation,
             so it subsumes the narrow one (both retained).
             Also hardened 5 latent `_dec` helpers that worked only because a
             sibling `from decimal import Decimal` happened to run one line
             earlier — ordering, not scoping. That is the luck that ran out here.
    Scope    : 6 def-local imports added, 1 docstring expanded, no behaviour
               change on any path that already worked.
    Tests    : 337 -> 339. Mutations (drop the def-local import; restore the
               hashlib local) each fail both new tests.

    Why    : POST path 500'd on any float in the body ("Float types are not
             supported" from DynamoDB) and did so AFTER D-G5h's DMS booking had
             committed, so the caller saw a failure while a real repair order
             existed at a real rooftop; a retry made a second one. Confirmed live
             (dms-staging RO 5fdad446, zero matching CMS rows). Fix parses the
             body with parse_float=Decimal in BOTH POST branches, which also
             moves the failure class out of the post-commit window because
             parsing precedes the DMS call.
             The first attempt at that fix reintroduced the shadowed-import bug
             a fifth time: `Decimal` is a handler-local via ~35 function-local
             imports, none of which run on the POST path, so the bare reference
             raised UnboundLocalError. Caught by the new test before shipping.
             Both use sites now carry a def-local import.
    Scope    : 2 json.loads calls + 2 local imports + comments.
    Tests    : 339 -> 341 (TestPostFloatHandling). Mutations: dropping
               parse_float fails test_no_float_reaches_dynamodb; dropping the
               local import fails both.

If this test fails in a future group
-------------------------------------
A failing test here means a concurrent session's commit has landed on the
branch since this spec was authored — which is **expected and correct**:
``2026-08-07-cms-account-provisioning-model`` and the diagnostics-platform
spec both touch this file intentionally.

The correct resolution is:
  1. Identify the commit that changed the file (``git log --oneline
     modules/cms_ui/source/handlers/main_api/index.py``).
  2. Confirm the change belongs to a sibling spec, not to fleet-intelligence.
  3. Re-pin: update ``EXPECTED_DIGEST`` below to the new digest
     (``shasum -a 256 modules/cms_ui/source/handlers/main_api/index.py``).
  4. Record the re-pin in ``decisions.md`` with the commit subject of the
     change that caused it.

**Never delete this guard.**  Its purpose is to make an accidental or
scope-creeping edit to main_api visible at the group boundary, not to block
legitimate sibling-spec changes that are properly tracked.

Run (must PASS):
    python3 -m pytest deployment/scripts/test_main_api_untouched.py -v
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# ── Pin ───────────────────────────────────────────────────────────────────────
#
# Re-pin this value if a sibling spec legitimately changes the file.
# Record the re-pin and its reason in decisions.md.
#
# ── Digest history (append-only) ──────────────────────────────────────────────
# Every re-pin appends a line here with the previous digest, the commit that
# caused the change, and a one-line reason.  Do NOT delete rows.  The audit
# trail is what makes a rebase reviewable — a bare new digest is a claim
# without evidence.
#
# 2026-09-10 · 288381cd88f44d3c0202573dc2d8d2231863d5270e64e73be38f9da4d0338c0c
#   → spec 2026-09-10-service-history-read-path-correctness Group 3:
#     append ?vehicle_vin=<resolved> to DMS URL (D1); delete client-side
#     VIN filter (D1); stamp provenance='dms' on merged records and
#     provenance='cache' + serviceId=roId fallback on cache-only rows
#     (D3, D4); rename cache label to 'mixed' when DMS answered AND
#     cache-only rows exist (D3); emit cacheOnlyCount always; emit asOf
#     only when cacheOnlyCount > 0 and take the OLDEST cache-only row's
#     timestamp (D3, worst-case staleness); add 'provenance' to
#     _SERVICE_HISTORY_RESPONSE_FIELDS.  Mutation-verified: dropping the
#     VIN query, restoring the client filter, changing 'mixed'→'cache',
#     changing min→max on asOf, and omitting provenance='dms' each fail
#     a named T1.3 test.
#
EXPECTED_DIGEST = (
    "0b49517a4e7669ece69246f2dbf114cd1acf518ce643d83db6ab4afab2f297cc"
)

MAIN_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "modules"
    / "cms_ui"
    / "source"
    / "handlers"
    / "main_api"
    / "index.py"
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Test ──────────────────────────────────────────────────────────────────────


def test_main_api_index_digest_unchanged() -> None:
    """main_api/index.py must match the digest pinned at spec authoring time.

    If this fails, a sibling spec has edited the file.  Re-pin the digest
    and record the rebase in decisions.md — never delete this guard.
    See the module docstring for the full resolution procedure.
    """
    assert MAIN_API_PATH.exists(), (
        f"Expected file not found: {MAIN_API_PATH}\n"
        "Check that MAIN_API_PATH is correct relative to the repo root."
    )

    actual = _sha256(MAIN_API_PATH)
    assert actual == EXPECTED_DIGEST, (
        f"main_api/index.py has changed since this spec was authored.\n"
        f"  Expected digest : {EXPECTED_DIGEST}\n"
        f"  Actual digest   : {actual}\n\n"
        "This is expected if a concurrent spec (account-provisioning-model or "
        "diagnostics-platform) has landed new commits.  Re-pin EXPECTED_DIGEST "
        "in this file to the actual value above, and record the rebase in "
        "decisions.md with the subject of the commit that caused the change.\n"
        "DO NOT delete this test."
    )

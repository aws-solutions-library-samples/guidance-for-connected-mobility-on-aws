"""
Fleet-scope authorization for /api/v1/fleet-intelligence/* routes.

Issue: issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/
Pattern: services/connectors/oem1/_lib/fleet_membership.py + the group parser
         every OEM1 admin handler wraps around it.

Every FI route ends up going through `index._authorize_or_403` before the
route dispatch table. This module pins:

  1. **Route enumeration is derived from ui_stack.py, not hand-listed.** The
     `_fi_method` calls in the CDK stack are the definition of "an FI route";
     an AST walk enumerates them so a new route added without an authz test
     fails this file — that is the mutation guard for the whole surface.

  2. **Every enumerated route rejects an unauthorized caller with 403** — a
     `fleet-operator` asking for a fleet they do not hold.

  3. **The demo personas measured on the CMS staging pool 2026-09-25 still
     work** — `FleetManager@example.com` (platform-admin) is cross-fleet;
     `fleet.operator.verify@example.com` (fleet-operator with
     `custom:fleetIds=flt-meridian-range-001,FLEET-DEMO-PUBLIC`) still reads
     each of their own fleets.

  4. The EventBridge scheduled `refresh-lifecycle-cache` invocation is NOT
     gated by fleet authz — it carries no claims and is not an API Gateway
     event.

Every test in this file opts out of the conftest default-admin-claims
fixture via `@pytest.mark.no_default_admin_claims`.
"""
from __future__ import annotations

import ast
import json
import pathlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Route enumeration — derived from deployment/stacks/ui_stack.py
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_UI_STACK = _REPO_ROOT / "deployment" / "stacks" / "ui_stack.py"


def _enumerate_fi_routes():
    """AST-walk ui_stack.py and return the list of (http_method, resource_path)
    pairs mounted via `_fi_method(res, "METHOD")`.

    Reconstructs the resource path by walking each `_fi_method` call's first
    argument back through the chain of `add_resource("segment")` calls (and
    their assigned bindings) to the ``fi_root = ...add_resource("fleet-
    intelligence")`` anchor. That anchor itself is rooted at ``v1_resource``,
    which we know from the same stack is ``/api/v1``. This is deliberately
    fragile-in-a-useful-way: if the CDK author renames the anchor or restructures
    the wiring, the enumeration fails loudly rather than silently under-counting
    routes.
    """
    src = _UI_STACK.read_text()
    tree = ast.parse(src, filename=str(_UI_STACK))

    # Walk the module: within each function/method body, collect the
    # `Name = call` assignments that describe the resource tree so we can
    # resolve `_fi_method(<name>, "GET")` back to a URL path.
    fi_method_calls: list[tuple[str, str, ast.Call]] = []
    # segment_for_name[name] = ("literal-segment", parent_name) — the segment
    # added by an `add_resource` call — or ("__ROOT__", None) for `fi_root`.
    segment_for_name: dict[str, tuple[str, str | None]] = {}

    def _resource_expr_name(expr: ast.AST) -> str | None:
        """If `expr` is `<Name>.add_resource("seg")` or `self.api.root.add_resource("seg")`,
        return the parent identifier (Name.id or `__ROOT__`); else None."""
        if not (isinstance(expr, ast.Call)
                and isinstance(expr.func, ast.Attribute)
                and expr.func.attr == "add_resource"):
            return None
        base = expr.func.value
        if isinstance(base, ast.Name):
            return base.id
        # self.api.root — Attribute(value=Attribute(value=Name('self'), attr='api'), attr='root')
        if (isinstance(base, ast.Attribute)
                and base.attr == "root"
                and isinstance(base.value, ast.Attribute)
                and base.value.attr == "api"
                and isinstance(base.value.value, ast.Name)
                and base.value.value.id == "self"):
            return "__ROOT__"
        return None

    for node in ast.walk(tree):
        # Track `fi_root = <expr>.add_resource("fleet-intelligence")`.
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            call = node.value
            parent = _resource_expr_name(call)
            if parent is not None:
                seg = call.args[0].value if (call.args and isinstance(call.args[0], ast.Constant)) else None
                if seg is not None:
                    segment_for_name[target] = (seg, parent)

        # Track `_fi_method(<arg>, "METHOD")` and `_fi_method(<arg>.add_resource("seg"), "METHOD")`.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_fi_method":
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                continue
            http = node.args[1].value
            arg0 = node.args[0]
            if isinstance(arg0, ast.Name):
                name = arg0.id
            elif isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Attribute) and arg0.func.attr == "add_resource":
                # Inline `.add_resource("seg")` — synthesise a name and record the segment.
                base = _resource_expr_name(arg0)
                seg = arg0.args[0].value if (arg0.args and isinstance(arg0.args[0], ast.Constant)) else None
                if base is None or seg is None:
                    continue
                synth = f"__inline_{id(arg0)}"
                segment_for_name[synth] = (seg, base)
                name = synth
            else:
                continue
            fi_method_calls.append((http, name, node))

    def _path_for(name: str) -> str:
        """Walk parent chain to build the resource path relative to the API root."""
        parts: list[str] = []
        seen: set[str] = set()
        cur: str | None = name
        while cur is not None and cur != "__ROOT__":
            if cur in seen:
                raise RuntimeError(f"cycle in resource chain at {cur!r}")
            seen.add(cur)
            seg_parent = segment_for_name.get(cur)
            if seg_parent is None:
                raise RuntimeError(f"no segment recorded for {cur!r} in ui_stack.py")
            seg, parent = seg_parent
            parts.append(seg)
            cur = parent
        parts.reverse()
        return "/" + "/".join(parts)

    return [(http, _path_for(name)) for http, name, _ in fi_method_calls]


# Enumerate ONCE at import — a static-analysis result about ui_stack.py, not a
# runtime measurement. If ui_stack.py cannot be parsed the tests fail at
# collection, which is the intended fail-mode: "no routes enumerated" is a
# regression.
_FI_ROUTES = _enumerate_fi_routes()


def test_route_enumeration_is_non_empty():
    """AST walk must find the FI routes — a zero count means the enumeration
    broke, not that the FI surface disappeared."""
    assert len(_FI_ROUTES) >= 6, (
        f"AST enumeration found only {len(_FI_ROUTES)} FI routes; expected >=6. "
        "If ui_stack.py's `_fi_method`/`add_resource` shape changed, update "
        "_enumerate_fi_routes to match — do not lower this threshold."
    )


def test_expected_routes_are_enumerated():
    """Anchor the enumeration against the routes documented in index.py's
    dispatch table so a silent drift on either side surfaces here.

    This is a set comparison, not an ordered one — the CDK stack's call order
    is not part of the contract, only the resulting (method, path) set is.
    """
    got = {(m, p) for m, p in _FI_ROUTES}
    expected = {
        ("GET",  "/api/v1/fleet-intelligence/cpm"),
        ("GET",  "/api/v1/fleet-intelligence/cpm/outliers"),
        ("GET",  "/api/v1/fleet-intelligence/lifecycle"),
        ("GET",  "/api/v1/fleet-intelligence/lifecycle/{vehicleId}"),
        ("GET",  "/api/v1/fleet-intelligence/pm/schedules"),
        ("POST", "/api/v1/fleet-intelligence/pm/schedules"),
        ("GET",  "/api/v1/fleet-intelligence/pm/compliance"),
        ("POST", "/api/v1/fleet-intelligence/pm/schedules/{id}/complete"),
    }
    assert got == expected, (
        f"Route enumeration drift.\n  ui_stack.py has: {sorted(got)}\n"
        f"  test expected: {sorted(expected)}\n"
        "If a new FI route is added, update BOTH this set AND ensure it is "
        "gated by _authorize_or_403 in services/fleet_intelligence/index.py."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_ADMIN_CLAIMS = {"cognito:groups": "platform-admin"}
_VIEWER_CLAIMS = {"cognito:groups": "fleet-viewer"}
_OPERATOR_MERIDIAN_CLAIMS = {
    # Measured on the CMS staging pool 2026-09-25 for
    # fleet.operator.verify@example.com — an fleet-operator with two fleets.
    # The CSV shape is the live encoding as of that date (see
    # lambdas/api-avx-findings/handler.py:_caller_fleet_ids for the same
    # observation).
    "cognito:groups": "fleet-operator",
    "custom:fleetIds": "flt-meridian-range-001,FLEET-DEMO-PUBLIC",
}
_OPERATOR_OTHER_CLAIMS = {
    "cognito:groups": "fleet-operator",
    "custom:fleetIds": "some-other-fleet",
}
_GUEST_CLAIMS = {
    "cognito:groups": "fleet-guest",
    "custom:fleetIds": "flt-meridian-range-001",
}
_DISPATCHER_CLAIMS = {
    "cognito:groups": "fleet-viewer,dispatcher",
    "custom:fleetIds": "flt-meridian-range-001",
}
_NO_GROUP_CLAIMS = {"custom:fleetIds": "flt-meridian-range-001"}
_UNRELATED_GROUP_CLAIMS = {"cognito:groups": "connected-services"}


def _event(method, resource, *, claims, qs=None, body=None, path_params=None):
    """Build an API Gateway proxy event carrying the given Cognito claims."""
    ev = {
        "httpMethod": method,
        "resource": resource,
        "pathParameters": path_params or {},
        "queryStringParameters": qs,
        "requestContext": {"authorizer": {"claims": claims}},
    }
    if body is not None:
        ev["body"] = json.dumps(body) if not isinstance(body, str) else body
    return ev


def _invoke(event, *, monkeypatch=None):
    """Import + invoke the handler. Stubs the ADP fetch, tire fetch, and DDB
    table access so a 200 reply reflects authz + dispatch only.
    """
    from services.fleet_intelligence import adp_source, index

    _fake_ddb_table = MagicMock()
    _fake_ddb_table.scan.return_value = {"Items": []}
    _fake_ddb_table.get_item.return_value = {"Item": {"purchasePrice": Decimal("60000.0")}}
    _fake_ddb_table.put_item.return_value = {}
    _fake_ddb_table.update_item.return_value = {}
    if monkeypatch is not None:
        monkeypatch.setenv("FI_WINDOW_MONTHS", "12")

    with patch.object(index, "_scan_cost_rows", return_value=[]), \
         patch.object(adp_source, "fetch_tire_health", return_value=[]), \
         patch.object(index, "_table", return_value=_fake_ddb_table):
        return index.handler(event, None)


# ---------------------------------------------------------------------------
# 1. Every route rejects an unauthorized fleetId with 403
# ---------------------------------------------------------------------------
def _route_ids():
    return [f"{m} {p}" for m, p in _FI_ROUTES]


@pytest.mark.no_default_admin_claims
@pytest.mark.parametrize("method,resource", _FI_ROUTES, ids=_route_ids())
def test_unauthorized_fleet_id_gets_403_on_every_route(method, resource, monkeypatch):
    """The core assertion of this fix: a fleet-operator asking for a fleet
    they do NOT hold gets 403 on every /api/v1/fleet-intelligence/* route.

    Parametrised over the AST-enumerated route list so a new route added
    without an authz gate lands here as a failure.
    """
    # Fleet-operator whose custom:fleetIds does NOT include the requested fleet.
    claims = _OPERATOR_OTHER_CLAIMS

    if method == "POST" and resource.endswith("/complete"):
        # pm/complete has no caller-supplied fleetId — the scope is the
        # schedule itself. Route the check via a stored schedule owned by
        # a fleet the caller does not hold.
        path_params = {"id": "sched-1"}
        body = {"vehicleId": "VEH-1", "completedDate": "2026-09-25"}
        event = _event(method, resource, claims=claims, body=body, path_params=path_params)
        # Stub the DDB lookup to return a schedule in flt-meridian-range-001;
        # the caller is scoped to "some-other-fleet".
        from services.fleet_intelligence import index
        fake_table = MagicMock()
        fake_table.get_item.return_value = {
            "Item": {
                "vehicleId": "VEH-1",
                "scheduleId": "sched-1",
                "fleetId": "flt-meridian-range-001",
            }
        }
        with patch.object(index, "_table", return_value=fake_table):
            resp = index.handler(event, None)
    elif method == "POST" and resource.endswith("/pm/schedules"):
        body = {
            "vehicleId": "VEH-1", "fleetId": "flt-meridian-range-001",
            "basis": "mileage", "intervalValue": 5000, "taskCode": "OIL",
            "lastPerformedMileage": 12000, "currentOdometer": 14500,
        }
        event = _event(method, resource, claims=claims, body=body)
        resp = _invoke(event, monkeypatch=monkeypatch)
    else:
        qs = {"fleetId": "flt-meridian-range-001"}
        # /lifecycle/{vehicleId} needs a path param — a syntactically valid
        # one is enough because authz rejects before the handler runs.
        path_params = {"vehicleId": "VEH-1"} if "{vehicleId}" in resource else None
        event = _event(method, resource, claims=claims, qs=qs, path_params=path_params)
        resp = _invoke(event, monkeypatch=monkeypatch)

    assert resp["statusCode"] == 403, (
        f"{method} {resource}: fleet-operator without membership must get 403; "
        f"got {resp['statusCode']} {resp['body']!r}"
    )
    body = json.loads(resp["body"])
    # The response body must NOT echo the requested fleetId (would confirm-or-
    # deny cross-tenant fleet existence to a probing caller).
    assert "flt-meridian-range-001" not in resp["body"]
    # And must not name the caller's own claim.
    assert "some-other-fleet" not in resp["body"]
    assert "error" in body


# ---------------------------------------------------------------------------
# 2. Portal-wide (`__all__` / absent fleetId) is admin-only
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
@pytest.mark.parametrize("qs", [None, {}, {"fleetId": "__all__"}, {"fleetId": ""}, {"fleetId": "   "}])
def test_scoped_caller_cannot_do_portal_wide_reads(qs, monkeypatch):
    """A scoped caller (fleet-operator/-guest/dispatcher) hitting `?fleetId=__all__`
    or a missing/blank fleetId gets 403 — portal-wide reads are for the
    cross-fleet groups only."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims=_OPERATOR_MERIDIAN_CLAIMS, qs=qs)
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 403, (
        f"portal-wide request from fleet-operator must be 403; got {resp['statusCode']} "
        f"{resp['body']!r} (qs={qs!r})"
    )


# ---------------------------------------------------------------------------
# 3. Positive controls — the demo personas measured on staging still work
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_platform_admin_can_read_any_fleet(monkeypatch):
    """FleetManager@example.com's staging groups = [platform-admin, connected-services]."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "platform-admin,connected-services"},
                   qs={"fleetId": "any-fleet"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


@pytest.mark.no_default_admin_claims
def test_platform_admin_can_read_portal_wide(monkeypatch):
    """platform-admin without an explicit fleetId — the aggregated view."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims=_ADMIN_CLAIMS, qs={})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


@pytest.mark.no_default_admin_claims
def test_fleet_viewer_gets_cross_fleet_read(monkeypatch):
    """engineer@example.com's staging groups include `fleet-viewer` — documented
    as "UNSCOPED global-read role in main_api" (useUserRole.ts:16). Preserve
    that invariant on FI too."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "product-engineer,fleet-viewer"},
                   qs={"fleetId": "any-fleet"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


@pytest.mark.no_default_admin_claims
@pytest.mark.parametrize("fleet_id", ["flt-meridian-range-001", "FLEET-DEMO-PUBLIC"])
def test_fleet_operator_can_read_each_of_their_own_fleets(fleet_id, monkeypatch):
    """fleet.operator.verify@example.com's staging claim is
    `custom:fleetIds=flt-meridian-range-001,FLEET-DEMO-PUBLIC` — both must be
    reachable individually, not just the first."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims=_OPERATOR_MERIDIAN_CLAIMS, qs={"fleetId": fleet_id})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


@pytest.mark.no_default_admin_claims
def test_fleet_operator_denied_on_a_fleet_they_do_not_hold(monkeypatch):
    """Positive-counterpart to the parametrised route test — a fleet-operator
    with membership in fleet A cannot read fleet B."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims=_OPERATOR_MERIDIAN_CLAIMS,
                   qs={"fleetId": "not-a-fleet-they-hold"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 403


@pytest.mark.no_default_admin_claims
def test_fleet_guest_can_read_their_fleet(monkeypatch):
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims=_GUEST_CLAIMS, qs={"fleetId": "flt-meridian-range-001"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200


@pytest.mark.no_default_admin_claims
def test_dispatcher_can_read_their_fleet(monkeypatch):
    """kevin.dispatch@example.com's staging groups are `fleet-viewer,dispatcher`
    (2026-09-25). fleet-viewer alone would already grant read; test the
    strict-dispatcher case too so the group's own semantics are pinned."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "dispatcher",
                           "custom:fleetIds": "flt-meridian-range-001"},
                   qs={"fleetId": "flt-meridian-range-001"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# 4. No-group / unknown-group callers cannot read FI at all
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
@pytest.mark.parametrize("claims", [
    _NO_GROUP_CLAIMS,          # has custom:fleetIds but no cognito:groups
    _UNRELATED_GROUP_CLAIMS,   # has connected-services only, no fleet-scoped group
    {},                         # empty claims dict
])
def test_no_fleet_group_denies_read(claims, monkeypatch):
    """A caller lacking any of {platform-admin, fleet-viewer, fleet-operator,
    fleet-guest, dispatcher} gets 403 regardless of fleetId — the same rule
    as the OEM1 admin gate."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims=claims, qs={"fleetId": "flt-meridian-range-001"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# 5. Empty custom:fleetIds cannot masquerade as a match
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_scoped_group_with_empty_fleet_ids_is_denied(monkeypatch):
    """A fleet-operator with an empty `custom:fleetIds` claim is denied on
    every request — an empty set cannot contain any fleetId, so the check
    fails closed. Same lesson as CVX's F31.5 blank-membership guard, applied
    to the CMS pool's shape.
    """
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "fleet-operator", "custom:fleetIds": ""},
                   qs={"fleetId": "flt-meridian-range-001"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# 6. pm/complete authz — fleet lookup + membership
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_pm_complete_denies_when_caller_is_not_in_the_schedules_fleet(monkeypatch):
    """The scheduleId identifies a row that carries its own fleetId — the
    check looks up that row and gates on `fleet_id ∈ custom:fleetIds`."""
    from services.fleet_intelligence import index

    event = _event(
        "POST",
        "/api/v1/fleet-intelligence/pm/schedules/{id}/complete",
        claims=_OPERATOR_OTHER_CLAIMS,  # holds "some-other-fleet"
        body={"vehicleId": "VEH-1", "completedDate": "2026-09-25"},
        path_params={"id": "sched-1"},
    )
    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {"vehicleId": "VEH-1", "scheduleId": "sched-1",
                 "fleetId": "flt-meridian-range-001"}
    }
    with patch.object(index, "_table", return_value=fake_table):
        resp = index.handler(event, None)
    assert resp["statusCode"] == 403


@pytest.mark.no_default_admin_claims
def test_pm_complete_returns_404_for_a_nonexistent_schedule(monkeypatch):
    """An admin caller hitting a missing scheduleId gets 404, not 403 or 500.

    404 leaks nothing new — GET /pm/schedules already lists what exists to
    an authorized caller — but 403 here would tell a caller "this exists,
    you just aren't allowed", so the distinction is deliberate.
    """
    from services.fleet_intelligence import index

    event = _event(
        "POST",
        "/api/v1/fleet-intelligence/pm/schedules/{id}/complete",
        claims=_ADMIN_CLAIMS,
        body={"vehicleId": "VEH-DOES-NOT-EXIST", "completedDate": "2026-09-25"},
        path_params={"id": "sched-does-not-exist"},
    )
    fake_table = MagicMock()
    fake_table.get_item.return_value = {}
    with patch.object(index, "_table", return_value=fake_table):
        resp = index.handler(event, None)
    assert resp["statusCode"] == 404


@pytest.mark.no_default_admin_claims
def test_pm_complete_authorized_flow_reaches_handler_dispatch(monkeypatch):
    """A fleet-operator with membership completes their own schedule — 200.

    Uses the real DDB update_item stub so a 200 confirms the handler was
    reached; a 403 short-circuited it before the update.
    """
    from services.fleet_intelligence import index

    event = _event(
        "POST",
        "/api/v1/fleet-intelligence/pm/schedules/{id}/complete",
        claims=_OPERATOR_MERIDIAN_CLAIMS,
        body={"vehicleId": "VEH-1", "completedDate": "2026-09-25"},
        path_params={"id": "sched-1"},
    )
    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {"vehicleId": "VEH-1", "scheduleId": "sched-1",
                 "fleetId": "flt-meridian-range-001"}
    }
    fake_table.update_item.return_value = {}
    with patch.object(index, "_table", return_value=fake_table):
        resp = index.handler(event, None)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


# ---------------------------------------------------------------------------
# 7. EventBridge scheduled refresh must NOT be blocked by fleet authz
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_scheduled_refresh_is_not_gated_by_fleet_authz(monkeypatch):
    """The EventBridge `refresh-lifecycle-cache` task carries no `requestContext`
    and is short-circuited before the authz check — it MUST NOT return 403.
    """
    from services.fleet_intelligence import adp_source, index, lifecycle_cache

    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")
    with patch.object(adp_source, "fetch_cost_rows", return_value=[]), \
         patch.object(adp_source, "fetch_tire_health", return_value=[]), \
         patch.object(lifecycle_cache, "store"):
        resp = index.handler({"fleetIntelligenceTask": "refresh-lifecycle-cache"}, None)

    # The refresh path returns a plain dict, not an HTTP-shaped 403.
    assert "statusCode" not in resp or resp.get("statusCode") != 403
    assert resp.get("status") == "refreshed"


# ---------------------------------------------------------------------------
# 8. Body / query-string parsing quirks — the shape of claims that Cognito
#    actually emits through the API Gateway authorizer.
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_cognito_groups_as_bracketed_string_is_parsed(monkeypatch):
    """API Gateway sometimes emits `cognito:groups` as `"[a, b]"` (Python-repr)
    — the OEM1 admin handlers already handle this, and so must FI."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "[fleet-operator, dispatcher]",
                           "custom:fleetIds": "flt-meridian-range-001"},
                   qs={"fleetId": "flt-meridian-range-001"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


@pytest.mark.no_default_admin_claims
def test_cognito_groups_as_actual_list_is_parsed(monkeypatch):
    """Access-token authorizer emits it as an actual JSON list — same result."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": ["platform-admin"]},
                   qs={"fleetId": "any-fleet"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200


@pytest.mark.no_default_admin_claims
def test_fleet_ids_with_surrounding_whitespace_are_trimmed(monkeypatch):
    """The live CSV shape sometimes carries stray whitespace after the
    comma. Members must be stripped so a real member is not silently
    denied by a whitespace mismatch (compare fleet-membership.parse_fleet_ids)."""
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "fleet-operator",
                           "custom:fleetIds": " flt-meridian-range-001 , FLEET-DEMO-PUBLIC "},
                   qs={"fleetId": "FLEET-DEMO-PUBLIC"})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# 9. POST /pm/schedules body-scoped authz
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_pm_schedule_create_scope_gates_on_body_fleet_id(monkeypatch):
    """POST /pm/schedules takes fleetId in the BODY, not the query string —
    the authz check must read the body form."""
    body = {
        "vehicleId": "VEH-1",
        "fleetId": "flt-not-in-caller-fleet-ids",
        "basis": "mileage",
        "intervalValue": 5000,
        "taskCode": "OIL",
        "lastPerformedMileage": 12000,
        "currentOdometer": 14500,
    }
    event = _event("POST", "/api/v1/fleet-intelligence/pm/schedules",
                   claims=_OPERATOR_MERIDIAN_CLAIMS, body=body)
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 403, (
        f"POST /pm/schedules must gate on body.fleetId; got {resp['statusCode']} "
        f"{resp['body']!r}"
    )


@pytest.mark.no_default_admin_claims
def test_pm_schedule_create_allowed_when_body_fleet_id_matches(monkeypatch):
    """Positive counterpart — a fleet-operator creating a schedule in one of
    their own fleets reaches the handler and gets 200."""
    body = {
        "vehicleId": "VEH-1",
        "fleetId": "flt-meridian-range-001",
        "basis": "mileage",
        "intervalValue": 5000,
        "taskCode": "OIL",
        "lastPerformedMileage": 12000,
        "currentOdometer": 14500,
    }
    event = _event("POST", "/api/v1/fleet-intelligence/pm/schedules",
                   claims=_OPERATOR_MERIDIAN_CLAIMS, body=body)
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']!r}"


# ---------------------------------------------------------------------------
# 10. Response body does not echo claim data
# ---------------------------------------------------------------------------
@pytest.mark.no_default_admin_claims
def test_403_body_does_not_echo_claims_or_requested_scope(monkeypatch):
    """The 403 must NOT contain the caller's group, the caller's own fleetIds,
    or the requested fleetId — echoing any of them assists a probing caller.
    """
    caller_fleet = "caller-fleet-abc-xyz"
    requested = "target-fleet-def-uvw"
    event = _event("GET", "/api/v1/fleet-intelligence/cpm",
                   claims={"cognito:groups": "fleet-operator",
                           "custom:fleetIds": caller_fleet},
                   qs={"fleetId": requested})
    resp = _invoke(event, monkeypatch=monkeypatch)
    assert resp["statusCode"] == 403
    assert caller_fleet not in resp["body"]
    assert requested not in resp["body"]
    assert "fleet-operator" not in resp["body"]

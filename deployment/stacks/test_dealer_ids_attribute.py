"""Guard: the Cognito pool schema must carry `custom:dealerIds`, mutable.

WHY THIS EXISTS
---------------
The DMS accelerator (`guidance-for-dealer-management-system-on-aws`, spec
`2026-08-26-dms-accelerator-v1`, OQ2 resolved 2026-08-28 to per-dealer grants) reads
`custom:dealerIds` from this pool to decide which dealers an API caller may access. Its
enforcement FAILS CLOSED: a caller with no grants is denied every dealer. So if this
attribute is missing from the pool schema, DMS's dealer endpoints return 403 for every
dealer-scoped user, with no error anywhere pointing at the cause.

Cognito will not let an existing pool lose a custom attribute, so the risk this guards is
not deletion from a live pool — it is a **fresh pool** (clean deploy, new region, new
account) synthesising without the attribute, and DMS then failing closed everywhere for a
reason that looks like an authorization bug rather than a schema gap.

Two properties are asserted, and the second matters as much as the first:

* the attribute EXISTS;
* it is MUTABLE. Dealer grants change over a user's life — reassignment, a second rooftop,
  revocation. An immutable custom attribute can only be written when the user is created
  (AWS: "You can only write a value to an immutable attribute when you create a user"), so
  flipping this to immutable would make regranting impossible without deleting the account.
  `custom:fleetIds` is mutable for the same reason; `tenantId` / `driverId` /
  `provisionedVia` are immutable because they record origin, not entitlement.

Cross-repo note: this file guards the PRODUCER side. The consumer side is guarded in the DMS
repo by `tests/handlers/test_dealer_scope.py`, which asserts every handler reading a
`dealerId` path parameter also calls `authorize_dealer_scope`. Neither test alone is
sufficient — this one can pass while nothing reads the attribute, and that one can pass while
the attribute does not exist.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from aws_cdk.assertions import Template


def _declared_custom_attributes() -> dict[str, bool]:
    """Parse `ui_stack.py`'s `custom_attributes` dict → {name: mutable}.

    Derived from the source rather than restated here, deliberately. The first revision of
    this file hardcoded a copy of the attribute list, which meant two of its three tests
    could not detect a change in `ui_stack.py` at all — flipping `dealerIds` to immutable
    there left them green. A guard that measures a copy of the thing is decoration.

    AST rather than regex: `custom_attributes` appears in prose in this repo's comments, and
    a textual match cannot distinguish a declaration from a mention of one.
    """
    import ast
    import pathlib

    src = (pathlib.Path(__file__).parent / "ui_stack.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "custom_attributes" or not isinstance(kw.value, ast.Dict):
                continue
            attrs: dict[str, bool] = {}
            for key, value in zip(kw.value.keys, kw.value.values):
                if not isinstance(key, ast.Constant):
                    continue
                mutable = None
                if isinstance(value, ast.Call):
                    for vkw in value.keywords:
                        if vkw.arg == "mutable" and isinstance(vkw.value, ast.Constant):
                            mutable = vkw.value.value
                attrs[key.value] = bool(mutable)
            if attrs:
                return attrs

    raise AssertionError(
        "could not locate a custom_attributes dict in ui_stack.py — the pool's attribute "
        "declaration moved or changed shape, and this guard is now blind. Fix the parser "
        "rather than deleting the test."
    )


def _synth_pool_schema() -> list[dict]:
    """Synthesise a probe pool from `ui_stack.py`'s OWN attribute declaration.

    Deliberately does not instantiate the whole UIStack: that requires the custom-domain
    context flags, the driver-self guard, MSK wiring and a resolved Federate secret, none of
    which this property depends on. Only the attribute contract is under test — but the
    contract is read from the real source, not restated.
    """
    app = cdk.App()
    stack = cdk.Stack(app, "SchemaProbe")
    cognito.UserPool(
        stack,
        "Probe",
        custom_attributes={
            name: cognito.StringAttribute(mutable=mutable)
            for name, mutable in _declared_custom_attributes().items()
        },
    )
    template = Template.from_stack(stack)
    pools = template.find_resources("AWS::Cognito::UserPool")
    assert len(pools) == 1, f"expected one probe pool, got {len(pools)}"
    return list(pools.values())[0]["Properties"]["Schema"]


def test_ui_stack_declares_dealer_ids_attribute() -> None:
    """`ui_stack.py` must declare `dealerIds` in the pool's custom_attributes."""
    import pathlib

    src = (pathlib.Path(__file__).parent / "ui_stack.py").read_text(encoding="utf-8")
    assert '"dealerIds": cognito.StringAttribute(' in src, (
        "deployment/stacks/ui_stack.py no longer declares the `dealerIds` custom attribute. "
        "DMS per-dealer grants read it and fail CLOSED without it — every dealer-scoped "
        "caller would be denied every dealer. See the module docstring."
    )
    assert '"dealerIds": cognito.StringAttribute(mutable=True)' in src, (
        "`dealerIds` must be mutable=True. Immutable custom attributes can only be written "
        "at user creation, which would make regranting a dealer impossible without deleting "
        "the account."
    )


def test_synthesised_schema_carries_mutable_dealer_ids() -> None:
    """The synthesised CloudFormation Schema must contain dealerIds with Mutable: true."""
    schema = _synth_pool_schema()
    by_name = {entry.get("Name"): entry for entry in schema}

    assert "dealerIds" in by_name, (
        f"synthesised pool Schema has no `dealerIds` attribute; present: {sorted(by_name)}"
    )
    assert by_name["dealerIds"].get("Mutable") is True, (
        f"`dealerIds` must synthesise with Mutable: true, got "
        f"{by_name['dealerIds'].get('Mutable')!r}"
    )
    assert by_name["dealerIds"].get("AttributeDataType") == "String", (
        "`dealerIds` carries a delimiter-separated list of dealer IDs and must be a String"
    )


def test_ui_stack_declares_district_ids_attribute() -> None:
    """`districtIds` guards the /dms/districts/* reads and fails closed without the claim."""
    import pathlib

    src = (pathlib.Path(__file__).parent / "ui_stack.py").read_text(encoding="utf-8")
    assert '"districtIds": cognito.StringAttribute(mutable=True)' in src, (
        "deployment/stacks/ui_stack.py must declare `districtIds` as mutable=True. DMS "
        "per-district grants read it and fail CLOSED without it, so every district "
        "manager would be denied every district."
    )


def test_grant_attributes_are_mutable_and_origin_attributes_are_not() -> None:
    """Pin the mutable/immutable split so the two categories do not get conflated.

    Entitlements (`fleetIds`, `dealerIds`, `role`) change over a user's life and must be
    mutable. Origin facts (`tenantId`, `driverId`, `provisionedVia`) are fixed at creation
    and must not be. A future contributor copying the line above `dealerIds` would have made
    it immutable — that is exactly the mistake this asserts against.
    """
    by_name = {entry.get("Name"): entry for entry in _synth_pool_schema()}

    for grant in ("fleetIds", "dealerIds", "districtIds", "role"):
        assert by_name[grant]["Mutable"] is True, f"{grant} is an entitlement — must be mutable"
    for origin in ("tenantId", "driverId", "provisionedVia"):
        assert by_name[origin]["Mutable"] is False, (
            f"{origin} records origin, not entitlement — must be immutable"
        )

#!/usr/bin/env python3
"""Restore Federate sign-in on a Cognito app client that a deploy reset.

INCIDENT 2026-08-11. The `cms-prod-ui` deploy at 13:50:12Z updated the app client (it had
to: the `write_attributes` restriction changed) and CloudFormation enforced template state
over two settings that had only ever been configured OUT OF BAND:

  SupportedIdentityProviders  ["COGNITO", "AmazonFederate"]  ->  ["COGNITO"]
  CallbackURLs                <the real prod callback>       ->  ["https://example.com"]

`https://example.com` is not a clobbered value someone typed — it is the aws-cdk-lib
`UserPoolClient` default when `o_auth` is left unset, and `ui_stack.py` does not set it.
So the template has always described a client with no Federate and a placeholder callback;
the only reason prod worked is that nobody had deployed a client change since the manual
configuration. The `AmazonFederate` provider itself was created 2026-04-27 and is
untouched — its `client_id` and issuer are intact — so this is recoverable without any
Federate credential.

Effect while broken: every one of the 9 prod platform-admins gets Cognito's
"An error was encountered with the requested page" on the Federate button, because the
provider is not permitted for the client AND the `redirect_uri` the SPA sends
(`<origin>/auth/callback`, SimpleAuthProvider.tsx:386) is not in CallbackURLs.

WHY THIS IS A SCRIPT AND NOT A CLI ONE-LINER
--------------------------------------------
`update-user-pool-client` is a REPLACE, not a merge: every parameter you omit reverts to
its default. Hand-typing flags to fix two fields is how a third field gets destroyed — the
same shape as the incident itself. This reads the live config, changes exactly the fields
named on the command line, and writes the whole thing back, printing a diff first.

THIS IS A STOPGAP. It creates drift: the next `cms-prod-ui` deploy will reset these fields
again, because the template still says `["COGNITO"]` and `["https://example.com"]`. The
durable fix is to put both into `ui_stack.py` (see the issue's Prevention section). Do not
treat a green run here as closure.

Usage:
  # show the diff, change nothing (default)
  python3 restore_federate_client_config.py --stage prod

  # apply
  python3 restore_federate_client_config.py --stage prod --apply --confirm-prod
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(os.path.dirname(_HERE), "config")

# Stage facts are READ FROM config/<stage>.env, never hardcoded here.
#
# Not style: this file SHIPS in the public template, and the pool id and the custom domain
# are both publish-scanner criticals (`cognito_user_pool_id`, `internal_awsi`). An earlier
# revision of this script hardcoded both and tripped the scanner — the fix is to remove the
# values, never to add a scan exclusion, because a file that is scan-excluded but not
# publish-excluded ships UNEXAMINED. Same precedent as
# `deprivilege_demo_personas.py`, which loads PROD_FLEETMANAGER_SUB_ID at runtime.
_STAGE_KEYS = {
    # stage: (pool-id key, custom-domain key)
    "prod": ("PROD_USER_POOL_ID", "UI_CUSTOM_DOMAIN"),
    "staging": ("STAGING_USER_POOL_ID", "UI_CUSTOM_DOMAIN"),
}


def _load_stage_config(stage: str) -> dict:
    """Read the publish-excluded stage env file and derive this script's inputs."""
    path = os.path.join(_CONFIG_DIR, f"{stage}.env")
    if not os.path.isfile(path):
        raise SystemExit(f"missing {path} — this script reads stage facts from it")
    values: dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()

    pool_key, domain_key = _STAGE_KEYS[stage]
    pool_id = values.get(pool_key, "")
    domain = values.get(domain_key, "")
    region = values.get("AWS_REGION", "")
    if not (pool_id and domain and region):
        raise SystemExit(
            f"{path} must define {pool_key}, {domain_key} and AWS_REGION; got "
            f"{pool_key}={'set' if pool_id else 'MISSING'}, "
            f"{domain_key}={'set' if domain else 'MISSING'}, "
            f"AWS_REGION={'set' if region else 'MISSING'}"
        )
    return {
        "region": region,
        "pool_id": pool_id,
        # SimpleAuthProvider.tsx:386 sends `${window.location.origin}/auth/callback`.
        "callbacks": [f"https://{domain}/auth/callback"],
        "idps": ["COGNITO"] + [
            p.strip()
            for p in values.get("CLIENT_EXTRA_IDPS", "").split(",")
            if p.strip() and p.strip().lower() not in ("cognito", "cognito-only")
        ],
    }


def _diff(before: dict, after: dict, fields: tuple[str, ...]) -> list[str]:
    """Report only real differences.

    Compares these fields as SETS. `SupportedIdentityProviders` and `CallbackURLs` are
    order-insensitive to Cognito, and an order-only difference reported as a change is
    worse than no diff at all: it trains the operator to click through a tool that cries
    wolf, on the one screen where they most need to read carefully.
    """
    out = []
    for f in fields:
        b, a = before.get(f), after.get(f)
        same = set(b or []) == set(a or []) if isinstance(b, list) or isinstance(a, list) else b == a
        if not same:
            out.append(f"  {f}:\n    before: {json.dumps(b)}\n    after:  {json.dumps(a)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", required=True, choices=sorted(_STAGE_KEYS))
    ap.add_argument("--apply", action="store_true", help="write the change (default: diff only)")
    ap.add_argument(
        "--confirm-prod",
        action="store_true",
        help="required with --apply when --stage prod; mutates live prod auth config",
    )
    args = ap.parse_args()

    cfg = _load_stage_config(args.stage)
    if args.apply and args.stage == "prod" and not args.confirm_prod:
        print(
            f"REFUSING: --stage prod --apply requires --confirm-prod.\n"
            f"This mutates the live app client on pool {cfg['pool_id']}, which all 9 "
            f"platform-admins authenticate through.",
            file=sys.stderr,
        )
        return 2

    idp = boto3.client("cognito-idp", region_name=cfg["region"])

    clients = idp.list_user_pool_clients(UserPoolId=cfg["pool_id"], MaxResults=60)[
        "UserPoolClients"
    ]
    if len(clients) != 1:
        print(
            f"REFUSING: expected exactly one app client on {cfg['pool_id']}, found "
            f"{len(clients)}. Widen this script deliberately rather than guessing which "
            f"one to change.",
            file=sys.stderr,
        )
        return 2
    client_id = clients[0]["ClientId"]

    current = idp.describe_user_pool_client(
        UserPoolId=cfg["pool_id"], ClientId=client_id
    )["UserPoolClient"]

    # Verify the provider we are about to permit actually exists and is usable. Permitting
    # a provider the pool does not have swaps one Hosted UI error for another.
    providers = {
        p["ProviderName"]
        for p in idp.list_identity_providers(UserPoolId=cfg["pool_id"], MaxResults=60)[
            "Providers"
        ]
    }
    missing = [p for p in cfg["idps"] if p != "COGNITO" and p not in providers]
    if missing:
        print(
            f"REFUSING: {missing} not present on the pool (have: {sorted(providers)}). "
            f"The provider must be created before the client can permit it, and creating "
            f"it needs the IdP client_id/client_secret.",
            file=sys.stderr,
        )
        return 2

    # Read-modify-write. Start from the live config so no unnamed field is reset — the
    # exact failure this incident is.
    desired = dict(current)
    desired["SupportedIdentityProviders"] = cfg["idps"]
    desired["CallbackURLs"] = sorted(
        set(current.get("CallbackURLs", []) + cfg["callbacks"])
        # Drop the CDK placeholder: it is not a real redirect target and leaving it in an
        # allowlist of redirect URIs is pointless surface.
        - {"https://example.com"}
    )

    changed = _diff(current, desired, ("SupportedIdentityProviders", "CallbackURLs"))
    if not changed:
        print("No change needed — client already has the desired configuration.")
        return 0

    print(f"Client {client_id} on {cfg['pool_id']} ({cfg['region']}):")
    print("\n".join(changed))

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply --confirm-prod.")
        return 0

    # Strip read-only members that describe returns but update rejects.
    for key in ("ClientId", "ClientName", "UserPoolId", "CreationDate", "LastModifiedDate",
                "ClientSecret"):
        desired.pop(key, None)

    idp.update_user_pool_client(
        UserPoolId=cfg["pool_id"],
        ClientId=client_id,
        ClientName=current["ClientName"],
        **desired,
    )
    after = idp.describe_user_pool_client(
        UserPoolId=cfg["pool_id"], ClientId=client_id
    )["UserPoolClient"]
    print("\nAPPLIED. Verified from the service:")
    print(f"  SupportedIdentityProviders: {after['SupportedIdentityProviders']}")
    print(f"  CallbackURLs:               {after['CallbackURLs']}")
    print(f"  WriteAttributes:            {after.get('WriteAttributes')}")
    print(
        "\nNOW: sign in via Federate to confirm, and read the FIRST id token's "
        "cognito:groups claim while you are there (Phase A first-token check).\n"
        "REMEMBER: this is drift. The next cms-prod-ui deploy resets it until "
        "ui_stack.py carries these values."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

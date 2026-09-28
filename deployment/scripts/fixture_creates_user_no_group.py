#!/usr/bin/env python3
"""Synthetic test fixture: creates a Cognito user WITHOUT assigning a group.

Used by test_assert_no_groupless_account_path.py to prove the scanner
detects the violation — the fail-then-pass proof.

A scanner only ever run against clean input proves nothing.

DO NOT USE in production. This fixture intentionally violates the
groupless-account invariant to verify the scanner fires.

Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ Group 2
"""
from __future__ import annotations

import boto3


def provision_user(cognito, pool_id: str, email: str) -> None:
    """Create a user without assigning any Cognito group.

    This is the VIOLATION that the scanner must detect.
    No admin_add_user_to_group call follows admin_create_user.
    """
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=email,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
        MessageAction="SUPPRESS",
    )
    # No admin_add_user_to_group call here — intentional violation.
    # The scanner should flag this as: admin_create_user without
    # a paired admin_add_user_to_group.

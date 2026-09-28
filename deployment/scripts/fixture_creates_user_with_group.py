#!/usr/bin/env python3
"""Synthetic test fixture: creates a Cognito user AND assigns a group.

Used by test_assert_no_groupless_account_path.py to prove the scanner
passes compliant code — the fail-then-pass proof.

A scanner only ever run against clean input proves nothing.

This fixture is the CORRECT pattern: every admin_create_user call is
followed by admin_add_user_to_group within the same function.

Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ Group 2
"""
from __future__ import annotations

import boto3


def provision_user(cognito, pool_id: str, email: str, group: str) -> None:
    """Create a user and immediately assign it to the specified group.

    This is the CORRECT pattern that the scanner must pass.
    admin_add_user_to_group is called within the same function
    before the success return.
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
    # Group assignment within the same function — this satisfies the
    # scanner's requirement for a paired group call.
    cognito.admin_add_user_to_group(
        UserPoolId=pool_id,
        Username=email,
        GroupName=group,
    )

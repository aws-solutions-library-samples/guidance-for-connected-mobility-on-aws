"""`VSA_CONNECT_REGION` must be the Connect instance's region, not the stage's.

Amazon Connect is the one AWS resource in this app whose region does NOT follow
the deployment stage. The staging API Gateway, WebSocket and telemetry endpoints
are all us-west-2; the Connect instance serving escalation is us-east-1. The
`ParticipantToken` that `StartChatContact` mints is only valid against that
region's `participant.connect.<region>.amazonaws.com` endpoint.

Point it at the app's own region and `CreateParticipantConnection` returns

    403 {"message": "User is not authorized to access this resource with an
         explicit deny in an identity-based policy"}

…which reads like an IAM problem and is not one. The request is not even
SigV4-signed (`ConnectChatClient.createParticipantConnection` sends only
`X-Amz-Bearer`), so no amount of IAM work would fix it.

This has happened twice, both times by someone setting the value to match the
us-west-2 lines around it:
  * issues/2026-06-22-ios-chat-connect-region/
  * issues/2026-09-22-ios-connect-region-mismatch-403/

Hence a test rather than a comment — a comment was already there the second time.

Run:
    pytest deployment/scripts/test_ios_connect_region_matches_instance.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _REPO / "clients/ios/MeridianMotorsCompanion/Config"

#: The region of the Connect instance that serves escalation (alias
#: cms-vsa-demo-use1). Source of truth is the tenant-config
#: `channels.connect.instanceArn`, surfaced as CVX SSM
#: /cvx/{stage}/connect-instance-arn. Update this only when that instance moves.
EXPECTED_CONNECT_REGION = "us-east-1"

_KEY = "VSA_CONNECT_REGION"


def _xcconfigs() -> list[Path]:
    found = sorted(p for p in _CONFIG_DIR.glob("*.xcconfig") if "local" not in p.name)
    assert found, f"no xcconfig files under {_CONFIG_DIR}"
    return found


def _connect_region(path: Path) -> str | None:
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        m = re.match(rf"^{_KEY}\s*=\s*(\S+)", stripped)
        if m:
            return m.group(1)
    return None


def test_at_least_one_xcconfig_declares_the_key():
    """Anti-vacuity: if the key is renamed, every assertion below would pass."""
    declared = {p.name: _connect_region(p) for p in _xcconfigs()}
    assert any(v is not None for v in declared.values()), (
        f"No xcconfig declares {_KEY}. Either it was renamed — update this test — "
        f"or the chat client is falling back to its compile-time default. Found: {declared}"
    )


@pytest.mark.parametrize("path", _xcconfigs(), ids=lambda p: p.name)
def test_connect_region_is_the_instance_region_not_the_stage_region(path: Path):
    region = _connect_region(path)
    if region is None:
        pytest.skip(f"{path.name} does not set {_KEY}")
    assert region == EXPECTED_CONNECT_REGION, (
        f"{path.name} sets {_KEY}={region}, but the Connect instance serving "
        f"escalation is in {EXPECTED_CONNECT_REGION}.\n"
        f"A ParticipantToken is region-bound: CreateParticipantConnection against "
        f"the wrong region returns HTTP 403 'explicit deny in an identity-based "
        f"policy', which is misleading — the call is not SigV4-signed at all.\n"
        f"Connect's region does NOT follow the deployment stage. Do not set this "
        f"to match the other endpoints in the file."
    )


def test_every_xcconfig_agrees():
    """The same Connect instance serves every stage, so the value cannot differ.

    A per-stage value is the shape the defect took: Release was correct and
    Staging was not, so a reader comparing the two files saw a plausible
    'staging uses the staging region' pattern rather than a bug.
    """
    regions = {p.name: _connect_region(p) for p in _xcconfigs()}
    distinct = {r for r in regions.values() if r is not None}
    assert len(distinct) <= 1, (
        f"{_KEY} differs across xcconfigs: {regions}. All stages escalate to the "
        f"same Connect instance, so a per-stage region is always wrong."
    )

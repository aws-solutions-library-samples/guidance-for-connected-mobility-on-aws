#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``verify_manifest_sync.py`` — spec T2.2.

Spec: ``.kiro/specs/2026-09-20-transform-manifest-contract-guards/tasks.md`` T2.2.

These cover the parts that do not need AWS: the exit-code contract, the local-side guards,
and the bucket-name derivation. The S3 comparison itself is exercised by
``make verify-manifests`` and by the opt-in live test at the bottom — a stubbed S3 client
cannot fail the way S3 fails, which is the lesson from
``~/.kiro/steering/spec-workflow.md`` § Live-Verification Gate.

**The exit-code contract is the most load-bearing thing here.** Because this guard is
deliberately outside blocking CI, "cannot evaluate" (2) must never be confusable with
"drift found" (1) — otherwise the first person to wire it up sees red, assumes drift, and
either chases a phantom or switches it off.

Run from repo root::

    python3 -m pytest scripts/tests/test_verify_manifest_sync.py -v
    LIVE_MANIFEST_SYNC=1 python3 -m pytest scripts/tests/test_verify_manifest_sync.py -v
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "verify_manifest_sync.py"
_spec = importlib.util.spec_from_file_location("verify_manifest_sync", _SCRIPT_PATH)
assert _spec and _spec.loader
sync = importlib.util.module_from_spec(_spec)
sys.modules["verify_manifest_sync"] = sync
_spec.loader.exec_module(sync)


def test_exit_codes_are_distinct_and_documented():
    """The whole point of the design: 'skipped' and 'failed' are different outcomes."""
    assert sync.EXIT_OK == 0
    assert sync.EXIT_FINDING == 1
    assert sync.EXIT_CANNOT_EVALUATE == 2
    assert len({sync.EXIT_OK, sync.EXIT_FINDING, sync.EXIT_CANNOT_EVALUATE}) == 3
    assert "NOT a drift finding" in sync.__doc__
    assert "not wired into blocking ci" in sync.__doc__.lower()


def test_bucket_derivation_matches_the_flink_stack_format():
    """Cross-check ``resolve_bucket`` against the f-string in ``flink_stack.py:800``.

    The original test asserted
    ``resolve_bucket(...) == "cms-staging-transform-manifests-us-west-2-123456789012"``
    — a restatement of resolve_bucket's own f-string.  Neither flink_stack.py nor any
    other CDK source was consulted, so a divergence kept the test green while
    verify_manifest_sync.py evaluated against a bucket nobody deploys to.

    This rewrite reads ``deployment/stacks/flink_stack.py`` (AST), extracts the
    ``S3_MANIFEST_BUCKET`` f-string template, and asserts that resolve_bucket produces
    the string that template would produce for the same stage/region/account.

    Mutation 1 (resolve_bucket f-string changed, e.g. insert '-extra-' or move region):
      test FAILS — resolve_bucket result no longer matches the CDK template.
    Mutation 2 (flink_stack.py S3_MANIFEST_BUCKET template changed in a /tmp/ copy):
      test FAILS — the extracted template no longer matches resolve_bucket.
    """
    import ast as _ast

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    flink_path = REPO_ROOT / "deployment" / "stacks" / "flink_stack.py"
    assert flink_path.exists(), f"flink_stack.py not found at {flink_path}"

    source = flink_path.read_text(encoding="utf-8")
    tree = _ast.parse(source)

    # Locate the S3_MANIFEST_BUCKET value by walking all Dict nodes.
    template_unparsed: str | None = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, _ast.Constant) and k.value == "S3_MANIFEST_BUCKET":
                    template_unparsed = _ast.unparse(v)
                    break
        if template_unparsed is not None:
            break

    assert template_unparsed is not None, (
        "Could not find S3_MANIFEST_BUCKET assignment in flink_stack.py. "
        "The CDK source may have moved — update this test to point at the new location."
    )

    # The CDK template contains construct_id, self.region, and self.account — local vars
    # in the CDK stack context. We evaluate it by substituting concrete values that match
    # what the CDK would produce for a given stage:
    #   construct_id = "cms-{stage}-flink"  (standard naming convention)
    #   self.region  = region
    #   self.account = account

    stage = "staging"
    region = "us-west-2"
    account = "123456789012"

    construct_id = f"cms-{stage}-flink"

    class _self:
        pass

    _self_inst = _self()
    _self_inst.region = region
    _self_inst.account = account

    # Evaluate the f-string template from CDK source.  We bind the exact same local
    # variables the CDK expression references: construct_id, self.
    cdk_bucket = eval(  # noqa: S307 — evaluating a string literal from a committed source file in a test
        template_unparsed,
        {"__builtins__": {}},
        {"construct_id": construct_id, "self": _self_inst},
    )

    expected = sync.resolve_bucket(stage, region, account)
    assert cdk_bucket == expected, (
        f"resolve_bucket diverges from flink_stack.py S3_MANIFEST_BUCKET:\n"
        f"  CDK template produces: {cdk_bucket!r}\n"
        f"  resolve_bucket returns: {expected!r}\n"
        f"  CDK expression: {template_unparsed}"
    )


def test_missing_manifest_dir_is_a_finding_not_a_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "MANIFEST_DIR", tmp_path / "absent")
    code, lines = sync.verify("staging", "us-west-2")
    assert code == sync.EXIT_FINDING
    assert "manifest directory not found" in "\n".join(lines)


def test_empty_local_set_refuses_to_report_in_sync(tmp_path, monkeypatch):
    """An empty local set must not compare clean against anything.

    Without this, deleting every manifest locally would make the guard report success —
    the vacuous-pass failure mode the sibling parity guard's floors exist for.
    """
    empty = tmp_path / "manifests"
    empty.mkdir()
    monkeypatch.setattr(sync, "MANIFEST_DIR", empty)
    code, lines = sync.verify("staging", "us-west-2")
    assert code == sync.EXIT_FINDING
    blob = "\n".join(lines)
    assert "refusing to report 'in sync'" in blob


def test_readme_is_not_compared(tmp_path, monkeypatch):
    """`README.md` is synced to the same prefix but is documentation, not a contract.

    A prose edit pending publication is not a finding worth failing a guard on. Asserted so
    a future widening of MANIFEST_GLOB is a deliberate decision.
    """
    assert sync.MANIFEST_GLOB == "*-transform.json"
    d = tmp_path / "manifests"
    d.mkdir()
    (d / "README.md").write_text("docs", encoding="utf-8")
    monkeypatch.setattr(sync, "MANIFEST_DIR", d)
    code, lines = sync.verify("staging", "us-west-2")
    # README alone means the *-transform.json set is empty → finding, not a comparison.
    assert code == sync.EXIT_FINDING
    assert "no '*-transform.json' files" in "\n".join(lines)


def test_unresolvable_credentials_yields_cannot_evaluate(monkeypatch):
    """No creds must be exit 2, never exit 1.

    Simulated by making the STS call raise NoCredentialsError, which is what an
    unconfigured environment produces.
    """
    import boto3
    from botocore.exceptions import NoCredentialsError

    def _boom(*a, **k):
        raise NoCredentialsError()

    monkeypatch.setattr(boto3, "client", _boom)
    code, lines = sync.verify("staging", "us-west-2")
    assert code == sync.EXIT_CANNOT_EVALUATE
    assert "NOT a drift finding" in "\n".join(lines)


def test_bucket_override_skips_account_resolution(monkeypatch):
    """`--bucket` must not require STS — that is how the Makefile passes the CFN output."""
    import boto3

    calls = []
    real = boto3.client

    def _tracking(service, **k):
        calls.append(service)
        return real(service, **k)

    monkeypatch.setattr(boto3, "client", _tracking)
    sync.verify("staging", "us-west-2", bucket_override="cms-nope-does-not-exist-000")
    assert "sts" not in calls, "an explicit bucket must not trigger account resolution"


def test_empty_bucket_override_is_cannot_evaluate(monkeypatch):
    """An empty-string ``--bucket`` must be exit 2, not a silent fallback to STS derivation.

    The Makefile's ``BUCKET=$$(...)`` yields empty when the stack is absent or the
    ``ManifestsBucketName`` output is missing.  Without this guard a missing stack silently
    causes the script to evaluate against a bucket it derived itself — a second source of
    truth for the bucket name, which decisions.md explicitly ruled out.
    Mutation: revert to ``if bucket_override:`` → this test FAILS because "" falls
    through to STS and the function proceeds (or raises a credentials error, exit 2 for a
    different reason — but the empty-string message is absent, which is what the assertion
    catches).
    """
    import boto3
    from botocore.exceptions import NoCredentialsError

    def _boom(*a, **k):
        raise NoCredentialsError()

    monkeypatch.setattr(boto3, "client", _boom)

    code, lines = sync.verify("staging", "us-west-2", bucket_override="")
    assert code == sync.EXIT_CANNOT_EVALUATE
    blob = "\n".join(lines)
    assert "empty string" in blob
    assert "NOT a drift finding" in blob


def test_nonexistent_bucket_is_cannot_evaluate(monkeypatch):
    """A bad bucket is an evaluation problem, not drift — mutation M8."""
    code, lines = sync.verify(
        "staging", "us-west-2", bucket_override="cms-definitely-not-a-bucket-000000"
    )
    assert code == sync.EXIT_CANNOT_EVALUATE
    blob = "\n".join(lines)
    assert "cannot list" in blob
    assert "NOT a drift finding" in blob


# ---------------------------------------------------------------------------
# F1.4 — exit 2 for any pre-comparison exception (ValueError from bad region,
#          bare Exception escaping verify())
# ---------------------------------------------------------------------------

def test_boto3_client_valueerror_yields_cannot_evaluate(monkeypatch):
    """boto3.client("s3") raising ValueError → EXIT_CANNOT_EVALUATE, output 'NOT a drift finding'.

    Reproduced by: ``--region ""`` → ValueError: Invalid endpoint: … → Python exits 1.
    That is the exit code reserved for "digest differs" — the inversion the three-code
    design exists to prevent.
    Mutation: remove the ValueError catch from the boto3.client construction block →
    this test FAILS because ValueError escapes to main() and exits 1.
    """
    import boto3

    real = boto3.client

    call_count = [0]

    def _boom(service, **k):
        call_count[0] += 1
        if service == "s3":
            raise ValueError("bad region")
        return real(service, **k)

    monkeypatch.setattr(boto3, "client", _boom)
    code, lines = sync.verify("staging", "us-west-2", bucket_override="fake-bucket")
    assert code == sync.EXIT_CANNOT_EVALUATE
    blob = "\n".join(lines)
    assert "NOT a drift finding" in blob


def test_main_wrapper_catches_bare_exception(tmp_path, monkeypatch):
    """An unexpected Exception escaping verify() → main() returns EXIT_CANNOT_EVALUATE.

    Belt-and-suspenders: the specific ValueError fix covers the known case; the main()
    wrapper prevents the class.  Simulated by monkeypatching verify() itself.
    Mutation: remove the ``except Exception`` block from main() → this test FAILS because
    main() re-raises and Python exits 1 (default error exit).
    """

    def _surprise(*a, **k):
        raise RuntimeError("surprise from verify")

    monkeypatch.setattr(sync, "verify", _surprise)
    # Ensure MANIFEST_DIR exists so verify() is reached (the wrapper is in main(), not
    # verify()), but we've patched verify() anyway.
    monkeypatch.setattr(sync, "MANIFEST_DIR", tmp_path)  # doesn't matter — patched
    rc = sync.main(["--stage", "staging", "--region", "us-west-2", "--bucket", "fake"])
    assert rc == sync.EXIT_CANNOT_EVALUATE


# ---------------------------------------------------------------------------
# F1.2 — S3 stub coverage for the three drift-detection branches + paginator
# ---------------------------------------------------------------------------
# These tests use a monkeypatched boto3.client so no real S3 connection is
# made. The stub is intentionally lightweight — it covers the *local comparison
# logic* (digest mismatch, git-only, S3-only, combined, paginator) which is
# pure Python and does not depend on network behaviour.  A stub cannot fail the
# way S3 fails (pagination, IAM, encryption) — the live test at the bottom
# covers that class.


def _make_s3_stub(
    tmp_path: Path,
    *,
    remote_names: list[str],
    remote_bytes: dict[str, bytes] | None = None,
    paginate_split: int | None = None,
) -> MagicMock:
    """Return a MagicMock boto3.client("s3") whose paginator + get_object honour the args.

    Args:
        tmp_path:       unused, kept for signature clarity with callers.
        remote_names:   filenames to appear in the S3 listing.
        remote_bytes:   optional override for per-file body content; defaults to a
                        synthetic bytes block different from any local content.
        paginate_split: if given, split the remote_names list at this index across
                        two paginator pages.  None → single page.
    """
    if remote_bytes is None:
        remote_bytes = {}

    # Build paginator pages
    def _make_contents(names: list[str]) -> list[dict]:
        return [{"Key": f"manifests/{n}"} for n in names]

    if paginate_split is not None:
        first = remote_names[:paginate_split]
        second = remote_names[paginate_split:]
        pages = [
            {"Contents": _make_contents(first)},
            {"Contents": _make_contents(second)},
        ]
    else:
        pages = [{"Contents": _make_contents(remote_names)}]

    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter(pages)

    def _get_object(Bucket: str, Key: str) -> dict:
        name = Key.rsplit("/", 1)[-1]
        body = remote_bytes.get(name, b"s3-content-" + name.encode())
        return {"Body": io.BytesIO(body)}

    mock_s3 = MagicMock()
    mock_s3.get_paginator.return_value = mock_paginator
    mock_s3.get_object.side_effect = _get_object
    return mock_s3


def _local_dir(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """Create a local manifest directory under tmp_path and return its path."""
    d = tmp_path / "manifests"
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / name).write_bytes(content)
    return d


# F1.2-1: Digest mismatch → EXIT_FINDING, output carries both digest prefixes + DRIFT
def test_s3_stub_digest_mismatch_is_finding(tmp_path, monkeypatch):
    """A manifest present on both sides with different bytes → DRIFT finding.

    Pins that the output line names BOTH digest prefixes, both byte counts, and the
    word DRIFT so a later 'clean up the output' cannot silently drop one field.
    Mutation: force ``_sha256(local[name]) == _sha256(body)`` in a /tmp/ copy → this
    test FAILS because the mismatch branch is never reached.
    """
    local_bytes = b"git-version-of-manifest"
    s3_bytes = b"deployed-version-of-manifest"
    name = "meridian-ev-transform.json"

    d = _local_dir(tmp_path, {name: local_bytes})
    monkeypatch.setattr(sync, "MANIFEST_DIR", d)

    import boto3

    mock_s3 = _make_s3_stub(
        tmp_path,
        remote_names=[name],
        remote_bytes={name: s3_bytes},
    )

    real_boto3_client = boto3.client

    def _boto3_client(service, **k):
        if service == "s3":
            return mock_s3
        return real_boto3_client(service, **k)

    monkeypatch.setattr(boto3, "client", _boto3_client)

    code, lines = sync.verify("staging", "us-west-2", bucket_override="fake-bucket")
    assert code == sync.EXIT_FINDING
    blob = "\n".join(lines)
    # Both digest prefixes must appear
    git_digest_prefix = hashlib.sha256(local_bytes).hexdigest()[:16]
    s3_digest_prefix = hashlib.sha256(s3_bytes).hexdigest()[:16]
    assert git_digest_prefix in blob, "git digest prefix missing from output"
    assert s3_digest_prefix in blob, "s3 digest prefix missing from output"
    # Both byte counts must appear
    assert str(len(local_bytes)) in blob, "git byte count missing"
    assert str(len(s3_bytes)) in blob, "s3 byte count missing"
    # DRIFT marker must appear
    assert "DRIFT" in blob


# F1.2-2: Git-only → EXIT_FINDING, output names remedy line
def test_s3_stub_git_only_is_finding(tmp_path, monkeypatch):
    """A manifest present locally but not in S3 → EXIT_FINDING.

    Pins that the output contains 'tracked in git but NOT deployed' AND the
    'make sync-manifests' remedy line.
    Mutation: force ``only_local = set()`` → this test FAILS.
    """
    name = "meridian-ev-transform.json"
    d = _local_dir(tmp_path, {name: b"local-content"})
    monkeypatch.setattr(sync, "MANIFEST_DIR", d)

    import boto3

    # Remote listing is empty — manifest is git-only
    mock_s3 = _make_s3_stub(tmp_path, remote_names=[])
    real_boto3_client = boto3.client

    def _boto3_client(service, **k):
        if service == "s3":
            return mock_s3
        return real_boto3_client(service, **k)

    monkeypatch.setattr(boto3, "client", _boto3_client)

    code, lines = sync.verify("staging", "us-west-2", bucket_override="fake-bucket")
    assert code == sync.EXIT_FINDING
    blob = "\n".join(lines)
    assert "tracked in git but NOT deployed" in blob
    assert "make sync-manifests" in blob


# F1.2-3: S3-only → EXIT_FINDING, output names --delete warning
def test_s3_stub_s3_only_is_finding(tmp_path, monkeypatch):
    """A manifest present in S3 but not locally → EXIT_FINDING.

    Pins that the output contains 'deployed but NOT tracked in git' AND the
    '--delete' warning line.
    Mutation: force ``only_remote = set()`` → this test FAILS.
    """
    local_name = "oem1-transform.json"
    s3_only_name = "mystery-transform.json"
    d = _local_dir(tmp_path, {local_name: b"local-content"})
    monkeypatch.setattr(sync, "MANIFEST_DIR", d)

    import boto3

    # Remote has the mystery file too — it does NOT appear locally
    mock_s3 = _make_s3_stub(tmp_path, remote_names=[local_name, s3_only_name])
    real_boto3_client = boto3.client

    def _boto3_client(service, **k):
        if service == "s3":
            return mock_s3
        return real_boto3_client(service, **k)

    monkeypatch.setattr(boto3, "client", _boto3_client)

    code, lines = sync.verify("staging", "us-west-2", bucket_override="fake-bucket")
    assert code == sync.EXIT_FINDING
    blob = "\n".join(lines)
    assert "deployed but NOT tracked in git" in blob
    assert "--delete" in blob


# F1.2-4: Combined git-only + S3-only → EXIT_FINDING, both messages present
def test_s3_stub_combined_git_only_and_s3_only(tmp_path, monkeypatch):
    """Both a git-only and an S3-only manifest in one run → both messages, single EXIT_FINDING."""
    git_only = "git-only-transform.json"
    s3_only = "s3-only-transform.json"
    d = _local_dir(tmp_path, {git_only: b"git-content"})
    monkeypatch.setattr(sync, "MANIFEST_DIR", d)

    import boto3

    mock_s3 = _make_s3_stub(tmp_path, remote_names=[s3_only])
    real_boto3_client = boto3.client

    def _boto3_client(service, **k):
        if service == "s3":
            return mock_s3
        return real_boto3_client(service, **k)

    monkeypatch.setattr(boto3, "client", _boto3_client)

    code, lines = sync.verify("staging", "us-west-2", bucket_override="fake-bucket")
    assert code == sync.EXIT_FINDING
    blob = "\n".join(lines)
    assert "tracked in git but NOT deployed" in blob
    assert "deployed but NOT tracked in git" in blob


# F1.2-5: Paginator boundary — list split across two pages
def test_s3_stub_paginator_boundary(tmp_path, monkeypatch):
    """list_objects_v2 split across two pages; second page carries the tracked manifest.

    Confirms the paginator loop iterates all pages. A caller who forgets to paginate
    (e.g. single s3.list_objects_v2 call) would only see page 1 and miss the manifest,
    reporting git-only instead of in-sync.
    Mutation: replace ``paginator.paginate(...)`` with a single non-paginated call →
    the second-page manifest is invisible and this test FAILS.
    """
    name_page1 = "oem1-transform.json"
    name_page2 = "meridian-ev-transform.json"
    local_content = b"common-content"
    d = _local_dir(tmp_path, {name_page1: local_content, name_page2: local_content})
    monkeypatch.setattr(sync, "MANIFEST_DIR", d)

    import boto3

    # Split: name_page1 on page 1, name_page2 on page 2
    mock_s3 = _make_s3_stub(
        tmp_path,
        remote_names=[name_page1, name_page2],
        remote_bytes={name_page1: local_content, name_page2: local_content},
        paginate_split=1,
    )
    real_boto3_client = boto3.client

    def _boto3_client(service, **k):
        if service == "s3":
            return mock_s3
        return real_boto3_client(service, **k)

    monkeypatch.setattr(boto3, "client", _boto3_client)

    code, lines = sync.verify("staging", "us-west-2", bucket_override="fake-bucket")
    # Both manifests are in sync — result should be EXIT_OK
    assert code == sync.EXIT_OK, "\n".join(lines)
    blob = "\n".join(lines)
    assert "in sync" in blob


@pytest.mark.skipif(
    not os.environ.get("LIVE_MANIFEST_SYNC"),
    reason="opt-in: needs AWS credentials. Set LIVE_MANIFEST_SYNC=1.",
)
def test_live_staging_manifests_are_in_sync():
    """The real comparison. Opt-in because a stub cannot fail the way S3 fails."""
    code, lines = sync.verify("staging", "us-west-2")
    assert code == sync.EXIT_OK, "\n".join(lines)
    blob = "\n".join(lines)
    assert "meridian-ev-transform.json in sync" in blob
    assert "oem1-transform.json in sync" in blob

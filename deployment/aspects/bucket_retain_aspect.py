"""CDK Aspect that enforces explicit ``RemovalPolicy.RETAIN`` on
globally-namespaced S3 buckets.

A bucket is treated as globally-namespaced if its construct was given an
explicit ``bucket_name`` argument. Auto-generated names are CDK-token-derived
and unique per deploy, so accidental deletion-on-replacement of an
auto-generated bucket has no global-namespace orphan-name risk. Buckets with
explicit names live in S3's partition-global namespace; deleting one holds
the name for 24-72 hours before another account/stack can reclaim it. An
unintended ``removal_policy=RemovalPolicy.DESTROY`` on such a bucket therefore
orphans the name on every CFN replacement, which is a class of regression
the portfolio has been bitten by repeatedly during cross-region clean-deploy
work (see ``~/.kiro/steering/cross-region-namespace.md``).

Today the safety hinges on CDK's L2 ``aws_s3.Bucket`` default of
``DeletionPolicy: Retain``. CDK majors can change defaults; this aspect
locks the invariant by failing synth on any violation.

Pattern usage in ``deployment/app.py``::

    from aspects.bucket_retain_aspect import BucketRetainAspect
    Aspects.of(storage_stack).add(BucketRetainAspect())
    # ...repeat for each verified-clean stack.

Per-stack scope is currently used because two conditional stacks
(``telemetry_integration_stack``, ``predictive_agent_stack``) ship explicit
``RemovalPolicy.DESTROY`` on globally-named buckets. App-level wiring would
break clean-deploy synth when ``DEPLOY_TELEMETRY_INTEGRATION=true`` (the
default in ``config/clean-deploy.env`` and the Makefile). Once those two
latent defects are fixed in their own follow-up issues, this aspect can be
promoted to ``Aspects.of(app).add(...)`` for portfolio-wide coverage.

Issue reference: ``cms/issues/2026-06-08-cms-bucket-retain-aspect/``.
"""

from __future__ import annotations

from typing import Any

import jsii
from aws_cdk import CfnDeletionPolicy, IAspect, Stack
from aws_cdk import aws_s3 as s3
from constructs import IConstruct


@jsii.implements(IAspect)
class BucketRetainAspect:
    """Aspect that fails synth on any globally-namespaced bucket lacking RETAIN.

    Walks every L1 ``CfnBucket`` in the scope. If ``bucket_name`` resolves
    to a non-None value (i.e. user passed an explicit name to the L2 Bucket
    constructor — either a literal or an expression with CFN tokens like
    ``f"foo-{Aws.REGION}-bar"``), checks that the synthesized
    ``DeletionPolicy`` is ``Retain``. If not, raises ``RuntimeError``, which
    CDK surfaces as a synth failure with the offending construct path.

    Auto-generated names are skipped: when no ``bucket_name=`` is passed,
    CDK fills the CfnBucket with a ``Lazy`` token that resolves to ``None``
    at synth time (CFN omits the property and generates a logical-id-derived
    name at deploy time). Such buckets are not pre-allocated in the global
    namespace and have no orphan-name risk on accidental
    deletion-on-replacement.
    """

    def visit(self, node: IConstruct) -> None:  # noqa: D401 — IAspect contract
        # Walk L1 CfnBucket nodes directly. The L2 ``aws_s3.Bucket`` wraps a
        # ``CfnBucket`` as its default child; visiting the L1 lets us read
        # the actual synthesized ``BucketName`` and ``DeletionPolicy``.
        if not isinstance(node, s3.CfnBucket):
            return

        # ``bucket_name`` on a CfnBucket is always set to *something*: either
        # the user-supplied literal/token, or a CDK ``Lazy`` token that
        # resolves to None when CFN should auto-generate. Use ``Stack.resolve``
        # to drill through tokens to the synth-time value. Resolved-to-None =
        # auto-generated = NOT globally-namespaced for this aspect's purposes.
        resolved: Any = Stack.of(node).resolve(node.bucket_name)
        if resolved is None:
            return

        deletion_policy = node.cfn_options.deletion_policy
        if deletion_policy == CfnDeletionPolicy.RETAIN:
            return

        # Surface the offending construct path so the operator can find the
        # site without grepping the synth tree.
        raise RuntimeError(
            "BucketRetainAspect: globally-namespaced S3 bucket "
            f"'{node.node.path}' has DeletionPolicy={deletion_policy!r}, "
            "must be RETAIN. Add `removal_policy=RemovalPolicy.RETAIN` to "
            "the s3.Bucket(...) constructor (or remove the explicit "
            "`bucket_name=` if the bucket does not need a globally-stable "
            "name). See `~/.kiro/steering/cross-region-namespace.md` for "
            "the discipline rationale."
        )

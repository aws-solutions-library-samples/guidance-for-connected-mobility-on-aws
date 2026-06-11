"""CDK aspects shipped under deployment/.

Currently:

- ``BucketRetainAspect`` — fail-synth check that every globally-namespaced
  S3 bucket carries ``RemovalPolicy.RETAIN``. Codifies the discipline in
  ``~/.kiro/steering/cross-region-namespace.md`` (Bucket RETAIN aspect,
  P3 backlog row).
"""

from aspects.bucket_retain_aspect import BucketRetainAspect

__all__ = ["BucketRetainAspect"]

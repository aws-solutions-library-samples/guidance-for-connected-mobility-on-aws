"""CDK aspects + synth-time guards shipped under deployment/.

- ``BucketRetainAspect`` — fail-synth check that every globally-namespaced
  S3 bucket carries ``RemovalPolicy.RETAIN`` (cross-region-namespace.md).
- ``enforce_ui_domain_alias`` — synth-time guard that aborts if the UI
  CloudFront distribution would lose its required custom-domain alias
  (e.g. ``staging.fleet.example.com``) in its home region.
  Wired for staging in ``app.py``; reusable for prod as a follow-up.
"""

from aspects.bucket_retain_aspect import BucketRetainAspect
from aspects.domain_alias_guard import enforce_ui_domain_alias

__all__ = ["BucketRetainAspect", "enforce_ui_domain_alias"]

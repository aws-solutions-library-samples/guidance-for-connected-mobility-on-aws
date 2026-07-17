#!/usr/bin/env python3
"""Unit tests for ``stacks._sim_image_config`` (simulation image resolver).

Verifies the published-image reference resolution + the anti-empty guard +
mode selection for the spec
``.kiro/specs/2026-06-16-cms-sim-images-codebuild-ecr/spec.md``.

Stdlib-only (``unittest``) — mirrors the project convention in
``test_bucket_retain_aspect.py`` / ``test_preflight_global_namespace.py``.
The resolver is pure (no CDK), so this runs without the venv.

Run from ``deployment/``::

    python3 scripts/test_sim_image_resolution.py
"""
from __future__ import annotations

import os
import sys
import unittest

# Make ``deployment/`` importable so ``stacks._sim_image_config`` resolves
# regardless of cwd. Mirrors test_bucket_retain_aspect.py.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEPLOYMENT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, DEPLOYMENT_DIR)

from stacks._sim_image_config import (  # noqa: E402
    DEFAULT_PUBLIC_ECR_REGISTRY,
    FWE_AGENT_IMAGE_NAME,
    SIM_IMAGE_MODE_ASSET,
    SIM_IMAGE_MODE_PUBLISHED,
    SIM_IMAGE_VERSION,
    SIM_SERVICE_IMAGE_NAME,
    get_sim_image_mode,
    resolve_sim_image_ref,
)


class ResolveSimImageRefTest(unittest.TestCase):
    def test_published_default_url_sim_service(self) -> None:
        ref = resolve_sim_image_ref(SIM_SERVICE_IMAGE_NAME, {})
        self.assertEqual(
            ref, f"{DEFAULT_PUBLIC_ECR_REGISTRY}/cms-sim-service:{SIM_IMAGE_VERSION}"
        )

    def test_published_default_url_fwe_agent(self) -> None:
        ref = resolve_sim_image_ref(FWE_AGENT_IMAGE_NAME, {})
        self.assertEqual(
            ref, f"{DEFAULT_PUBLIC_ECR_REGISTRY}/cms-fwe-agent:{SIM_IMAGE_VERSION}"
        )

    def test_env_override_registry_and_tag(self) -> None:
        env = {
            "PUBLIC_ECR_REGISTRY": "123456789012.dkr.ecr.us-west-2.amazonaws.com/myrepo",
            "PUBLIC_ECR_TAG": "v9.9.9-feature",
        }
        ref = resolve_sim_image_ref(SIM_SERVICE_IMAGE_NAME, env)
        self.assertEqual(
            ref,
            "123456789012.dkr.ecr.us-west-2.amazonaws.com/myrepo/cms-sim-service:v9.9.9-feature",
        )

    def test_trailing_slash_on_registry_normalized(self) -> None:
        env = {"PUBLIC_ECR_REGISTRY": "public.ecr.aws/example/"}
        ref = resolve_sim_image_ref(SIM_SERVICE_IMAGE_NAME, env)
        self.assertEqual(
            ref, f"public.ecr.aws/example/cms-sim-service:{SIM_IMAGE_VERSION}"
        )

    def test_empty_registry_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_sim_image_ref(SIM_SERVICE_IMAGE_NAME, {"PUBLIC_ECR_REGISTRY": ""})

    def test_whitespace_tag_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_sim_image_ref(SIM_SERVICE_IMAGE_NAME, {"PUBLIC_ECR_TAG": "   "})

    def test_empty_image_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_sim_image_ref("", {})


class GetSimImageModeTest(unittest.TestCase):
    def test_default_is_published(self) -> None:
        self.assertEqual(get_sim_image_mode({}), SIM_IMAGE_MODE_PUBLISHED)

    def test_asset_mode_honored(self) -> None:
        self.assertEqual(
            get_sim_image_mode({"SIM_IMAGE_MODE": "asset"}), SIM_IMAGE_MODE_ASSET
        )

    def test_invalid_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_sim_image_mode({"SIM_IMAGE_MODE": "bogus"})


if __name__ == "__main__":
    unittest.main()

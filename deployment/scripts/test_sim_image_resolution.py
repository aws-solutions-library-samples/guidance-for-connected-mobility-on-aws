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
    ENV_ALLOW_STALE,
    FWE_AGENT_IMAGE_NAME,
    SIM_IMAGE_MODE_ASSET,
    SIM_IMAGE_MODE_PUBLISHED,
    SIM_IMAGE_VERSION,
    SIM_SERVICE_IMAGE_NAME,
    assert_published_image_fresh,
    check_published_image_provenance,
    get_sim_image_mode,
    resolve_sim_image_ref,
)

# Patch target for module-level constants (Guard 2 tests).
_MOD = "stacks._sim_image_config"


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


# NOTE: the ``if __name__ == "__main__"`` block deliberately lives at the END of
# this file. It previously sat here, above TestPerImageModeOverride, so the
# documented ``python3 scripts/test_sim_image_resolution.py`` invocation ran only
# the first two classes and silently skipped every class appended later. pytest
# was unaffected (it imports rather than executes as __main__), which is why the
# gap went unnoticed. Keep new test classes above that block.


class TestPerImageModeOverride(unittest.TestCase):
    """``SIM_IMAGE_MODE_<IMAGE>`` overrides the global ``SIM_IMAGE_MODE``.

    Added 2026-08-04 for spec ``2026-08-04-cms-vehicle-trip-lifecycle-split``
    Group 5. The two simulation images have wildly different build costs —
    ``cms-sim-service`` is Python and builds in seconds, ``cms-fwe-agent``
    compiles aws-iot-fleetwise-edge plus the AWS SDK for C++ from source and
    takes tens of minutes. Without a per-image override the only choices are a
    long rebuild of an unchanged image, or an all-``published`` deploy that
    silently ships a *stale* sim-service while appearing to deploy new code.
    Both are wrong; this override is what makes a fast loop also a truthful one.
    """

    def test_per_image_override_wins_over_global(self):
        env = {
            "SIM_IMAGE_MODE": "asset",
            "SIM_IMAGE_MODE_CMS_FWE_AGENT": "published",
        }
        # The unchanged C++ image stays published...
        self.assertEqual(
            get_sim_image_mode(env, FWE_AGENT_IMAGE_NAME), "published",
            "per-image override must win over the global mode",
        )
        # ...while the image carrying new Python code builds from source.
        self.assertEqual(
            get_sim_image_mode(env, SIM_SERVICE_IMAGE_NAME), "asset",
            "images without an override must follow the global mode",
        )

    def test_global_applies_when_no_override_present(self):
        env = {"SIM_IMAGE_MODE": "asset"}
        for name in (FWE_AGENT_IMAGE_NAME, SIM_SERVICE_IMAGE_NAME):
            self.assertEqual(get_sim_image_mode(env, name), "asset")

    def test_default_still_published_with_image_name(self):
        self.assertEqual(get_sim_image_mode({}, FWE_AGENT_IMAGE_NAME), "published")

    def test_backward_compatible_without_image_name(self):
        """Existing single-argument callers keep working unchanged."""
        self.assertEqual(get_sim_image_mode({"SIM_IMAGE_MODE": "asset"}), "asset")
        self.assertEqual(get_sim_image_mode({}), "published")

    def test_image_name_dashes_map_to_underscores(self):
        # cms-sim-service -> SIM_IMAGE_MODE_CMS_SIM_SERVICE
        env = {"SIM_IMAGE_MODE_CMS_SIM_SERVICE": "asset"}
        self.assertEqual(get_sim_image_mode(env, SIM_SERVICE_IMAGE_NAME), "asset")
        self.assertEqual(get_sim_image_mode(env, FWE_AGENT_IMAGE_NAME), "published")

    def test_invalid_per_image_value_fails_loudly_and_names_the_var(self):
        env = {"SIM_IMAGE_MODE_CMS_FWE_AGENT": "publish"}  # typo
        with self.assertRaises(ValueError) as ctx:
            get_sim_image_mode(env, FWE_AGENT_IMAGE_NAME)
        msg = str(ctx.exception)
        self.assertIn("SIM_IMAGE_MODE_CMS_FWE_AGENT", msg,
                      "error must name the per-image var, not the global one, "
                      "or the operator edits the wrong setting")

    def test_invalid_global_still_names_the_global_var(self):
        with self.assertRaises(ValueError) as ctx:
            get_sim_image_mode({"SIM_IMAGE_MODE": "nope"}, FWE_AGENT_IMAGE_NAME)
        self.assertIn("SIM_IMAGE_MODE=", str(ctx.exception))

    def test_empty_per_image_value_is_rejected_not_silently_ignored(self):
        """An explicitly blank override must fail, not fall through to global.

        Same doctrine as ``resolve_sim_image_ref``'s anti-empty guard: a blank
        value is an operator mistake and must not silently select a build path.
        """
        env = {"SIM_IMAGE_MODE": "asset", "SIM_IMAGE_MODE_CMS_FWE_AGENT": "   "}
        with self.assertRaises(ValueError):
            get_sim_image_mode(env, FWE_AGENT_IMAGE_NAME)


class PublishedImageProvenanceTest(unittest.TestCase):
    """Guard 2 — issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/.

    The regression these pin: ``published`` mode deployed a pinned tag whose
    images predated the simulation source, so the sidecar ran code with no
    presence loop while the deploy reported success.
    """

    def _git(self, root, *args: str) -> str:
        import subprocess

        proc = subprocess.run(
            [
                "git",
                "-c", "user.email=test@example.com",
                "-c", "user.name=test",
                "-c", "commit.gpgsign=false",
                *args,
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip()

    def _repo_with_commit(self, tmp):
        """Init a throwaway repo containing one committed sim source file."""
        from pathlib import Path

        root = Path(tmp)
        sim = root / "services" / "simulation"
        sim.mkdir(parents=True)
        (sim / "realtime_telemetry_simulator.py").write_text("print('v1')\n")
        self._git(root, "init", "-q")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", "initial")
        return root, self._git(root, "rev-parse", "HEAD")

    def test_in_sync_reports_no_drift(self) -> None:
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self._repo_with_commit(tmp)
            with mock.patch(_MOD + ".SIM_IMAGE_SOURCE_COMMIT", sha):
                drifted, reason = check_published_image_provenance({}, repo_root=root)
        self.assertEqual(drifted, [])
        self.assertIsNone(reason)

    def test_drift_is_detected_and_fails_closed(self) -> None:
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self._repo_with_commit(tmp)
            # Simulate source moving ahead of the published image.
            (root / "services" / "simulation" / "realtime_telemetry_simulator.py").write_text(
                "print('v2 — presence loop added')\n"
            )
            with mock.patch(_MOD + ".SIM_IMAGE_SOURCE_COMMIT", sha):
                drifted, reason = check_published_image_provenance({}, repo_root=root)
                self.assertIsNone(reason)
                self.assertIn(
                    "services/simulation/realtime_telemetry_simulator.py", drifted
                )
                with self.assertRaises(ValueError) as ctx:
                    assert_published_image_fresh(
                        SIM_SERVICE_IMAGE_NAME, {}, repo_root=root
                    )
        msg = str(ctx.exception)
        # The message must be actionable: name both escapes.
        self.assertIn(f"SIM_IMAGE_MODE={SIM_IMAGE_MODE_ASSET}", msg)
        self.assertIn("publish-public-ecr", msg)
        self.assertIn("STALE", msg)

    def test_allow_stale_escape_permits_drift(self) -> None:
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self._repo_with_commit(tmp)
            (root / "services" / "simulation" / "realtime_telemetry_simulator.py").write_text(
                "print('v2')\n"
            )
            with mock.patch(_MOD + ".SIM_IMAGE_SOURCE_COMMIT", sha):
                # Must NOT raise.
                assert_published_image_fresh(
                    SIM_SERVICE_IMAGE_NAME,
                    {ENV_ALLOW_STALE: "1"},
                    repo_root=root,
                )

    def test_no_git_metadata_degrades_to_warning(self) -> None:
        """The fresh-customer path is a release archive with no .git.

        This MUST allow the synth — failing here would break the only consumer
        published mode exists to serve.
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            drifted, reason = check_published_image_provenance({}, repo_root=root)
            self.assertEqual(drifted, [])
            self.assertIsNotNone(reason)
            self.assertIn("no git metadata", reason)
            # And the assertion helper must not raise.
            assert_published_image_fresh(SIM_SERVICE_IMAGE_NAME, {}, repo_root=root)

    def test_unknown_commit_degrades_to_warning(self) -> None:
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root, _sha = self._repo_with_commit(tmp)
            with mock.patch(_MOD + ".SIM_IMAGE_SOURCE_COMMIT", "0" * 40):
                drifted, reason = check_published_image_provenance({}, repo_root=root)
                self.assertEqual(drifted, [])
                self.assertIsNotNone(reason)
                assert_published_image_fresh(
                    SIM_SERVICE_IMAGE_NAME, {}, repo_root=root
                )

    def test_explicit_tag_override_is_undeterminable(self) -> None:
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root, sha = self._repo_with_commit(tmp)
            with mock.patch(_MOD + ".SIM_IMAGE_SOURCE_COMMIT", sha):
                drifted, reason = check_published_image_provenance(
                    {"PUBLIC_ECR_TAG": "v9.9.9"}, repo_root=root
                )
        self.assertEqual(drifted, [])
        self.assertIn("overrides the pinned", reason)

    def test_unset_source_commit_degrades_to_warning(self) -> None:
        from unittest import mock

        with mock.patch(_MOD + ".SIM_IMAGE_SOURCE_COMMIT", "  "):
            drifted, reason = check_published_image_provenance({})
        self.assertEqual(drifted, [])
        self.assertIn("unset", reason)

    def test_real_repo_is_in_sync_after_republish(self) -> None:
        """Ground truth: this checkout matches the pinned image.

        Pre-republish this asserted the opposite (16 drifted files against
        v0.2.8's publish commit) and passed — which is how the bug was measured.
        Inverted 2026-08-19 when v0.2.9 was published from a918ce35. If this
        starts failing, simulation source has moved ahead of the pinned image
        again: republish and bump both constants, or deploy with
        SIM_IMAGE_MODE=asset.
        """
        drifted, reason = check_published_image_provenance({})
        if reason is not None:
            self.skipTest(f"provenance undeterminable here: {reason}")
        self.assertEqual(
            drifted,
            [],
            "simulation source has drifted ahead of the pinned published image",
        )


if __name__ == "__main__":
    unittest.main()

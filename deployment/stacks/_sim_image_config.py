"""Simulation container-image source configuration + reference resolver.

Single source of truth for how ``SimulationStack`` decides which container
images to run for the two simulation workloads (``sim-service`` and
``fwe-agent``). Pure stdlib — **no CDK imports** — so it is trivially unit
testable (``deployment/scripts/test_sim_image_resolution.py``) and importable
from both the stack (at synth) and any tooling.

Design: spec ``.kiro/specs/2026-06-16-cms-sim-images-codebuild-ecr/spec.md``,
verified patterns in ``docs/tech.md`` (ADDENDUM 2026-06-16 (2)).

Two modes (env ``SIM_IMAGE_MODE``):

* ``published`` (default) — reference a prebuilt image from the AWS Solutions
  public ECR registry via ``ecs.ContainerImage.from_registry(...)``. No local
  container builder required. This is the fresh-customer / release path.
* ``asset`` — build locally from ``services/simulation`` via
  ``ecs.ContainerImage.from_asset(..., platform=LINUX_ARM64)`` (Option A; needs
  a builder + ``CDK_DOCKER``). Dev inner-loop escape for editing the sim source.

The published registry + tag follow the AWS Solutions Engineering public-ECR
convention (env ``PUBLIC_ECR_REGISTRY`` / ``PUBLIC_ECR_TAG``), with safe
defaults so an unset environment still resolves to a concrete, non-empty
reference. An explicitly-empty value is a hard error (no synth-time-empty
foot-gun — mirrors the runtimeConfig-race discipline, commit ``1a96302``).
"""
from __future__ import annotations

from typing import Mapping

# --- Pinned defaults -------------------------------------------------------
# Tracks the solution release / public-mirror tag. Bump in lockstep with the
# image publish (``make publish-public-ecr``). CMS release at authoring: v0.2.6.
# Bumped to v0.2.8 on 2026-08-03: images pushed to public.ecr.aws/o0q5e8r2 and the
# publish_preflight gate (spec 2026-08-02-publish-flow-extract) now enforces that this
# constant equals the tag being published, so v0.2.7/v0.2.8 drift cannot recur silently.
# Bumped to v0.3.2 on 2026-08-19: republished so the pinned image carries the
# simulation_api.py argv fix (request-reachable code execution via python -c
# source interpolation) - issues/2026-08-19-cms-sim-api-subprocess-source-interpolation/.
# Superseded comment for v0.3.1 follows:
# Bumped to v0.3.1 on 2026-08-19: republished so the pinned image carries the
# presenceOwner fencing token (defect C). Prior: v0.3.0 carried the heartbeat.
# Superseded comment for v0.3.0 follows:
# Bumped to v0.3.0 on 2026-08-19: republished so the pinned image carries the
# PresenceLoop.heartbeat() lastSeenAt fix
# (issues/2026-08-19-cms-presence-lastseenat-stale-and-no-reaper/). The v0.2.9
# republish earlier the same day carried the presence loop itself.
# Bumped to v0.4.0 on 2026-09-25: republished so the pinned image carries the
# SOVD sidecar handlers (routine_sims.py, sovd_payload_sizing.py,
# uds_freeze_frame_fixtures.py) and the _shared/routine_catalog overlay that
# the sidecar imports for F2.3's fail-closed safety_class check. v0.3.2 was
# built from 4ca1b7ce (2026-08-19), which predates the SOVD sidecar and the
# _shared/ Dockerfile COPY that landed in c3f4a49d (2026-09-08). Also required
# an update to scripts/stage_ecr_resources.sh to overlay services/_shared/
# into ecr/cms-sim-service/, mirroring the synth-path staging in
# simulation_stack._stage_sim_service_context() — the asset and published
# paths had silently diverged since c3f4a49d.
SIM_IMAGE_VERSION: str = "v0.4.0"

# Git commit the images at ``SIM_IMAGE_VERSION`` were built from.
#
# Bump this in the SAME commit as ``SIM_IMAGE_VERSION`` on every
# ``make publish-public-ecr`` (Guard 1 in that target enforces the tag half;
# ``check_published_image_provenance`` below enforces this half).
#
# Why it exists: ``published`` is the DEFAULT mode, so a plain
# ``make deploy-simulation`` pulls this pinned tag. When simulation source moves
# ahead of the published image, the container silently runs old code — which is
# precisely what the ``get_sim_image_mode`` docstring below warned about on
# 2026-08-04 and what happened anyway on 2026-08-13: task-def rev 18 pinned
# v0.2.8 (published 08-03) while ``PresenceLoop`` had landed in source on 08-05,
# so the vehicle-ecu sidecar ran an image with no presence loop at all, exited 0
# after 3.6s, and was never restarted. A warning in a docstring did not prevent
# it; a synth-time gate does. See
# ``issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/``.
#
# Value below is ``6d67193c`` — HEAD when cms-sim-service:v0.4.0 was rebuilt and
# re-pushed (2026-09-26, digest sha256:47215756…), with services/simulation and
# services/_shared/ clean against it. The rebuild adds the healthy-trip fix
# (48544764: random faults off by default, so healthy trips raise no
# P0299/P0562/P0001). cms-fwe-agent:v0.4.0 is unchanged (Dockerfile.fwe copies
# nothing from services/simulation).
# Superseded comment for the first v0.4.0 push (2026-09-25) follows:
# Value was ``c3e5a555`` — HEAD at the v0.4.0 publish (2026-09-25), with
# services/simulation and services/_shared/ clean against it. v0.4.0 therefore
# carries the SOVD sidecar (routine_sims.py, sovd_payload_sizing.py,
# uds_freeze_frame_fixtures.py from c3f4a49d + 89ac5f0c), the _shared/ overlay
# for `_shared.routine_catalog`, and every sim fix through the diagnostics-IA
# pre-deploy fixes at c3e5a555.
# Superseded comment for v0.3.0 follows:
# Value below is ``c4232950`` — HEAD at the v0.3.0 publish (2026-08-19), with
# services/simulation clean against it. v0.3.0 therefore carries PresenceLoop
# (03d012b4), every sim fix through a918ce35, and the lastSeenAt heartbeat
# (6492e31d).
SIM_IMAGE_SOURCE_COMMIT: str = "6d67193c3a448f776e3dbb8beab86f4c3a0105ac"

# Repo-relative paths whose contents end up inside the published sim images.
# Both images build from ``services/simulation`` (Dockerfile / Dockerfile.fwe);
# ``deployment/ecr/`` is deliberately NOT listed — it is a gitignored staging
# area produced by ``stage_ecr_resources.sh``, not a source of truth.
SIM_SOURCE_PATHS: tuple[str, ...] = ("services/simulation",)

# Default public registry that published images are pulled from when
# PUBLIC_ECR_REGISTRY is unset. TEMPORARY self-hosted namespace so fresh deploys
# pull with zero config; replace with a sanctioned / custom-alias registry once
# one is provisioned (one-line change here + the Makefile publish default).
DEFAULT_PUBLIC_ECR_REGISTRY: str = "public.ecr.aws/o0q5e8r2"

# Public ECR repository names — dir names under deployment/ecr/ MUST match
# these. Prefixed ``cms-`` to avoid collisions in the shared public namespace.
SIM_SERVICE_IMAGE_NAME: str = "cms-sim-service"
FWE_AGENT_IMAGE_NAME: str = "cms-fwe-agent"

# --- Modes -----------------------------------------------------------------
SIM_IMAGE_MODE_PUBLISHED: str = "published"
SIM_IMAGE_MODE_ASSET: str = "asset"
_VALID_MODES = (SIM_IMAGE_MODE_PUBLISHED, SIM_IMAGE_MODE_ASSET)

# Env var names (AWS Solutions public-ECR convention)
ENV_MODE = "SIM_IMAGE_MODE"
ENV_REGISTRY = "PUBLIC_ECR_REGISTRY"
ENV_TAG = "PUBLIC_ECR_TAG"
# Explicit, auditable escape from the provenance gate below. Same philosophy as
# PUBLISH_SKIP_SIM_IMAGE_PREFLIGHT in scripts/lib/publish-config.sh: a gate with
# no documented override is a gate somebody deletes the first time it is
# inconvenient.
ENV_ALLOW_STALE = "SIM_IMAGE_ALLOW_STALE"


def get_sim_image_mode(env: Mapping[str, str], image_name: str | None = None) -> str:
    """Return the validated simulation image mode from the environment.

    Defaults to ``published`` when unset. Raises ``ValueError`` on an
    unrecognized value so a typo fails loudly rather than silently selecting
    a build path.

    ``image_name`` enables a **per-image override** via
    ``SIM_IMAGE_MODE_<IMAGE>`` (e.g. ``SIM_IMAGE_MODE_CMS_FWE_AGENT=published``
    alongside a global ``SIM_IMAGE_MODE=asset``). The per-image value wins when
    set; otherwise the global applies.

    Why this exists: the two simulation images have very different build costs.
    ``cms-sim-service`` is a Python image that builds in seconds, while
    ``cms-fwe-agent`` compiles aws-iot-fleetwise-edge (and the AWS SDK for C++)
    from source and takes tens of minutes. Iterating on Python behaviour should
    not require rebuilding an unchanged C++ image, and — worse — the reverse
    trap is silent: deploying wholly in ``published`` mode to avoid that
    rebuild ships a *stale* sim-service image, so new simulator code appears
    deployed while the container runs the last published build. This override
    lets each image pick the correct source independently, which is the only
    way to get a fast loop AND a truthful one.

    Added 2026-08-04 for spec ``2026-08-04-cms-vehicle-trip-lifecycle-split``
    Group 5, where verifying the new presence loop required an asset
    ``cms-sim-service`` while ``cms-fwe-agent`` was unchanged.
    """
    per_image = None
    if image_name and image_name.strip():
        suffix = image_name.strip().upper().replace("-", "_")
        per_image = env.get(f"{ENV_MODE}_{suffix}")

    raw = per_image if per_image is not None else env.get(ENV_MODE, SIM_IMAGE_MODE_PUBLISHED)
    mode = raw.strip()
    if mode not in _VALID_MODES:
        source = (
            f"{ENV_MODE}_{image_name.strip().upper().replace('-', '_')}"
            if per_image is not None else ENV_MODE
        )
        raise ValueError(
            f"{source}={mode!r} is invalid; expected one of {_VALID_MODES}"
        )
    return mode


def resolve_sim_image_ref(image_name: str, env: Mapping[str, str]) -> str:
    """Resolve the fully-qualified ``<registry>/<image_name>:<tag>`` reference
    for the ``published`` path.

    Registry/tag come from ``PUBLIC_ECR_REGISTRY`` / ``PUBLIC_ECR_TAG`` when
    set, else the pinned defaults. An **explicitly empty / whitespace** value
    is a hard error — the stack must never synthesize an empty image
    reference.
    """
    if not image_name or not image_name.strip():
        raise ValueError("image_name must be a non-empty string")

    registry = env.get(ENV_REGISTRY, DEFAULT_PUBLIC_ECR_REGISTRY)
    tag = env.get(ENV_TAG, SIM_IMAGE_VERSION)

    if not registry or not registry.strip():
        raise ValueError(
            f"{ENV_REGISTRY} resolved empty; refusing to synthesize an empty "
            "image reference. Unset it to use the default, or set a non-empty value."
        )
    if not tag or not tag.strip():
        raise ValueError(
            f"{ENV_TAG} resolved empty; refusing to synthesize an empty image "
            "reference. Unset it to use the default, or set a non-empty value."
        )

    return f"{registry.strip().rstrip('/')}/{image_name.strip()}:{tag.strip()}"


# --- Guard 2: published-image source provenance ----------------------------
# issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/
#
# ``published`` mode pulls a PINNED tag. Nothing previously related that tag to
# the simulation source in the checkout, so source could advance arbitrarily far
# ahead of the image while deploys reported success. This gate makes that state
# fail closed at synth.
#
# Deliberately git-provenance rather than a content digest: a digest would have
# to re-implement stage_ecr_resources.sh's prune_per_dockerignore to hash the
# same file set the image build sees, and would drift spuriously whenever that
# logic changed. Git already tracks "did these paths change".


def _repo_root() -> "Path":
    """Repo root, derived from this file's location (deployment/stacks/)."""
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def check_published_image_provenance(
    env: Mapping[str, str], repo_root=None
) -> tuple[list[str], str | None]:
    """Compare simulation source against the commit the pinned image was built from.

    Returns ``(drifted_paths, undeterminable_reason)``:

    * ``([], None)`` — in sync; the published image matches the source.
    * ``([paths...], None)`` — drift detected; the pinned image is stale.
    * ``([], "<reason>")`` — could not determine (no git metadata, unknown
      commit, explicit tag override). Callers MUST treat this as *allow* with a
      warning, never as a failure: the fresh-customer path is a release archive
      with no ``.git``, and that path is the entire reason ``published`` mode
      exists. Failing there would break the only consumer it serves.

    Never raises, never makes network calls, never needs AWS credentials.
    """
    import shutil
    import subprocess

    commit = (SIM_IMAGE_SOURCE_COMMIT or "").strip()
    if not commit:
        return [], "SIM_IMAGE_SOURCE_COMMIT is unset"

    # An explicit PUBLIC_ECR_TAG override selects an image this constant does not
    # describe, so provenance is unknowable rather than violated.
    tag_override = (env.get(ENV_TAG) or "").strip()
    if tag_override and tag_override != SIM_IMAGE_VERSION:
        return [], (
            f"{ENV_TAG}={tag_override} overrides the pinned "
            f"{SIM_IMAGE_VERSION}; SIM_IMAGE_SOURCE_COMMIT does not describe it"
        )

    if shutil.which("git") is None:
        return [], "git is not available on PATH"

    root = repo_root if repo_root is not None else _repo_root()
    if not (root / ".git").exists():
        return [], f"no git metadata at {root}"

    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", commit, "--", *SIM_SOURCE_PATHS],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        return [], f"git invocation failed: {type(exc).__name__}: {exc}"

    if proc.returncode != 0:
        # Most common cause: shallow clone / release archive where the recorded
        # commit is not in the object store.
        detail = (proc.stderr or "").strip().splitlines()
        return [], (
            f"git could not compare against {commit[:12]}"
            + (f" ({detail[0]})" if detail else "")
        )

    drifted = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return drifted, None


def assert_published_image_fresh(
    image_name: str, env: Mapping[str, str], repo_root=None
) -> None:
    """Fail closed when ``published`` mode would deploy a stale image.

    Called from the synth path (``simulation_stack._resolve_sim_container_image``)
    rather than from ``resolve_sim_image_ref``, so the reference resolver stays a
    pure function of its inputs and remains trivially unit-testable.
    """
    if (env.get(ENV_ALLOW_STALE) or "").strip() == "1":
        print(
            f"⚠️  {ENV_ALLOW_STALE}=1 — skipping published-image provenance gate "
            f"for {image_name}. The deployed container may run source older than "
            "this checkout."
        )
        return

    drifted, reason = check_published_image_provenance(env, repo_root=repo_root)

    if reason is not None:
        print(
            f"⚠️  published-image provenance not verified for {image_name}: "
            f"{reason}. Proceeding — this is expected for a release archive."
        )
        return

    if not drifted:
        return

    shown = "\n".join(f"    - {p}" for p in drifted[:10])
    more = f"\n    … and {len(drifted) - 10} more" if len(drifted) > 10 else ""
    raise ValueError(
        f"\n{image_name}: published image is STALE relative to this checkout.\n"
        f"  Pinned tag:      {SIM_IMAGE_VERSION}\n"
        f"  Built from:      {SIM_IMAGE_SOURCE_COMMIT[:12]}\n"
        f"  Drifted paths ({len(drifted)}):\n{shown}{more}\n"
        "\n"
        "  Deploying now would run OLD simulation code in a container that\n"
        "  reports success. Choose one:\n"
        f"    1. Build from this checkout:  SIM_IMAGE_MODE={SIM_IMAGE_MODE_ASSET}\n"
        "    2. Republish the image:       bump SIM_IMAGE_VERSION +\n"
        "       SIM_IMAGE_SOURCE_COMMIT, then make publish-public-ecr VERSION=<new>\n"
        f"    3. Accept the risk knowingly: {ENV_ALLOW_STALE}=1\n"
    )

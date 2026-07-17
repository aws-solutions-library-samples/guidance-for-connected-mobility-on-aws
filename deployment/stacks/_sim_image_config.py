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
SIM_IMAGE_VERSION: str = "v0.2.6"

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


def get_sim_image_mode(env: Mapping[str, str]) -> str:
    """Return the validated simulation image mode from the environment.

    Defaults to ``published`` when unset. Raises ``ValueError`` on an
    unrecognized value so a typo fails loudly rather than silently selecting
    a build path.
    """
    mode = env.get(ENV_MODE, SIM_IMAGE_MODE_PUBLISHED).strip()
    if mode not in _VALID_MODES:
        raise ValueError(
            f"{ENV_MODE}={mode!r} is invalid; expected one of {_VALID_MODES}"
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

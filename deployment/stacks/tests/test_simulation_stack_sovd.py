"""Synth-based tests for the SOVD additions to SimulationStack.

Spec: .kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/ (Group 5 Task 5.2 Part B)
SR Cycle 4 Suggestion #1: sidecar worker_task_role must have s3:PutObject on the
SOVD responses bucket so _handle_sovd() can upload oversized payloads.

These tests synthesise SimulationStack in isolation (no app.py guards) using the
CDK Python API, then assert structural invariants on the CloudFormation template.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/tests/test_simulation_stack_sovd.py -v

Or from the project root:
  deployment/.venv/bin/python -m pytest deployment/stacks/tests/test_simulation_stack_sovd.py -v

Note: SIM_IMAGE_ALLOW_STALE=1 is set by the fixture to bypass the published-image
provenance gate, which would fail in unit/CI contexts where the image is not freshly
built from the current checkout. This is only safe in test isolation; do not set this
flag in a production deploy context.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the deployment directory is on sys.path so `stacks.*` imports work.
_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

# ---------------------------------------------------------------------------
# Constants — kept in sync with commands_stack.py's bucket naming convention
# ---------------------------------------------------------------------------

_STAGE = "staging"
_ACCOUNT = "123456789012"   # synthetic placeholder
_REGION = "us-west-2"

_BUCKET_NAME = f"cms-{_STAGE}-storage-sovd-responses-{_REGION}-{_ACCOUNT}"
_BUCKET_ARN_FRAGMENT = f"arn:aws:s3:::{_BUCKET_NAME}"


# ---------------------------------------------------------------------------
# Lazy fixture: synthesise the stack once per module. Skip if CDK unavailable.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def template() -> dict:
    """Synthesise SimulationStack and return the CloudFormation template dict.

    Sets SIM_IMAGE_ALLOW_STALE=1 to bypass the provenance gate (image freshness
    is irrelevant for IAM/env-var structural tests). This is test-only and must
    never propagate to a real deploy.
    """
    try:
        from aws_cdk import App, Environment
        from stacks.simulation_stack import SimulationStack
    except ImportError as exc:
        pytest.skip(f"aws_cdk unavailable: {exc}")

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    os.environ["SIM_IMAGE_ALLOW_STALE"] = "1"  # unit test only — skip provenance gate

    app = App()
    stack = SimulationStack(
        app,
        f"cms-{_STAGE}-simulation",
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    synth_result = app.synth()
    raw = synth_result.get_stack_by_name(f"cms-{_STAGE}-simulation").template
    return json.loads(json.dumps(raw))


# ---------------------------------------------------------------------------
# Helpers — mirror the pattern from test_commands_stack_sovd.py
# ---------------------------------------------------------------------------

def _of_type(t: dict, resource_type: str) -> dict[str, dict]:
    return {k: v for k, v in t["Resources"].items() if v["Type"] == resource_type}


def _iam_policies(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::IAM::Policy")


def _all_iam_statements(t: dict) -> list[dict]:
    """Flatten all IAM statements from all policies in the template."""
    statements = []
    for policy in _iam_policies(t).values():
        doc = policy.get("Properties", {}).get("PolicyDocument", {})
        statements.extend(doc.get("Statement", []))
    return statements


def _statement_actions(stmt: dict) -> list[str]:
    actions = stmt.get("Action", [])
    if isinstance(actions, str):
        return [actions]
    return actions


def _statement_resources(stmt: dict) -> list[str]:
    resources = stmt.get("Resource", [])
    if isinstance(resources, str):
        return [resources]
    # Resources may be dicts (Fn::Sub, Fn::Join, etc.) — convert to string for grep
    return [json.dumps(r) for r in resources]


# ---------------------------------------------------------------------------
# Test: worker_task_role s3:PutObject grant (SR Cycle 4 Suggestion #1)
# ---------------------------------------------------------------------------

class TestWorkerTaskRoleSovdS3Grant:
    """T5.2 Part B — sidecar worker_task_role must have s3:PutObject on the SOVD bucket.

    SR Cycle 4 Suggestion #1 flagged the missing grant. Without it, _handle_sovd's
    S3 upload path fails with AccessDenied and oversized SOVD payloads cannot be
    offloaded from MQTT — which then fails for payloads above the 128 KB MQTT limit.
    """

    def test_worker_task_role_grants_s3_putobject_on_sovd_responses_bucket(
        self, template: dict
    ) -> None:
        """worker_task_role must have s3:PutObject on the SOVD responses bucket ARN.

        Verifies:
        - At least one IAM statement includes Action: s3:PutObject
        - That statement's Resource includes the SOVD responses bucket ARN pattern
          (cms-{stage}-storage-sovd-responses-{region}-{account}/*)
        - The grant uses string-pattern ARN construction (no cross-stack reference)
        """
        statements = _all_iam_statements(template)
        matching = [
            stmt for stmt in statements
            if "s3:PutObject" in _statement_actions(stmt)
            and any(_BUCKET_NAME in r for r in _statement_resources(stmt))
        ]
        assert matching, (
            f"No IAM statement found with Action=s3:PutObject and Resource containing "
            f"'{_BUCKET_NAME}'. "
            "The sidecar's worker_task_role must have s3:PutObject on the SOVD responses "
            "bucket to upload oversized payloads (> 80 KB) without AccessDenied. "
            "See spec § Design § 2, SR Cycle 4 Suggestion #1, and decisions.md "
            "§ 2026-09-01 Task 5.2 scope extension."
        )

    def test_worker_task_role_s3_grant_covers_object_path(
        self, template: dict
    ) -> None:
        """The s3:PutObject grant must include the /* suffix for object-level access.

        A grant on the bucket ARN alone (without /*) does not allow PutObject on
        individual objects — s3:PutObject requires the object path in the Resource.
        """
        statements = _all_iam_statements(template)
        s3_put_on_sovd = [
            stmt for stmt in statements
            if "s3:PutObject" in _statement_actions(stmt)
            and any(_BUCKET_NAME in r for r in _statement_resources(stmt))
        ]
        assert s3_put_on_sovd, (
            f"No s3:PutObject grant on SOVD bucket found. "
            f"Cannot verify object-path coverage without a base grant."
        )
        for stmt in s3_put_on_sovd:
            resources = _statement_resources(stmt)
            sovd_resources = [r for r in resources if _BUCKET_NAME in r]
            has_object_path = any(
                r.endswith('/*"') or r.endswith("/*") or "/*" in r
                for r in sovd_resources
            )
            assert has_object_path, (
                f"s3:PutObject grant on SOVD bucket lacks '/*' object-path suffix. "
                f"Resources found: {sovd_resources}. "
                "Add '/*' to the Resource ARN to allow PutObject on bucket objects."
            )

    def test_worker_task_role_existing_grants_unchanged(
        self, template: dict
    ) -> None:
        """The SOVD s3:PutObject addition must not remove existing worker_task_role grants.

        Specifically: dynamodb:GetItem/Query (vehicle state), iot:Publish (telemetry),
        geo:CalculateRoute (routing), sts:GetCallerIdentity (IAM identity verification)
        must still be present.
        """
        statements = _all_iam_statements(template)
        all_actions = set()
        for stmt in statements:
            all_actions.update(_statement_actions(stmt))

        for required_action in [
            "dynamodb:GetItem",
            "iot:Publish",
            "geo:CalculateRoute",
            "sts:GetCallerIdentity",
        ]:
            assert required_action in all_actions, (
                f"Required action '{required_action}' is missing from the template. "
                f"The SOVD s3:PutObject addition must be purely additive. "
                "Do not alter or remove existing worker_task_role grants."
            )


class TestWorkerTaskEnvVarsSovd:
    """T5.2 Part B — SOVD_RESPONSES_BUCKET env var must be set on the vehicle-ecu container.

    Fix Group 4 / Cycle 8 remediation: tests are scoped to the vehicle-ecu container
    by name within the fwe-agent task definition family.  The prior generic matcher
    (any container in any task def) passed because it found the env var on the Fargate
    worker container — which does NOT run _handle_sovd.  Scoped matchers catch the
    exact container-level mismatch the Critical finding described.
    """

    def test_vehicle_ecu_container_has_sovd_responses_bucket_env_var(
        self, template: dict
    ) -> None:
        """The vehicle-ecu sidecar container must carry SOVD_RESPONSES_BUCKET.

        The vehicle-ecu sidecar is the ONLY container that runs
        `realtime_telemetry_simulator.py --commands-mqtt` and therefore the ONLY
        container that calls _handle_sovd() and attempts the S3 upload.
        The env var must be on THIS container — not just somewhere in the template.

        Looks up the fwe-agent task definition (family ends with '-fwe-agent') and
        finds the container named 'vehicle-ecu' within it.
        """
        task_defs = _of_type(template, "AWS::ECS::TaskDefinition")
        assert task_defs, "No ECS TaskDefinition resources found in template"

        fwe_agent_task = None
        for logical_id, task_def in task_defs.items():
            family = task_def.get("Properties", {}).get("Family", "")
            if family.endswith("-fwe-agent"):
                fwe_agent_task = task_def
                break

        assert fwe_agent_task is not None, (
            "No ECS TaskDefinition with family ending in '-fwe-agent' found. "
            "The fwe-agent task definition must exist (contains fwe-agent + vehicle-ecu)."
        )

        containers = fwe_agent_task.get("Properties", {}).get("ContainerDefinitions", [])
        vehicle_ecu = next(
            (c for c in containers if c.get("Name") == "vehicle-ecu"), None
        )
        assert vehicle_ecu is not None, (
            "No container named 'vehicle-ecu' found in the fwe-agent task definition. "
            "The vehicle-ecu sidecar must be a ContainerDefinition in the fwe-agent task."
        )

        env_vars = vehicle_ecu.get("Environment", [])
        sovd_bucket_vars = [
            e for e in env_vars
            if e.get("Name") == "SOVD_RESPONSES_BUCKET"
            and _BUCKET_NAME in e.get("Value", "")
        ]
        assert sovd_bucket_vars, (
            f"vehicle-ecu container lacks SOVD_RESPONSES_BUCKET={_BUCKET_NAME!r}. "
            "_handle_sovd() reads SOVD_RESPONSES_BUCKET at runtime to construct the "
            "S3 key path.  Without this env var the S3 upload path falls back to "
            "inline MQTT for ALL response sizes, which fails at the 128 KB MQTT limit. "
            "Add SOVD_RESPONSES_BUCKET to the vehicle-ecu container environment in "
            "simulation_stack.py (Fix Group 4, FG4.1)."
        )

    def test_sovd_env_var_is_on_vehicle_ecu_not_worker_only(
        self, template: dict
    ) -> None:
        """Positive control: SOVD_RESPONSES_BUCKET must be on vehicle-ecu, regardless of worker.

        This test is the negative-space complement to
        test_vehicle_ecu_container_has_sovd_responses_bucket_env_var.  The original
        Cycle 8 Critical defect was that the env var was ONLY on the Fargate worker
        container (which never calls _handle_sovd), and absent from vehicle-ecu.

        This test catches a regression where the env var is moved back to the worker
        and the sidecar is left without it, independently of whatever the worker carries.
        """
        task_defs = _of_type(template, "AWS::ECS::TaskDefinition")

        fwe_agent_task = None
        for logical_id, task_def in task_defs.items():
            family = task_def.get("Properties", {}).get("Family", "")
            if family.endswith("-fwe-agent"):
                fwe_agent_task = task_def
                break

        assert fwe_agent_task is not None, (
            "No ECS TaskDefinition with family ending in '-fwe-agent' found."
        )

        containers = fwe_agent_task.get("Properties", {}).get("ContainerDefinitions", [])
        vehicle_ecu = next(
            (c for c in containers if c.get("Name") == "vehicle-ecu"), None
        )
        assert vehicle_ecu is not None, (
            "No 'vehicle-ecu' container found in fwe-agent task definition."
        )

        vehicle_ecu_env_names = [
            e.get("Name") for e in vehicle_ecu.get("Environment", [])
        ]
        assert "SOVD_RESPONSES_BUCKET" in vehicle_ecu_env_names, (
            "SOVD_RESPONSES_BUCKET is absent from the vehicle-ecu container. "
            "The Cycle 8 Critical defect was that the env var was only on the "
            "Fargate worker (which does not run --commands-mqtt and never calls "
            "_handle_sovd).  This positive-control test ensures the env var is "
            "always on the container that actually performs S3 uploads. "
            "Fix: add SOVD_RESPONSES_BUCKET to the vehicle-ecu environment in "
            "simulation_stack.py (see Fix Group 4, FG4.1)."
        )

    def test_vehicle_ecu_container_has_aws_region_env_var(
        self, template: dict
    ) -> None:
        """The vehicle-ecu sidecar container must carry AWS_REGION.

        The sidecar uses AWS_REGION for boto3 session construction and S3 key
        building. This was already set prior to the SOVD work; this test confirms
        it was not accidentally removed from the vehicle-ecu container.
        """
        task_defs = _of_type(template, "AWS::ECS::TaskDefinition")
        assert task_defs, "No ECS TaskDefinition resources found in template"

        fwe_agent_task = None
        for logical_id, task_def in task_defs.items():
            family = task_def.get("Properties", {}).get("Family", "")
            if family.endswith("-fwe-agent"):
                fwe_agent_task = task_def
                break

        assert fwe_agent_task is not None, (
            "No fwe-agent task definition found."
        )

        containers = fwe_agent_task.get("Properties", {}).get("ContainerDefinitions", [])
        vehicle_ecu = next(
            (c for c in containers if c.get("Name") == "vehicle-ecu"), None
        )
        assert vehicle_ecu is not None, (
            "No 'vehicle-ecu' container found in fwe-agent task definition."
        )

        env_vars = vehicle_ecu.get("Environment", [])
        region_vars = [e for e in env_vars if e.get("Name") == "AWS_REGION"]
        assert region_vars, (
            "vehicle-ecu container does not have AWS_REGION env var. "
            "This was set prior to the SOVD work and must not have been removed. "
            "See simulation_stack.py vehicle-ecu container environment."
        )



# ---------------------------------------------------------------------------
# Test: F2.1 — _shared/routine_catalog.py is in the staged sim-service build context
# ---------------------------------------------------------------------------

class TestSharedModuleBundled:
    """F2.1 — _shared/routine_catalog.py is staged into the sim-service Docker build context.

    For the sidecar container the bundle is the Docker build context (not a Lambda zip).
    _stage_sim_service_context() copies services/simulation/ and overlays services/_shared/
    at _shared/. The Dockerfile then COPYs it into /app/_shared/ so
    `import _shared.routine_catalog` resolves from /app/ (the WORKDIR).

    An import that works locally proves nothing about what ships — this test
    asserts presence in .build/sim_service/ at synth time. The template fixture
    imports simulation_stack, which triggers _stage_sim_service_context() at module
    scope, so the staged dir is always populated when this test runs.
    """

    def test_shared_routine_catalog_py_in_staged_context(self, template: dict) -> None:  # noqa: ARG002
        """_shared/routine_catalog.py must exist in the staged sim-service build context.

        Without this, `docker build` from the staged context cannot COPY _shared/
        into the image, and the sidecar cannot import ``_shared.routine_catalog``
        at runtime — reading safety_class from the request instead (F28 fail-open).
        """
        import os
        from stacks.simulation_stack import _SIM_SERVICE_BUILD_DIR
        routine_catalog_path = os.path.join(
            _SIM_SERVICE_BUILD_DIR, "_shared", "routine_catalog.py"
        )
        assert os.path.isfile(routine_catalog_path), (
            f"routine_catalog.py not found in the sim-service staged build context at "
            f"{routine_catalog_path}. "
            "_stage_sim_service_context() must overlay services/_shared/ at _shared/ "
            "so docker build can COPY it into /app/_shared/ and the sidecar can import "
            "_shared.routine_catalog. "
            "F28 root cause: a safety authority that ships only in deployment/scripts/ "
            "is not reachable by the sidecar runtime. See Fix Group 2 and "
            "issues/2026-09-08-safety-class-taken-from-request-fail-open/."
        )

    def test_shared_init_py_in_staged_context(self, template: dict) -> None:  # noqa: ARG002
        """_shared/__init__.py must also be present in the staged build context.

        Without __init__.py Python treats _shared/ as a namespace package rather
        than a regular package; under some import configurations this prevents
        `from _shared.routine_catalog import ...` from resolving.
        """
        import os
        from stacks.simulation_stack import _SIM_SERVICE_BUILD_DIR
        init_path = os.path.join(_SIM_SERVICE_BUILD_DIR, "_shared", "__init__.py")
        assert os.path.isfile(init_path), (
            f"_shared/__init__.py not found in the staged build context at {init_path}. "
            "services/_shared/__init__.py must be present in the overlay for Python "
            "to treat _shared/ as a regular package inside the sidecar container."
        )

    def test_dockerfile_copies_shared_into_image(self, template: dict) -> None:  # noqa: ARG002
        """The Dockerfile in the staged build context must COPY _shared/ into /app/.

        The staged build context has _shared/ at its root; the Dockerfile must
        instruct `docker build` to COPY it into the image WORKDIR (/app/) so that
        `import _shared.routine_catalog` from within a running container resolves.
        """
        import os
        from stacks.simulation_stack import _SIM_SERVICE_BUILD_DIR
        dockerfile_path = os.path.join(_SIM_SERVICE_BUILD_DIR, "Dockerfile")
        assert os.path.isfile(dockerfile_path), (
            f"Dockerfile not found in the staged build context at {dockerfile_path}. "
            "The sim-service Dockerfile must be present in the staged context."
        )
        with open(dockerfile_path) as fh:
            content = fh.read()
        assert "COPY _shared/" in content, (
            "The sim-service Dockerfile does not contain 'COPY _shared/'. "
            "Without this instruction, `docker build` will not include _shared/ in the "
            "image even if it is present in the staged build context. "
            "Add `COPY _shared/ ./_shared/` after the other COPY directives."
        )

    def test_shared_routine_catalog_not_duplicated_in_sim_dir(
        self, template: dict  # noqa: ARG002
    ) -> None:
        """routine_catalog.py must NOT appear in services/simulation/ directly.

        A copy in services/simulation/ would drift silently from services/_shared/ —
        the same failure mode that produced F27. One module, one source of truth,
        overlaid at bundle time.
        """
        import os
        sim_src = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "services", "simulation",
        ))
        direct_copy = os.path.join(sim_src, "routine_catalog.py")
        assert not os.path.isfile(direct_copy), (
            f"routine_catalog.py found at {direct_copy}. "
            "Do NOT copy this module into services/simulation/ — "
            "services/_shared/ is the single source of truth, overlaid at bundle time. "
            "A second copy drifts silently and is the F27 failure mode one layer down."
        )



# ---------------------------------------------------------------------------
# Test: every sibling module the sim entrypoints import is COPYed into the image
# ---------------------------------------------------------------------------

class TestSimulatorImportsAreCopiedIntoImage:
    """The Dockerfile's explicit COPY list must cover every sibling module imported.

    WHY THIS EXISTS, AND WHY TestSharedModuleBundled ABOVE DID NOT CATCH IT
    ----------------------------------------------------------------------
    `services/simulation/Dockerfile` COPYs an *enumerated* list of .py files
    rather than the whole directory — deliberately, to keep the sidecar image
    minimal (26 of the directory's top-level modules are dev/ops/test scripts
    that must not ship). The cost of an allowlist is that adding a new runtime
    module requires editing two places, and nothing linked them.

    `routine_sims.py` was added by spec 2026-09-10-cms-sovd-routine-result-contracts
    and imported by `realtime_telemetry_simulator._handle_sovd`, but never added
    to the COPY list. `TestSharedModuleBundled` above passed throughout, because
    it asserts presence in the *staged build context* — and the staged context
    was always correct (`_stage_sim_service_context` copies the whole directory).
    The omission was one layer further down, in what `docker build` selects out
    of that context.

    Observed failure mode in the built image (reproduced before this test was
    written): `from routine_sims import produce_result` raises ImportError, the
    dual-context fallback `from services.simulation.routine_sims import ...`
    also fails ("No module named 'services'" — there is no `services/` package
    inside the image), and the outer handler converts it to

        {"status": "FAILED", "reason": "schema validation failed: No module named 'services'"}

    So every SOVD routine invocation fails, while *reporting a schema problem
    that does not exist*. That misdirection is the reason this is a test rather
    than a comment: the symptom points away from the cause.

    See issues/2026-09-13-routine-sims-omitted-from-sidecar-image/.
    """

    @staticmethod
    def _copied_names(dockerfile_text: str) -> set[str]:
        """Top-level names COPYed by the Dockerfile, honouring line continuations."""
        import re
        # Join continuation lines so a multi-line COPY is one logical line.
        joined = re.sub(r"\\\s*\n", " ", dockerfile_text)
        names: set[str] = set()
        for line in joined.splitlines():
            stripped = line.strip()
            if not stripped.upper().startswith("COPY "):
                continue
            parts = stripped.split()[1:]
            # Drop flags (--from=..., --chown=...) and the final destination arg.
            parts = [p for p in parts if not p.startswith("--")]
            if len(parts) < 2:
                continue
            for src in parts[:-1]:
                names.add(src.rstrip("/"))
        return names

    @staticmethod
    def _sibling_modules(src_dir: str) -> set[str]:
        import os
        return {
            f[:-3]
            for f in os.listdir(src_dir)
            if f.endswith(".py") and not f.startswith("__")
        }

    @classmethod
    def _reachable_sibling_imports(cls, src_dir: str, entrypoints: tuple[str, ...]) -> set[str]:
        """Transitive closure of sibling-module imports reachable from *entrypoints*.

        Transitive, not just direct: a module that ships can still import a
        sibling that does not, which fails identically at runtime. Uses ``ast``
        so imports nested inside functions are seen — ``routine_sims`` is
        imported inside ``_handle_sovd``, not at module scope, which a
        module-level-only scan would miss.
        """
        import ast
        import os

        siblings = cls._sibling_modules(src_dir)
        seen: set[str] = set()
        queue = list(entrypoints)

        while queue:
            mod = queue.pop()
            if mod in seen:
                continue
            seen.add(mod)
            path = os.path.join(src_dir, f"{mod}.py")
            if not os.path.isfile(path):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for node in ast.walk(tree):
                found: set[str] = set()
                if isinstance(node, ast.Import):
                    found = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    found = {node.module.split(".")[0]}
                for name in found & siblings:
                    if name not in seen:
                        queue.append(name)

        # The entrypoints themselves are required too.
        return (seen & siblings) | set(entrypoints)

    def test_every_imported_sibling_module_is_copied(self, template: dict) -> None:  # noqa: ARG002
        """No runtime module may be importable locally yet absent from the image."""
        import os
        from stacks.simulation_stack import _SIM_SERVICE_SRC_DIR

        dockerfile = os.path.join(_SIM_SERVICE_SRC_DIR, "Dockerfile")
        text = open(dockerfile, encoding="utf-8").read()

        copied = self._copied_names(text)
        required = self._reachable_sibling_imports(
            _SIM_SERVICE_SRC_DIR,
            ("simulation_api", "realtime_telemetry_simulator"),
        )

        missing = sorted(m for m in required if f"{m}.py" not in copied)
        assert not missing, (
            "These modules are imported at sim runtime but are NOT in the "
            f"Dockerfile COPY list, so they will be absent from the image: {missing}.\n"
            f"  Dockerfile: {dockerfile}\n"
            "  Add them to the enumerated COPY. Do NOT switch to `COPY . ./` — the "
            "allowlist is deliberate and keeps ~26 dev/ops/test scripts out of the "
            "sidecar image.\n"
            "  A module missing here fails at runtime as "
            '"schema validation failed: No module named \'services\'", which points '
            "at the schema layer rather than at this list."
        )

    def test_guard_is_not_vacuous(self, template: dict) -> None:  # noqa: ARG002
        """The closure must actually resolve modules, or the test above proves nothing.

        If ``_reachable_sibling_imports`` returned an empty or near-empty set (a
        parser regression, a renamed entrypoint), ``missing`` would be trivially
        empty and the guard would pass while checking nothing.
        """
        from stacks.simulation_stack import _SIM_SERVICE_SRC_DIR

        required = self._reachable_sibling_imports(
            _SIM_SERVICE_SRC_DIR,
            ("simulation_api", "realtime_telemetry_simulator"),
        )
        assert "routine_sims" in required, (
            "routine_sims is imported by realtime_telemetry_simulator._handle_sovd "
            "but the import closure did not find it — the ast walk or the sibling "
            "set is broken, and the sibling-COPY guard is therefore vacuous."
        )
        assert len(required) >= 6, (
            f"import closure resolved only {len(required)} modules "
            f"({sorted(required)}); expected the entrypoints plus their sibling "
            "imports. Suspect a parser regression."
        )

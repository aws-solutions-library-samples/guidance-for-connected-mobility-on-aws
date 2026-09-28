"""Single source of truth for Fleet Intelligence ADP-consumer resource names.

Spec: `.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/` § D4.

Why this module exists
----------------------
The Athena workgroup and its results bucket are created by
``fleet_intelligence_analytics_stack.py`` in **us-east-1**. The IAM policy that
authorizes them is attached to ``FleetIntelligenceRole`` in ``ui_stack.py``,
which on staging deploys to **us-west-2**. CloudFormation cannot export a value
across Regions, so the two stacks cannot share a reference — they can only
agree on a name computed the same way on both sides.

That agreement is the failure mode worth guarding. If ``ui_stack`` and the
analytics stack each build the bucket name independently and one of them
changes, both stacks still synthesize, both still deploy, every per-stack
assertion still passes, and the live query is denied because the policy names a
bucket that does not exist. Putting the names here makes the drift impossible
rather than merely tested for.

The stage/region/account inputs are all resolved at synth time, so nothing
about these names needs to travel to the Lambda at runtime. This is why § D4's
``ADP_LAKE_BUCKET`` and ``ADP_ACCOUNT`` env vars are not set: the CDK builds
every ARN it needs here, and no handler code reads either variable.

Note for test authors: the guard suite
``stacks/tests/test_ui_stack_fleet_intelligence_adp_grants.py`` deliberately
does **not** import this module. It restates § D4's literals independently, so
a mutation here fails there instead of silently moving both sides together.
"""
from __future__ import annotations

import re as _re

# ADP's lake, Glue catalog and therefore Athena all live in us-east-1, on both
# stages. CMS staging is us-west-2 and CMS prod is us-east-1, so this is a
# cross-Region read on staging and a same-Region read on prod. The value is not
# derived from the deploying stack's region for that reason.
ADP_REGION = "us-east-1"

# The five products in ADP's CMS-facing Lake Formation share. Must stay in
# lockstep with `platform-foundation/stacks/governance_stack.py`'s
# `GovernanceStack._CMS_SHARE_DATABASES` — an IAM grant for a database the LF
# share omits is dead, and an LF grant the IAM policy omits is unreachable.
#
# `tire_health` has no consumer in `services/fleet_intelligence/` and that is
# deliberate (§ D11): the tire-prediction surface is deferred, and the grant is
# preemptive so a v1.2 PM-due signal is a code change rather than a permissions
# change. Do not "clean it up".
#
# `vehicle_telemetry_aggregated` is deliberately absent (§ D11): 2,880 parquet
# files and 2.9 GB with no v1.1 payoff.
ADP_PRODUCTS: tuple[str, ...] = (
    "service_records",
    "charging_sessions",
    "vehicle_identity",
    "energy_usage",
    "tire_health",
)

# ADP dimension tables the Fleet Intelligence code joins. Only `vins`: the ADP
# rollup (spec 2026-09-25-cms-fi-adp-wide-lifecycle, Q3 and Q4) reads model and
# model year from `adp_{stage}_dimensions.vins` for the cohort table.
# `vehicle_identity` has no model year, which is why it can't stand in.
#
# Granted per TABLE, never `table/adp_{stage}_dimensions/*` and never the
# `dimensions/` S3 prefix: the same database holds `customers`, which is PII.
# Must stay in lockstep with `platform-foundation/stacks/governance_stack.py`'s
# `GovernanceStack._CMS_DIMENSION_TABLES`, for the same reason as ADP_PRODUCTS.
#
# The T2.4 build assumed "no IAM change" and shipped without this; the first
# staging refresh was denied `glue:GetDatabase` on `adp_staging_dimensions`
# (issues/2026-09-26-fi-adp-rollup-role-lacks-dimensions-grant/).
ADP_DIMENSIONS_DATABASE = "dimensions"
ADP_DIMENSION_TABLES: tuple[str, ...] = ("vins",)

# Athena writes query results under this prefix, and the results bucket expires
# objects beneath it after 7 days. Results are cheap to regenerate.
RESULTS_PREFIX = "fleet-intelligence/"

_STAGE_SHAPE = _re.compile(r"^[a-z0-9]+$")


def _validate_stage(stage: str) -> None:
    """Reject stage strings that would produce a malformed resource name.

    This is **shape** validation, not an allowlist. CMS is a public reference
    architecture and its stage space is open: ``ui_stack.py`` already recognises
    ``dev``/``development``/``local``/``test`` alongside ``staging``/``prod``,
    ``stacks/tests/test_callback_url_registry.py`` synthesizes ``cms-test-ui``,
    and a customer may deploy under a stage name of their own. An allowlist here
    would hard-fail their synth on a feature they might not even use. (An earlier
    revision of this module borrowed ADP's two-value allowlist verbatim and did
    exactly that — it broke four callback-registry tests, because ADP genuinely
    only has staging and prod and CMS does not.)

    What is rejected is a stage that cannot produce a valid name downstream:

    * empty or whitespace — yields ``cms--analytics``
    * uppercase — S3 bucket names are lowercase-only, so the deploy fails late
      with an opaque CloudFormation error instead of here
    * hyphens or underscores — the stage is interpolated into both
      ``cms-{stage}-athena-results-...`` (where a hyphen makes the segmentation
      ambiguous) and ``adp_{stage}_{product}`` (Glue database names disallow
      hyphens)

    A stage that is merely *misspelled* — ``stagng`` — is shape-valid and passes
    here on purpose. It is self-revealing rather than silent: ``ADP_STAGE``
    reaches the Lambda as ``stagng``, the query targets
    ``adp_stagng_service_records``, and Athena fails with ``TABLE_NOT_FOUND`` on
    the first call. Failing synth for that case would cost a third-party deploy
    more than it saves.
    """
    if not isinstance(stage, str) or not _STAGE_SHAPE.match(stage):
        raise ValueError(
            f"stage must be lowercase alphanumeric with no separators; got {stage!r}. "
            "It is interpolated into an S3 bucket name (lowercase-only) and a Glue "
            "database name (no hyphens), so a malformed stage produces resources "
            "that cannot be created or cannot be read."
        )


def workgroup_name(stage: str) -> str:
    """Athena workgroup, e.g. ``cms-staging-analytics``.

    Deliberately CMS-owned rather than reusing CVX's `cvx-{stage}-analytics`.
    ADP already carries an open P2 for exactly that cross-project coupling
    (`publish_product.py` hardcoding `cvx-staging-analytics` for every stage);
    borrowing it here would be repeating a known defect on purpose.
    """
    _validate_stage(stage)
    return f"cms-{stage}-analytics"


def results_bucket_name(stage: str, account: str) -> str:
    """Athena results bucket, e.g. ``cms-staging-athena-results-<account>-us-east-1``.

    Region-suffixed per `~/.kiro/steering/cross-region-namespace.md`: S3 names
    are partition-global, so a name that is a function of {stage, account} alone
    collides with the same stage deployed to another Region.

    Length budget: the longest commercial Region name is 14 characters, giving
    ``cms-`` + 7 + ``-athena-results-`` + 12 + ``-`` + 14 = 54 characters
    against S3's 63-character limit. This name pins us-east-1 (9 characters, 49
    total) because Athena must run where ADP's catalog is.
    """
    _validate_stage(stage)
    return f"cms-{stage}-athena-results-{account}-{ADP_REGION}"


def results_output_location(stage: str, account: str) -> str:
    """``s3://`` URI Athena writes results to, including the trailing slash."""
    return f"s3://{results_bucket_name(stage, account)}/{RESULTS_PREFIX}"


def adp_lake_bucket_name(stage: str, account: str) -> str:
    """ADP's curated lake bucket.

    Mirrors `platform-foundation/stacks/_naming.py::_stage_name`, which composes
    ``adp-{stage}-foundation-{suffix}``, plus the ``-<account>-<region>`` suffix
    `foundation_stack.py` appends. Same account as CMS today, so the account is
    the consuming stack's own.
    """
    _validate_stage(stage)
    return f"adp-{stage}-foundation-lake-{account}-{ADP_REGION}"


def adp_database_name(stage: str, product: str) -> str:
    """ADP Glue database, e.g. ``adp_staging_service_records``.

    Mirrors `platform-foundation/stacks/_naming.py::_stage_db_name`
    (``adp_{stage}_{product}``). Underscores, not hyphens — Glue database names
    cannot contain hyphens.
    """
    _validate_stage(stage)
    return f"adp_{stage}_{product}"


def curated_prefixes(stage: str) -> tuple[str, ...]:
    """The ten ``s3:prefix`` values the lake List grant is conditioned on.

    Two per product: the bare prefix (so a delimiter-scoped list of the
    "directory" itself succeeds) and the recursive form. `stage` is accepted for
    validation symmetry and to keep every name in this module stage-checked;
    curated prefixes are not themselves stage-scoped because the stage lives in
    the bucket name.
    """
    _validate_stage(stage)
    prefixes: list[str] = []
    for product in ADP_PRODUCTS:
        prefixes.append(f"curated/{product}")
        prefixes.append(f"curated/{product}/*")
    return tuple(prefixes)


def dimension_prefixes(stage: str) -> tuple[str, ...]:
    """The ``s3:prefix`` values for the granted dimension tables.

    Two per table, as for :func:`curated_prefixes`. ADP registers
    ``adp_{stage}_dimensions.<table>`` at ``dimensions/<table>`` in the lake
    bucket (checked against Glue 2026-09-26).
    """
    _validate_stage(stage)
    prefixes: list[str] = []
    for table in ADP_DIMENSION_TABLES:
        prefixes.append(f"dimensions/{table}")
        prefixes.append(f"dimensions/{table}/*")
    return tuple(prefixes)


# ── Provenance (§ D6) ────────────────────────────────────────────────────────
# ADP records carry no provenance column, so the consumer stamps one per row
# from `ADP_DATA_PROVENANCE`. The Lambda fails closed when it is unset
# (`adp_source.py`); the CDK's job is to make sure it never is.
#
# Derived from the stage rather than read from the deploying shell, because this
# repo has already paid for the alternative: `DRIVER_SELF_GUARD_ENABLED` is read
# from the deploy environment and prod therefore ran with the guard OFF for 18
# days after the fix was committed, because one deploy path did not export it
# (issues/2026-08-03-prod-driver-self-guard-inert). A wrong provenance label is
# quieter than that and worse — it presents Faker-generated cost figures as
# measured.
#
# prod stays `simulated` until § F5 clears: three of the five products are not
# published to prod at all. The flip signal is the `Prod publish batch` P2 and
# `PySpark local blocked` P2 backlog rows, not a deploy.
_PROVENANCE_BY_STAGE = {
    "staging": "simulated",
    "prod": "simulated",
}

# Any other stage — dev, test, local, or a customer's own name — is a
# non-production sandbox reading ADP's Faker-generated staging products, so
# `simulated` is the honest label. Kept as an explicit fallback rather than
# widening the map above, so the two real stages stay visible as decisions
# rather than as two entries among many.
_PROVENANCE_FALLBACK = "simulated"

# Kept in sync with `services/fleet_intelligence/provenance.py`. Duplicated
# rather than imported: `deployment/` does not have the Lambda bundle on its
# path, and a synth-time guard that silently degrades when an import fails is
# worse than a short literal with a named counterpart.
_VALID_PROVENANCE = ("measured", "simulated", "derived")


def data_provenance(stage: str, override: str | None = None) -> str:
    """Resolve the provenance label for a stage, validating any override.

    An explicit ``ADP_DATA_PROVENANCE`` in ``config/<stage>.env`` wins, so the
    § F5 flip is a one-line config change with no code deploy. An unrecognised
    value raises rather than reaching the Lambda, where it would fail closed at
    cold start and take the whole Fleet Intelligence surface down instead of
    failing the deploy that caused it.
    """
    _validate_stage(stage)
    if override is not None and override.strip():
        value = override.strip()
        if value not in _VALID_PROVENANCE:
            raise ValueError(
                f"ADP_DATA_PROVENANCE={value!r} is not one of {_VALID_PROVENANCE}. "
                "Set it in config/<stage>.env or leave it unset to take the "
                f"stage default ({_PROVENANCE_BY_STAGE.get(stage, _PROVENANCE_FALLBACK)!r})."
            )
        return value
    return _PROVENANCE_BY_STAGE.get(stage, _PROVENANCE_FALLBACK)

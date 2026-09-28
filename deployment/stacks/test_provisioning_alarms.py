"""Guard: every Cognito trigger Lambda is monitored, and no alarm is silent.

Group 9 of spec 2026-08-07-cms-account-provisioning-model.

The task text asked for three alarms that exist "even when the corresponding trigger
doesn't". This suite asserts a **structural invariant instead**, and the deviation is
deliberate — see `decisions.md` 2026-08-11:

    every Cognito trigger Lambda in the template has an Errors alarm,
    and every alarm in the template has a non-empty AlarmActions.

That is the property worth having. A fixed count of three alarms can be satisfied while a
trigger goes unmonitored (add a fourth trigger and the count still passes), and an alarm
for a function that does not exist sits in INSUFFICIENT_DATA forever while telling an
operator that coverage exists — the same "looks deployed, protects nothing" shape as the
CloudFront WAF rule that could never match.

The empty-`AlarmActions` assertion is the CVX Tier 2 lesson made executable: that spec
shipped two alarms with no actions, so an unattended nightly failure notified nobody and
Tier 1 kept narrating a stale artifact with a receding `computed_at`.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_provisioning_alarms.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOYMENT = os.path.abspath(os.path.join(_HERE, os.pardir))

# Trigger Lambdas are identified by their construct id prefix in the synthesised template.
# Keyed by the logical-id prefix CDK generates from the construct id.
_TRIGGER_LAMBDA_PREFIXES = ("ProvisioningLambda", "PreSignUpLambda")


def _synth(*context: str) -> dict:
    """Synthesise cms-staging-ui with extra context flags; return the template dict.

    Uses the canonical invocation (stage env file + domain-guard context); the
    abbreviated `cdk synth` form in tasks.md does not run.

    `--output` into a private temp directory is load-bearing, not tidiness. The default
    `cdk.out` is locked by whichever CLI is synthing, and a concurrent session doing so
    makes `cdk synth` exit non-zero with *"Another CLI (PID=...) is currently synthing to
    cdk.out"*. Observed 2026-08-11. Without `--output` this suite then SKIPS and reports
    green — a guard that disappears when someone else is working is not a guard, and the
    failure is invisible because a skip is not a failure.
    """
    env = dict(os.environ)
    cfg = os.path.join(_DEPLOYMENT, "config", "staging.env")
    if os.path.isfile(cfg):
        with open(cfg) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.replace("_", "").isalnum():
                    env[k] = v
    with tempfile.TemporaryDirectory(prefix="cdkout-alarms-") as out_dir:
        args = [
            "npx", "cdk", "synth", "cms-staging-ui", "--json",
            "--output", out_dir,
            "-c", f"uiCustomDomain={env.get('UI_CUSTOM_DOMAIN','')}",
            "-c", f"uiCustomDomainCertArn={env.get('UI_CUSTOM_DOMAIN_CERT_ARN','')}",
            "-c", f"uiCustomDomainRegion={env.get('UI_CUSTOM_DOMAIN_REGION','')}",
            "-c", "uiCustomDomainManageDns=false",
            "-c", "cms.allow_unauth_map_auth=true",
            "-c", "cms.allow_self_signup=true",
            *context,
        ]
        proc = subprocess.run(
            args, cwd=_DEPLOYMENT, env=env, capture_output=True, text=True, timeout=900
        )
    # A failed synth is a FAILURE, not a skip. Skipping on a non-zero exit is how a
    # broken template, a firing guard, or a concurrent-synth lock all come out green.
    assert proc.returncode == 0, (
        f"cdk synth failed (exit {proc.returncode}):\n{proc.stderr[-1500:]}"
    )
    return json.loads(proc.stdout[proc.stdout.find("{"):])


@pytest.fixture(scope="module")
def both_gates() -> dict:
    return _synth(
        "-c", "cms.enable_internal_auto_provisioning=true",
        "-c", "cms.enable_external_self_signup=true",
    )


@pytest.fixture(scope="module")
def no_gates() -> dict:
    return _synth()


def _of_type(template: dict, resource_type: str) -> dict[str, dict]:
    return {
        k: v for k, v in template["Resources"].items() if v["Type"] == resource_type
    }


def _alarms(template: dict) -> dict[str, dict]:
    return _of_type(template, "AWS::CloudWatch::Alarm")


def _trigger_lambdas(template: dict) -> dict[str, dict]:
    return {
        k: v
        for k, v in _of_type(template, "AWS::Lambda::Function").items()
        if k.startswith(_TRIGGER_LAMBDA_PREFIXES)
    }


# ---------------------------------------------------------------------------
# The structural invariant
# ---------------------------------------------------------------------------

class TestEveryTriggerIsMonitored:
    def test_both_trigger_lambdas_are_present_when_gated_on(self, both_gates) -> None:
        """Sanity anchor: the invariant below is vacuous if nothing is synthesised."""
        found = _trigger_lambdas(both_gates)
        assert len(found) == 2, f"expected 2 trigger Lambdas, got {sorted(found)}"

    def test_every_trigger_lambda_has_an_errors_alarm(self, both_gates) -> None:
        """Add a third trigger without an alarm and this fails; a count check would not."""
        alarm_targets = set()
        for alarm in _alarms(both_gates).values():
            props = alarm["Properties"]
            if props.get("MetricName") != "Errors":
                continue
            for dim in props.get("Dimensions", []):
                if dim.get("Name") == "FunctionName":
                    alarm_targets.add(json.dumps(dim.get("Value"), sort_keys=True))

        for logical_id in _trigger_lambdas(both_gates):
            ref = json.dumps({"Ref": logical_id}, sort_keys=True)
            assert ref in alarm_targets, (
                f"trigger Lambda {logical_id} has no Errors alarm. An unmonitored "
                f"Cognito trigger fails silently and the failure mode is a sign-in "
                f"outage. Alarmed targets: {sorted(alarm_targets)}"
            )

    def test_errors_alarms_fire_on_a_single_error(self, both_gates) -> None:
        """There is no acceptable rate of failed authentication."""
        for logical_id, alarm in _alarms(both_gates).items():
            props = alarm["Properties"]
            if props.get("MetricName") != "Errors":
                continue
            assert props["Threshold"] == 0, logical_id
            assert props["ComparisonOperator"] == "GreaterThanThreshold", logical_id
            assert props["EvaluationPeriods"] == 1, logical_id


# ---------------------------------------------------------------------------
# No silent alarms
# ---------------------------------------------------------------------------

class TestNoAlarmIsSilent:
    def test_every_alarm_has_a_non_empty_alarm_actions(self, both_gates) -> None:
        """CVX Tier 2 follow-on #14, made executable.

        An alarm with no action is worse than no alarm: it reports a state nobody reads
        while implying the failure would be noticed.
        """
        for logical_id, alarm in _alarms(both_gates).items():
            actions = alarm["Properties"].get("AlarmActions")
            assert actions, f"{logical_id} has empty or missing AlarmActions"

    def test_alarm_actions_point_at_the_operator_topic(self, both_gates) -> None:
        topics = _of_type(both_gates, "AWS::SNS::Topic")
        topic_refs = {json.dumps({"Ref": k}, sort_keys=True) for k in topics}
        for logical_id, alarm in _alarms(both_gates).items():
            # .get, not [], so a missing key fails as an assertion with a useful message
            # rather than a KeyError.
            for action in alarm["Properties"].get("AlarmActions", []):
                assert json.dumps(action, sort_keys=True) in topic_refs, (
                    f"{logical_id} alarms to something that is not an SNS topic in this "
                    f"stack: {action}"
                )

    def test_alarms_treat_missing_data_as_not_breaching(self, both_gates) -> None:
        """A quiet demo stage has no invocations; that is not an incident."""
        for logical_id, alarm in _alarms(both_gates).items():
            assert (
                alarm["Properties"].get("TreatMissingData") == "notBreaching"
            ), logical_id


# ---------------------------------------------------------------------------
# The operator channel exists before it is needed
# ---------------------------------------------------------------------------

class TestOperatorTopic:
    def test_topic_exists_even_with_both_gates_off(self, no_gates) -> None:
        """Subscription is a manual step; a topic that appears only when Phase B is
        enabled is a topic nobody is subscribed to on the day Phase B is enabled."""
        topics = _of_type(no_gates, "AWS::SNS::Topic")
        names = [t["Properties"].get("TopicName") for t in topics.values()]
        assert "cms-staging-ui-security-operators" in names, names

    def test_topic_is_encrypted(self, no_gates) -> None:
        """cdk-nag AwsSolutions-SNS2; matches FlinkAlarmsTopic / SimulationAlarmsTopic."""
        topics = _of_type(no_gates, "AWS::SNS::Topic")
        target = [
            t for t in topics.values()
            if t["Properties"].get("TopicName") == "cms-staging-ui-security-operators"
        ]
        assert target and target[0]["Properties"].get("KmsMasterKeyId"), (
            "operator topic is unencrypted"
        )

    def test_no_trigger_alarms_when_gates_are_off(self, no_gates) -> None:
        """Zero footprint: no alarm for a function that does not exist.

        An alarm on an absent function sits in INSUFFICIENT_DATA forever while telling an
        operator that coverage exists. See decisions.md 2026-08-11 for why this diverges
        from the task text's "alarms exist even when the trigger doesn't".
        """
        assert _trigger_lambdas(no_gates) == {}
        assert _alarms(no_gates) == {}


# ---------------------------------------------------------------------------
# The denial metric filter — the coupling that can go quiet
# ---------------------------------------------------------------------------

class TestDenialMetricFilter:
    def test_metric_filter_exists_on_the_pre_sign_up_log_group(self, both_gates) -> None:
        filters = _of_type(both_gates, "AWS::Logs::MetricFilter")
        assert filters, "no metric filter for pre-sign-up denials"

    def test_filter_pattern_matches_the_handler_token(self, both_gates) -> None:
        """The one coupling in this group that fails silently if it drifts.

        The handler's `_DENIAL_METRIC_TOKEN` is the string this pattern matches. If either
        side changes alone, denials stop being counted, the alarm reports OK forever, and
        nothing errors. Read the token from the handler source rather than hardcoding it
        here, so the two cannot disagree.
        """
        handler_path = os.path.join(
            _DEPLOYMENT, "lambdas", "cognito_triggers", "pre_sign_up", "handler.py"
        )
        with open(handler_path) as fh:
            source = fh.read()
        marker = '_DENIAL_METRIC_TOKEN = "'
        token = source.split(marker, 1)[1].split('"', 1)[0]

        patterns = [
            f["Properties"]["FilterPattern"]
            for f in _of_type(both_gates, "AWS::Logs::MetricFilter").values()
        ]
        assert any(token in p for p in patterns), (
            f"no metric filter matches the handler's denial token {token!r}. "
            f"Patterns present: {patterns}"
        )

    def test_denial_metric_has_a_default_value(self, both_gates) -> None:
        """Without it the alarm sits in INSUFFICIENT_DATA on a quiet stage."""
        for f in _of_type(both_gates, "AWS::Logs::MetricFilter").values():
            transformations = f["Properties"]["MetricTransformations"]
            assert transformations[0].get("DefaultValue") == 0

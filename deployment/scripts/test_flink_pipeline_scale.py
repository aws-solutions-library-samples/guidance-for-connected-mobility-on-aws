#!/usr/bin/env python3
"""Red-phase assertions for the OEM1 Event-Driven Pipeline Scaling spec
(2026-06-17-oem1-event-driven-pipeline-scale, task 1.3).

Asserts against cdk.out/cms-staging-flink.template.json (synthesized output).
All tests FAIL today (pre-implementation); they turn green after task 2.2.

Synth prerequisite:
    cd deployment
    source .venv/bin/activate
    cdk synth cms-staging-flink -o ./cdk.out
"""
from __future__ import annotations

import fnmatch
import json
import sys
import unittest
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).parent.parent / "cdk.out" / "cms-staging-flink.template.json"
)


class FlinkPipelineScaleTest(unittest.TestCase):
    """Assertions for parallelism, log level, SNS alarm topic, and lag alarms."""

    @classmethod
    def setUpClass(cls) -> None:
        if not TEMPLATE_PATH.exists():
            return
        template = json.loads(TEMPLATE_PATH.read_text())
        resources = template["Resources"]
        cls.flink_apps: dict = {
            n: r
            for n, r in resources.items()
            if r["Type"] == "AWS::KinesisAnalyticsV2::Application"
        }
        cls.sns_topics: dict = {
            n: r for n, r in resources.items() if r["Type"] == "AWS::SNS::Topic"
        }
        cls.alarms: dict = {
            n: r for n, r in resources.items() if r["Type"] == "AWS::CloudWatch::Alarm"
        }

    def _skip_if_no_template(self) -> None:
        if not TEMPLATE_PATH.exists():
            self.skipTest(
                f"Synth output not found at {TEMPLATE_PATH}. "
                "Run `cdk synth cms-staging-flink -o ./cdk.out` from deployment/ first."
            )

    def _parallelism(self, resource: dict) -> int:
        ac = resource["Properties"].get("ApplicationConfiguration", {})
        fa = ac.get("FlinkApplicationConfiguration", {})
        return fa.get("ParallelismConfiguration", {}).get("Parallelism", -1)

    def _log_level(self, resource: dict) -> str:
        ac = resource["Properties"].get("ApplicationConfiguration", {})
        fa = ac.get("FlinkApplicationConfiguration", {})
        return fa.get("MonitoringConfiguration", {}).get("LogLevel", "")

    def _app_by_name_fragment(self, fragment: str) -> dict | None:
        for res in self.flink_apps.values():
            app_name = res["Properties"].get("ApplicationName", "")
            if fragment in app_name:
                return res
        return None

    # --- parallelism assertions ---

    def test_trip_processor_parallelism_is_3(self) -> None:
        """trip-processor must have Parallelism == 3 (spec § Design 2)."""
        self._skip_if_no_template()
        trip = self._app_by_name_fragment("trip-processor")
        self.assertIsNotNone(trip, "No app with 'trip-processor' in ApplicationName")
        actual = self._parallelism(trip)
        self.assertEqual(
            actual,
            3,
            f"trip-processor Parallelism: expected 3, got {actual}",
        )

    def test_non_trip_app_parallelism_is_1(self) -> None:
        """At least one non-trip app (safety-processor) must have Parallelism == 1."""
        self._skip_if_no_template()
        safety = self._app_by_name_fragment("safety-processor")
        self.assertIsNotNone(safety, "No app with 'safety-processor' in ApplicationName")
        actual = self._parallelism(safety)
        self.assertEqual(
            actual,
            1,
            f"safety-processor Parallelism: expected 1 (default preserved), got {actual}",
        )

    # --- log level assertions ---

    def test_all_apps_log_level_info(self) -> None:
        """All Flink apps must have MonitoringConfiguration.LogLevel == 'INFO' (spec § Design 3)."""
        self._skip_if_no_template()
        failures: list[str] = []
        for name, res in self.flink_apps.items():
            app_name = res["Properties"].get("ApplicationName", name)
            level = self._log_level(res)
            if level != "INFO":
                failures.append(f"{app_name}: LogLevel={level!r}, expected 'INFO'")
        if failures:
            self.fail(
                "Apps with incorrect LogLevel (expected INFO):\n"
                + "\n".join(f"  {f}" for f in failures)
            )

    # --- SNS alarm topic assertions ---

    def test_flink_alarms_sns_topic_exists(self) -> None:
        """An SNS topic matching 'cms-*-flink-alarms' must exist (spec § Design 4)."""
        self._skip_if_no_template()
        for res in self.sns_topics.values():
            topic_name = res["Properties"].get("TopicName", "")
            if fnmatch.fnmatch(topic_name, "cms-*-flink-alarms"):
                return
        self.fail(
            "No AWS::SNS::Topic with TopicName matching 'cms-*-flink-alarms' found. "
            f"Existing topics: {[r['Properties'].get('TopicName') for r in self.sns_topics.values()]}"
        )

    # --- CloudWatch alarm assertions ---

    def test_trip_processor_lag_alarm_exists(self) -> None:
        """>=1 AWS::CloudWatch::Alarm on 'records_lag_max' for trip-processor must exist (spec § Design 4)."""
        self._skip_if_no_template()
        for res in self.alarms.values():
            props = res["Properties"]
            metric = props.get("MetricName", "")
            alarm_name = props.get("AlarmName", "")
            if metric == "records_lag_max" and "trip" in alarm_name:
                return
        # Check also via Metrics (for metric math alarms)
        for res in self.alarms.values():
            props = res["Properties"]
            alarm_name = props.get("AlarmName", "")
            metrics = props.get("Metrics", [])
            if "trip" in alarm_name and any(
                m.get("MetricStat", {}).get("Metric", {}).get("MetricName") == "records_lag_max"
                for m in metrics
            ):
                return
        alarm_summary = [
            f"{r['Properties'].get('AlarmName','?')}|{r['Properties'].get('MetricName','?')}"
            for r in self.alarms.values()
        ]
        self.fail(
            "No AWS::CloudWatch::Alarm with MetricName='records_lag_max' and 'trip' in AlarmName found.\n"
            f"Existing alarms: {alarm_summary}"
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromTestCase(FlinkPipelineScaleTest))
    sys.exit(0 if result.wasSuccessful() else 1)

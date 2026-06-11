"""Monitor Agent - real-time cost event monitoring and anomaly detection."""


class MonitorAgent:
    """Continuously monitors incoming cost events, checks against thresholds,
    and flags anomalies for downstream investigation."""

    def process_cost_event(self, event):
        """Ingest a single cost event from the cms-cost-events Kafka topic
        and update running aggregates."""
        raise NotImplementedError

    def check_thresholds(self, event):
        """Compare the event against fleet- and vehicle-level SSM threshold
        parameters; emit an alert if exceeded."""
        raise NotImplementedError

    def detect_anomaly(self, event):
        """Apply statistical / ML-based anomaly detection to identify
        cost outliers and publish to cms-cost-anomalies."""
        raise NotImplementedError

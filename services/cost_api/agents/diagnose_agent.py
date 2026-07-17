"""Diagnose Agent - root-cause investigation for cost anomalies."""


class DiagnoseAgent:
    """Investigates flagged cost anomalies by correlating telemetry,
    maintenance history, and external data to produce a diagnosis."""

    def investigate_anomaly(self, anomaly):
        """Orchestrate a full investigation for a detected cost anomaly."""
        raise NotImplementedError

    def query_data_sources(self, anomaly):
        """Query DynamoDB, Iceberg, and Redis for contextual data
        surrounding the anomaly time window."""
        raise NotImplementedError

    def generate_diagnosis(self, anomaly, context):
        """Synthesize queried data into a human-readable diagnosis
        with root-cause attribution."""
        raise NotImplementedError

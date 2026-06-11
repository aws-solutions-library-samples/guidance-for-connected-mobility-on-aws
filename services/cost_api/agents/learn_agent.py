"""Learn Agent - feedback loop for continuous threshold and model improvement."""


class LearnAgent:
    """Reviews outcomes of past recommendations, adjusts detection
    thresholds, and triggers model retraining when drift is detected."""

    def review_outcomes(self, recommendation_id):
        """Compare predicted vs. actual savings for a completed
        recommendation to measure accuracy."""
        raise NotImplementedError

    def update_thresholds(self, fleet_id):
        """Recalculate and update SSM cost-threshold parameters for a
        fleet based on recent outcome data."""
        raise NotImplementedError

    def trigger_retraining(self):
        """Evaluate model drift metrics and trigger SageMaker retraining
        pipeline if accuracy has degraded."""
        raise NotImplementedError

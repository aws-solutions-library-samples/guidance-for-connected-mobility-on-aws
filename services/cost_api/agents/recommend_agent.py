"""Recommend Agent - cost-optimization recommendation generation."""


class RecommendAgent:
    """Generates actionable cost-optimization recommendations based on
    diagnoses, estimates financial impact, and prioritizes by ROI."""

    def generate_recommendation(self, diagnosis):
        """Produce a concrete recommendation from a diagnosis
        (e.g., route change, maintenance schedule, fuel card switch)."""
        raise NotImplementedError

    def estimate_cost_impact(self, recommendation):
        """Estimate the projected savings or cost avoidance if the
        recommendation is implemented."""
        raise NotImplementedError

    def prioritize(self, recommendations):
        """Rank a list of recommendations by estimated ROI and urgency."""
        raise NotImplementedError

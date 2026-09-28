"""
Red-phase tests for the provenance contract — T1.3.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D1

These tests MUST FAIL until Group 2 (T2.1) implements
services/fleet_intelligence/provenance.py.  That is the deliverable for this task.

Contract (from § D1):
  - Four valid provenance values: "simulated", "measured", "derived", "reference"
  - `derived` inherits the *weakest* provenance of its inputs.
    Weakness order (weakest first): simulated > reference > derived > measured
    A result derived from ANY simulated input is "simulated", not "derived".
  - An input with an unknown provenance value is rejected (raises ValueError or similar).
  - An input with a *missing* provenance is rejected, not silently defaulted.

Negative control (required by T1.3 Constraints):
  test_derived_negative_control_rejects_silent_measured_default asserts that
  weakest_provenance(["simulated", "measured", "measured"]) == "simulated", NOT "measured".
  If weakest_provenance silently defaulted missing/short-circuit cases to "measured",
  the bug (unlabelled simulated data reaching a screen) would be invisible to this suite.
  This control makes that failure mode detectable.
"""
import pytest


def _import_provenance():
    """
    Lazy import helper. Each test calls this so the suite COLLECTS fully
    (giving a FAILED count rather than a single ERROR) and fails per-test
    until provenance.py is created in T2.1.
    """
    try:
        from services.fleet_intelligence import provenance as _prov  # noqa: F401
        return _prov
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/provenance.py does not exist yet — "
            f"implement it in T2.1 (T1.3 red phase): {exc}"
        )


def _get(symbol):
    """Return a named attribute from provenance, failing if the module is absent."""
    mod = _import_provenance()
    if not hasattr(mod, symbol):
        pytest.fail(
            f"provenance.py exists but does not define '{symbol}' — "
            f"implement it in T2.1"
        )
    return getattr(mod, symbol)


# ---------------------------------------------------------------------------
# § D1 — the four allowed provenance values
# ---------------------------------------------------------------------------

class TestValidProvenanceValues:
    """§ D1: only four string values are valid."""

    def test_all_four_values_are_defined(self):
        VALID_PROVENANCE_VALUES = _get("VALID_PROVENANCE_VALUES")
        assert set(VALID_PROVENANCE_VALUES) == {"simulated", "measured", "derived", "reference"}

    def test_simulated_is_valid(self):
        validate_provenance = _get("validate_provenance")
        assert validate_provenance("simulated") == "simulated"

    def test_measured_is_valid(self):
        validate_provenance = _get("validate_provenance")
        assert validate_provenance("measured") == "measured"

    def test_derived_is_valid(self):
        validate_provenance = _get("validate_provenance")
        assert validate_provenance("derived") == "derived"

    def test_reference_is_valid(self):
        validate_provenance = _get("validate_provenance")
        assert validate_provenance("reference") == "reference"

    def test_unknown_value_is_rejected(self):
        """§ D1: an unknown provenance value is rejected — not silently accepted."""
        validate_provenance = _get("validate_provenance")
        with pytest.raises((ValueError, KeyError, TypeError)):
            validate_provenance("inferred")

    def test_empty_string_is_rejected(self):
        """§ D1: empty string is not a valid provenance value."""
        validate_provenance = _get("validate_provenance")
        with pytest.raises((ValueError, KeyError, TypeError)):
            validate_provenance("")

    def test_none_is_rejected(self):
        """§ D1: None is not a valid provenance value — no defaulting."""
        validate_provenance = _get("validate_provenance")
        with pytest.raises((ValueError, KeyError, TypeError, AttributeError)):
            validate_provenance(None)


# ---------------------------------------------------------------------------
# § D1 — weakest-input inheritance for `derived` records
# ---------------------------------------------------------------------------

class TestWeakestProvenanceInheritance:
    """
    § D1: 'A CPM figure derived from simulated cost rows is "simulated", not "derived".
    Weakest-input inheritance is the rule that makes the label honest rather than laundering.'

    Weakness order (weakest first): simulated > reference > derived > measured
    Any mix that includes a simulated input must return "simulated".
    """

    def test_all_measured_inputs_yields_measured(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["measured", "measured", "measured"])
        assert result == "measured"

    def test_simulated_plus_measured_yields_simulated(self):
        """§ D1 key case: one simulated input poisons the whole result."""
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["simulated", "measured"])
        assert result == "simulated"

    def test_simulated_plus_reference_yields_simulated(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["simulated", "reference"])
        assert result == "simulated"

    def test_reference_plus_measured_yields_reference(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["reference", "measured"])
        assert result == "reference"

    def test_derived_plus_measured_yields_derived(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["derived", "measured"])
        assert result == "derived"

    def test_simulated_beats_derived(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["simulated", "derived"])
        assert result == "simulated"

    def test_single_simulated_input_yields_simulated(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["simulated"])
        assert result == "simulated"

    def test_single_measured_input_yields_measured(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["measured"])
        assert result == "measured"

    def test_three_inputs_simulated_measured_measured(self):
        """
        § D1 explicit case: 'a value derived from one simulated and two measured inputs
        is simulated, not derived'.
        """
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["simulated", "measured", "measured"])
        assert result == "simulated"

    def test_all_reference_inputs_yields_reference(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["reference", "reference"])
        assert result == "reference"

    def test_mixed_three_ways_dominated_by_simulated(self):
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["measured", "reference", "simulated"])
        assert result == "simulated"


# ---------------------------------------------------------------------------
# § D1 — missing or unknown inputs are rejected (not defaulted)
# ---------------------------------------------------------------------------

class TestMissingProvenanceRejection:
    """
    § D1 + T2.1 Constraints: 'A record without provenance raises; it does not become
    measured. Defaulting is how an unlabelled value reaches a screen.'
    """

    def test_missing_provenance_in_inputs_is_rejected(self):
        weakest_provenance = _get("weakest_provenance")
        with pytest.raises((ValueError, KeyError, TypeError, AttributeError)):
            weakest_provenance(["measured", None, "measured"])

    def test_unknown_provenance_in_inputs_is_rejected(self):
        weakest_provenance = _get("weakest_provenance")
        with pytest.raises((ValueError, KeyError, TypeError)):
            weakest_provenance(["measured", "inferred"])

    def test_empty_input_list_is_rejected(self):
        weakest_provenance = _get("weakest_provenance")
        with pytest.raises((ValueError, IndexError)):
            weakest_provenance([])


# ---------------------------------------------------------------------------
# NEGATIVE CONTROL — T1.3 Constraints (required)
# ---------------------------------------------------------------------------

class TestNegativeControl:
    """
    T1.3 Constraints: 'Include an explicit negative control: a case that would PASS if
    inheritance silently defaulted to "measured". Without it the suite cannot fail the way
    the real defect fails.'

    The defect: a CPM figure derived from simulated cost rows is labelled "derived" or
    "measured" rather than "simulated". This happens if weakest_provenance() treats any
    short-circuit or default as "measured".

    This test makes that failure mode detectable: if weakest_provenance(["simulated",
    "measured", "measured"]) returned "measured", this assertion FAILS and the bug is caught.
    The CORRECT result is "simulated".
    """

    def test_derived_negative_control_rejects_silent_measured_default(self):
        """
        § D1: 'A CPM figure derived from simulated cost rows is "simulated", not "derived".'

        Negative control: the result of weakest_provenance(["simulated", "measured", "measured"])
        MUST be "simulated".

        If it returned "measured" → silent-default bug (unlabelled simulated data on screen).
        If it returned "derived" → laundering bug (simulated inputs hidden under "derived").
        Both are the defect this spec closes.
        """
        weakest_provenance = _get("weakest_provenance")
        result = weakest_provenance(["simulated", "measured", "measured"])

        # The result MUST be "simulated":
        assert result == "simulated", (
            f"Expected 'simulated' (weakest-input wins) but got '{result}'. "
            "This is the silent-default bug: an input derived from simulated cost data "
            "was labelled as if it were measured or derived."
        )
        # Negative assertion 1 — the silent-default bug value:
        assert result != "measured", (
            "Silent-default bug: weakest_provenance() returned 'measured'. "
            "Simulated data would reach the screen unlabelled (§ D1)."
        )
        # Negative assertion 2 — the laundering bug value:
        assert result != "derived", (
            "Laundering bug: a result derived from simulated input returned 'derived'. "
            "§ D1: 'Weakest-input inheritance is the rule that makes the label honest "
            "rather than laundering.'"
        )

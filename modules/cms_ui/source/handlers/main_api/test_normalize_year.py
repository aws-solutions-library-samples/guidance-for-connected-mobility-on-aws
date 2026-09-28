"""`year` must reach DynamoDB as one consistent type, or sorting 500s.

The defect this closes: `year` was written as whatever the caller sent, and the
create path defaulted a missing value to `''` — an empty **String**. One String
among 154 Numbers made `sortBy=year` raise

    TypeError: '<' not supported between instances of 'str' and 'decimal.Decimal'

and return HTTP 500 for the *entire* vehicles list, because the exception escapes
before pagination. See issues/2026-09-23-vehicle-year-mixed-type-breaks-sort/.

`_sort_key` was hardened to tolerate a mixed type, but tolerance is a safety net,
not the fix — these tests pin the write boundary so the mix cannot be created.

The property under test is NOT "the function returns a number". It is:
**nothing this function returns can ever be a str, a bool, or a NULL-producing
None-as-value.** A guard that merely coerced the happy path would pass a
presence check and still let `''` through.

Run:
    pytest modules/cms_ui/source/handlers/main_api/test_normalize_year.py -v
"""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("main_api_year", _HERE / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IDX = _load()
_normalize_year = IDX._normalize_year


# ---------------------------------------------------------------------------
# Real values normalise to int, whatever shape they arrive in.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (2023, 2023),
    ("2023", 2023),            # form values arrive as text; the one silent conversion
    ("  2023  ", 2023),        # whitespace is the caller's, not the data's
    (Decimal("2023"), 2023),   # what a DynamoDB round-trip hands back
    (2023.0, 2023),
])
def test_usable_values_become_int(raw, expected):
    out = _normalize_year(raw)
    assert out == expected
    assert type(out) is int, f"{raw!r} produced {type(out).__name__}, not int"


# ---------------------------------------------------------------------------
# The exact value that caused the outage.
# ---------------------------------------------------------------------------

def test_empty_string_does_not_become_a_stored_empty_string():
    # `entry.get('year', '')` was the create-path default. This is the single
    # value that manufactured the mixed-type state.
    assert _normalize_year("") is None


@pytest.mark.parametrize("raw", ["", "   ", None, "unknown", "20x3", [], {}, object()])
def test_unusable_values_return_none_meaning_omit(raw):
    assert _normalize_year(raw) is None


def test_bool_is_not_a_model_year():
    # bool subclasses int, so a naive int() check accepts True as 1.
    assert _normalize_year(True) is None
    assert _normalize_year(False) is None


# ---------------------------------------------------------------------------
# The invariant that actually prevents the 500.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    2023, "2023", "  2023  ", Decimal("2023"), 2023.0,
    "", "   ", None, "unknown", "20x3", True, False, [], {},
])
def test_never_returns_a_str_or_bool(raw):
    out = _normalize_year(raw)
    assert out is None or type(out) is int, (
        f"{raw!r} produced {type(out).__name__}. Anything other than int-or-None "
        "can re-create the mixed-type state that 500s sortBy=year."
    )
    assert not isinstance(out, str)
    assert not isinstance(out, bool)


def test_none_means_omit_not_write_null():
    # Documents intent that callers must honour: None is a signal to leave the
    # attribute OFF the item. Writing it as a value stores a DynamoDB NULL,
    # which is a third type and makes the original problem worse.
    assert _normalize_year(None) is None
    assert _normalize_year("") is None


# ---------------------------------------------------------------------------
# Call sites: the helper is worthless if a writer bypasses it.
# ---------------------------------------------------------------------------

def test_create_path_does_not_default_year_to_empty_string():
    src = (_HERE / "index.py").read_text()
    assert "'year': entry.get('year', '')" not in src, (
        "The create path is defaulting year to an empty string again — this is "
        "the original defect verbatim."
    )


def test_both_write_paths_go_through_the_normaliser():
    src = (_HERE / "index.py").read_text()
    # Update path must not assign the raw caller value.
    assert "expression_values[':year'] = entry['year']" not in src, (
        "The update path is writing the caller's raw year type again."
    )
    # And the normaliser is actually invoked on both paths.
    assert src.count("_normalize_year(") >= 3, (
        "Expected the definition plus a call on each of the create and update "
        f"paths; found {src.count('_normalize_year(')} occurrences."
    )

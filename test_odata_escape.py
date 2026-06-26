"""
test_odata_escape.py - Offline tests for fetchers._odata_escape.

Runs without SF credentials. Verifies OData v2 $filter escaping is correct
(prevents injection) and byte-identical for normal SuccessFactors codes, so
the live two-phase fetch behaviour is unchanged for real data.

Usage:
    python test_odata_escape.py      # or: pytest test_odata_escape.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fetchers import _odata_escape


def test_normal_codes_unchanged():
    for code in ["CAN", "USA", "POS_000123", "JC-1000", "1000123"]:
        assert _odata_escape(code) == code


def test_single_quote_doubled():
    assert _odata_escape("O'Brien") == "O''Brien"


def test_injection_attempt_neutralised():
    raw = "x' or externalCode ne 'y"
    literal = f"externalCode eq '{_odata_escape(raw)}'"
    assert literal == "externalCode eq 'x'' or externalCode ne ''y'"


def test_batch_clause_still_valid():
    batch = ["A", "O'Hara", "B"]
    clause = " or ".join(f"externalCode eq '{_odata_escape(c)}'" for c in batch)
    assert clause == ("externalCode eq 'A' or externalCode eq 'O''Hara' or externalCode eq 'B'")


def test_non_string_coerced():
    assert _odata_escape(123) == "123"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))

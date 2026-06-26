"""
test_validators.py - Offline tests for validators.py pure helpers.

Covers the correctness-critical, network-free logic: SF date parsing,
effective-dating / active-status checks, and value extraction. Runs without
SF credentials or a database.

Usage:
    pytest test_validators.py        # or: python test_validators.py
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from validators import _is_active_on_date, _parse_date, _val


class TestParseDate:
    def test_none_and_blank(self):
        assert _parse_date(None) is None
        assert _parse_date("") is None
        assert _parse_date("   ") is None

    def test_iso_date(self):
        assert _parse_date("2026-06-13") == datetime.date(2026, 6, 13)

    def test_iso_datetime_truncated_to_date(self):
        assert _parse_date("2026-06-13T10:30:00") == datetime.date(2026, 6, 13)

    def test_sf_epoch_millis(self):
        # 2021-01-01 UTC in epoch millis.
        ms = int(
            datetime.datetime(2021, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        )
        assert _parse_date(f"/Date({ms})/") == datetime.date(2021, 1, 1)

    def test_sf_epoch_with_offset_suffix(self):
        ms = int(
            datetime.datetime(2021, 1, 1, tzinfo=datetime.UTC).timestamp()
            * 1000
        )
        # SF often appends a timezone offset, e.g. /Date(1609459200000+0000)/
        assert _parse_date(f"/Date({ms}+0000)/") == datetime.date(2021, 1, 1)

    def test_invalid_returns_none(self):
        assert _parse_date("not-a-date") is None
        assert _parse_date("/Date(abc)/") is None


class TestIsActiveOnDate:
    def _rec(self, start, end, status="A"):
        return {"startDate": start, "endDate": end, "status": status}

    def test_active_within_window(self):
        rec = self._rec("2026-01-01", "2026-12-31")
        assert _is_active_on_date(rec, datetime.date(2026, 6, 13)) is True

    def test_open_ended_end_date_is_active(self):
        rec = self._rec("2026-01-01", None)
        assert _is_active_on_date(rec, datetime.date(2026, 6, 13)) is True

    def test_before_start_is_inactive(self):
        rec = self._rec("2026-07-01", "2026-12-31")
        assert _is_active_on_date(rec, datetime.date(2026, 6, 13)) is False

    def test_after_end_is_inactive(self):
        rec = self._rec("2026-01-01", "2026-03-31")
        assert _is_active_on_date(rec, datetime.date(2026, 6, 13)) is False

    def test_inactive_status_excluded(self):
        rec = self._rec("2026-01-01", "2026-12-31", status="I")
        assert _is_active_on_date(rec, datetime.date(2026, 6, 13)) is False

    def test_missing_start_is_inactive(self):
        rec = self._rec(None, "2026-12-31")
        assert _is_active_on_date(rec, datetime.date(2026, 6, 13)) is False


class TestVal:
    def test_strips_whitespace(self):
        assert _val({"x": "  hi  "}, "x") == "hi"

    def test_blank_becomes_none(self):
        assert _val({"x": "   "}, "x") is None

    def test_missing_key_is_none(self):
        assert _val({}, "x") is None

    def test_non_string_coerced(self):
        assert _val({"x": 123}, "x") == "123"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))

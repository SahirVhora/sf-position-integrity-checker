"""Regression tests for refresh failure safety."""

from pathlib import Path

import database as db
import fetchers
import pytest


def _use_isolated_database(tmp_path, monkeypatch):
    live_path = tmp_path / "sf_integrity_TEST.db"
    monkeypatch.setattr(db, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(live_path))
    db.init_db()
    return live_path


def _seed_completed_extract():
    db.save_positions([{"code": "KEEP-ME", "effectiveStatus": "A"}])
    db.save_extract_meta("TEST", 1, complete=True)


def _fail_after_destructive_reset(*args, **kwargs):
    # Simulate the existing destructive initialization followed by a
    # failure in a network/parser/database phase.
    db.init_db()
    raise RuntimeError("simulated refresh failure")


def test_failed_refresh_restores_previous_completed_extract(tmp_path, monkeypatch):
    """A failed refresh must not destroy the last known-good cache."""
    live_path = _use_isolated_database(tmp_path, monkeypatch)
    _seed_completed_extract()
    monkeypatch.setattr(fetchers, "_run_full_extract", _fail_after_destructive_reset)

    with pytest.raises(RuntimeError, match="simulated refresh failure"):
        fetchers.run_full_extract("TEST")

    assert [row["code"] for row in db.load_table("positions")] == ["KEEP-ME"]
    meta = db.get_latest_extract_meta()
    assert meta is not None
    assert meta["country"] == "TEST"
    assert meta["positions_fetched"] == 1
    assert meta["extract_complete"] == 1
    assert not list(Path(live_path).parent.glob(f".{live_path.name}.*.refresh-backup"))


def test_empty_refresh_restores_previous_completed_extract(tmp_path, monkeypatch):
    """A zero-position refresh must preserve the last known-good cache."""
    live_path = _use_isolated_database(tmp_path, monkeypatch)
    _seed_completed_extract()
    monkeypatch.setattr(fetchers, "_run_full_extract", lambda *args, **kwargs: {"positions": 0})

    assert fetchers.run_full_extract("TEST") == {"positions": 0}
    assert [row["code"] for row in db.load_table("positions")] == ["KEEP-ME"]
    assert db.get_latest_extract_meta()["extract_complete"] == 1
    assert not list(Path(live_path).parent.glob(f".{live_path.name}.*.refresh-backup"))

"""Regression tests for local credential-file permissions."""

from __future__ import annotations

import json
import stat

import config


def test_file_credential_fallback_is_owner_only(tmp_path, monkeypatch):
    credentials_path = tmp_path / "config" / "credentials.json"
    monkeypatch.setattr(config, "_CREDS_FILE", str(credentials_path))

    config._save_file_creds({"username": "synthetic-user", "password": "synthetic-secret"})

    assert credentials_path.read_text(encoding="utf-8")
    saved = json.loads(credentials_path.read_text(encoding="utf-8"))
    assert saved["username"] == "synthetic-user"
    assert "password" not in saved
    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600


def test_env_file_is_owner_only(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "_env_file_path", lambda: str(env_path))

    config._write_env_var("SF_PASSWORD", "synthetic-secret")

    assert "SF_PASSWORD" in env_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

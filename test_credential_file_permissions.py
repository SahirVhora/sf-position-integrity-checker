"""Regression coverage for the plaintext credential fallback's permissions."""

import stat

import config


def test_file_credential_fallback_is_created_mode_600(tmp_path, monkeypatch):
    creds_file = tmp_path / "config" / "credentials.json"
    monkeypatch.setattr(config, "_CREDS_FILE", str(creds_file))

    config._save_file_creds({"password": "test-only-value"})

    assert creds_file.exists()
    assert stat.S_IMODE(creds_file.stat().st_mode) == 0o600
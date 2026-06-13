"""
test_auth_basic.py - Offline tests for auth/basic.py credential resolution.

Verifies the non-interactive import behaviour (regression test for the
import-time credential prompt that used to block CI / MCP / web startup) and
the Basic Auth header encoding. Runs without a keyring, TTY, or network.

Usage:
    pytest test_auth_basic.py
"""

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

from auth.basic import get_basic_auth_headers, resolve_basic_credentials

_SF_VARS = (
    "SF_ODATA_BASE_URL",
    "SF_BASE_URL",
    "SF_USERNAME",
    "SF_PASSWORD",
    "SF_COMPANY_ID",
    "SF_INSTANCE_ID",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all SF_* credential vars so resolution falls through to prompt/empty."""
    for var in _SF_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestResolveNoPrompt:
    def test_no_creds_no_prompt_returns_empty(self, clean_env):
        # The core fix: prompt=False must never call input() - returns empties.
        url, user, pwd, company = resolve_basic_credentials(prompt=False)
        assert (url, user, pwd, company) == ("", "", "", "")

    def test_env_creds_resolved(self, clean_env):
        clean_env.setenv("SF_ODATA_BASE_URL", "https://api4.successfactors.com")
        clean_env.setenv("SF_USERNAME", "apiuser")
        clean_env.setenv("SF_PASSWORD", "secret")
        clean_env.setenv("SF_COMPANY_ID", "ACME")
        url, user, pwd, company = resolve_basic_credentials(prompt=False)
        assert user == "apiuser"
        assert pwd == "secret"
        assert company == "ACME"

    def test_legacy_env_var_names(self, clean_env):
        clean_env.setenv("SF_BASE_URL", "https://api4.successfactors.com")
        clean_env.setenv("SF_USERNAME", "apiuser")
        clean_env.setenv("SF_PASSWORD", "secret")
        clean_env.setenv("SF_INSTANCE_ID", "ACME")
        url, user, pwd, company = resolve_basic_credentials(prompt=False)
        assert company == "ACME"


class TestBasicAuthHeaders:
    def test_header_encodes_user_at_company(self, clean_env):
        clean_env.setenv("SF_ODATA_BASE_URL", "https://api4.successfactors.com")
        clean_env.setenv("SF_USERNAME", "apiuser")
        clean_env.setenv("SF_PASSWORD", "secret")
        clean_env.setenv("SF_COMPANY_ID", "ACME")
        headers = get_basic_auth_headers()
        token = headers["Authorization"].split(" ", 1)[1]
        decoded = base64.b64decode(token).decode()
        assert decoded == "apiuser@ACME:secret"

    def test_company_derived_from_username(self, clean_env):
        clean_env.setenv("SF_ODATA_BASE_URL", "https://api4.successfactors.com")
        clean_env.setenv("SF_USERNAME", "apiuser@ACME")
        clean_env.setenv("SF_PASSWORD", "secret")
        headers = get_basic_auth_headers()
        decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
        assert decoded == "apiuser@ACME:secret"


def test_config_imports_without_prompt_when_unconfigured(clean_env, capsys):
    """Importing config with no creds and no TTY must not raise or block."""
    import importlib

    import config

    importlib.reload(config)
    # HEADERS stays empty rather than triggering an interactive prompt.
    assert config.HEADERS == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

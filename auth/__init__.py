"""
auth/__init__.py — Auth method dispatcher for SF Position Integrity Checker.
"""

from auth.basic import get_basic_auth_headers
from auth.oauth2 import get_oauth2_headers
import config


def get_auth_headers() -> dict:
    """
    Return the correct auth headers based on SF_AUTH_METHOD env var.
    Defaults to 'basic' if SF_AUTH_METHOD is not set.
    """
    method = config.AUTH_METHOD  # "basic" or "oauth2"
    if method == "oauth2":
        return get_oauth2_headers()
    return get_basic_auth_headers()

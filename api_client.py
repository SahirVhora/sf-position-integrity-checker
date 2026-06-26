"""
api_client.py - OData v2 API client with pagination, retry, and error handling.

Now backed by sapsf_shared.SFClient for consistent retry/pagination/auth.
Public API preserved for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

import config

from sapsf_shared import AuthConfig, SFClient, SFClientError

logger = logging.getLogger(__name__)


def _build_auth_config() -> AuthConfig:
    """Build an AuthConfig from the current config state."""
    cfg = AuthConfig(
        base_url=config.SF_BASE_URL,
        username=config.SF_USERNAME,
        password=config.SF_PASSWORD,
        company_id=config.SF_INSTANCE_ID,
        auth_type=config.AUTH_METHOD,
        timeout_sec=60,
    )
    if config.AUTH_METHOD == "oauth2":
        cfg.client_id = config.OAUTH2_CLIENT_ID
        cfg.client_secret = ""  # SF PIC uses JWT, not client_secret
        cfg.company_id = config.OAUTH2_COMPANY_ID
        cfg.token_url = config.OAUTH2_TOKEN_URL
    return cfg


def _get_client() -> SFClient:
    """Create an SFClient from current config. Reuse where possible."""
    cfg = _build_auth_config()
    return SFClient(cfg, default_top=config.PAGE_SIZE)


def _build_url(entity: str, params: dict[str, str]) -> str:
    """Construct the full OData v2 URL with query parameters.

    Preserved for backward compatibility. New code should use SFClient directly.
    """
    from urllib.parse import urlencode
    params["$format"] = "json"
    query_string = urlencode(params, safe="$,'() ")
    return f"{config.ODATA_BASE_URL}{entity}?{query_string}"


def _get_with_retry(url: str, entity: str) -> dict[str, Any]:
    """Perform a GET request with retry on 5xx / timeout.

    Now delegates to sapsf_shared.SFClient internals for consistent retry behaviour.
    Preserved for backward compatibility.
    """
    try:
        client = _get_client()
        resp = client._request_with_retry("GET", url)
        client._check_response(resp, url)
        return resp.json()
    except SFClientError as exc:
        if exc.status_code == 401:
            raise RuntimeError(
                "Authentication failed - check SF_INSTANCE_ID, SF_USERNAME "
                "and SF_PASSWORD in .env"
            ) from exc
        if exc.status_code == 403:
            raise RuntimeError(
                f"Access denied on {entity} - check API user permissions"
            ) from exc
        raise RuntimeError(
            f"Failed to fetch {entity}: {exc}"
        ) from exc


def fetch_all(
    entity: str,
    select_fields: list[str] | None = None,
    filter_expr: str | None = None,
    expand_fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all records for an OData entity with automatic pagination.

    Now backed by sapsf_shared.SFClient.get() for consistent pagination,
    retry, and error handling.

    expand_fields: navigation properties to $expand
    Returns a flat list of record dicts.
    """
    try:
        client = _get_client()
        records = client.get(
            entity,
            top=config.PAGE_SIZE,
            select=select_fields,
            expand=expand_fields,
            filter_expr=filter_expr,
        )
        logger.info("Fetched %d records from %s", len(records), entity)
        return records
    except SFClientError as exc:
        if exc.status_code == 401:
            raise RuntimeError(
                "Authentication failed - check SF credentials in .env"
            ) from exc
        if exc.status_code == 403:
            raise RuntimeError(
                f"Access denied on {entity} - check API user permissions"
            ) from exc
        raise RuntimeError(
            f"Failed to fetch {entity}: {exc}"
        ) from exc

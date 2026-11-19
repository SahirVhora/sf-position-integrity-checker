"""Batched job sub function lookup and shared-client reuse."""

from __future__ import annotations

from unittest.mock import patch

import api_client
import fetchers

JC_A = {"externalCode": "A", "startDate": "/Date(946684800000)/"}
JC_B = {"externalCode": "B", "startDate": "/Date(946684800000)/"}
JC_C = {"externalCode": "C", "startDate": "/Date(946684800000)/"}


def _bulk_row(code, start, nav):
    return {"externalCode": code, "startDate": start, "cust_jobsubfunction": nav}


def test_batched_query_resolves_without_per_record_calls():
    rows = [
        _bulk_row("A", JC_A["startDate"], {"externalCode": "SF-A"}),
        _bulk_row("B", JC_B["startDate"], None),
    ]
    with (
        patch("api_client.fetch_all", return_value=rows) as mock_fetch,
        patch("api_client._get_with_retry") as mock_get,
    ):
        result = fetchers.fetch_jobcode_subfunctions([JC_A, JC_B])

    assert result == {"A": "SF-A", "B": None}
    mock_fetch.assert_called_once()
    assert (
        "externalCode eq 'A' or externalCode eq 'B'" in mock_fetch.call_args.kwargs["filter_expr"]
    )
    mock_get.assert_not_called()


def test_unmatched_and_deferred_records_use_per_record_lookup():
    rows = [
        _bulk_row("A", JC_A["startDate"], {"externalCode": "SF-A"}),
        _bulk_row("B", JC_B["startDate"], {"__deferred": {"uri": "x"}}),
        # C missing: different effective date than the requested record
        _bulk_row("C", "/Date(0)/", {"externalCode": "OLD"}),
    ]
    with (
        patch("api_client.fetch_all", return_value=rows),
        patch(
            "api_client._get_with_retry",
            side_effect=lambda url, entity: {"d": {"externalCode": f"NAV-{entity[-1]}"}},
        ) as mock_get,
    ):
        result = fetchers.fetch_jobcode_subfunctions([JC_A, JC_B, JC_C])

    assert result == {"A": "SF-A", "B": "NAV-B", "C": "NAV-C"}
    assert mock_get.call_count == 2


def test_batched_query_failure_falls_back_to_per_record_lookup():
    with (
        patch("api_client.fetch_all", side_effect=RuntimeError("400")),
        patch(
            "api_client._get_with_retry", return_value={"d": {"externalCode": "NAV"}}
        ) as mock_get,
    ):
        result = fetchers.fetch_jobcode_subfunctions([JC_A, JC_B])

    assert result == {"A": "NAV", "B": "NAV"}
    assert mock_get.call_count == 2


def test_client_is_reused_until_settings_change(monkeypatch):
    monkeypatch.setattr(api_client, "_client", None)
    monkeypatch.setattr(api_client, "_client_key", None)
    monkeypatch.setattr(api_client.config, "SF_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(api_client.config, "SF_USERNAME", "user1")
    monkeypatch.setattr(api_client.config, "SF_PASSWORD", "pw")
    monkeypatch.setattr(api_client.config, "AUTH_METHOD", "basic")

    first = api_client._get_client()
    assert api_client._get_client() is first

    monkeypatch.setattr(api_client.config, "SF_USERNAME", "user2")
    second = api_client._get_client()
    assert second is not first
    assert second.config.username == "user2"
    second.close()

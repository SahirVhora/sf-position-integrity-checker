"""
api_client.py - OData v2 API client with pagination, retry, and error handling.
"""

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

import config
from auth import get_auth_headers


def _build_url(entity: str, params: Dict[str, str]) -> str:
    """Construct the full OData v2 URL with query parameters."""
    params["$format"] = "json"
    query_string = urlencode(params, safe="$,'() ")
    return f"{config.ODATA_BASE_URL}{entity}?{query_string}"


def _get_with_retry(url: str, entity: str) -> Dict[str, Any]:
    """
    Perform a GET request with exponential backoff retry on 5xx / timeout.
    Raises on 401 / 403 with a clear message.
    """
    delays = [2, 4, 8]
    last_exc: Optional[Exception] = None

    for attempt, delay in enumerate(delays, start=1):
        try:
            response = requests.get(
                url,
                headers={
                    **get_auth_headers(),
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=60,
            )

            if response.status_code == 401:
                raise RuntimeError(
                    "Authentication failed - check SF_INSTANCE_ID, SF_USERNAME "
                    "and SF_PASSWORD in .env"
                )
            if response.status_code == 403:
                raise RuntimeError(
                    f"Access denied on {entity} - check API user permissions"
                )
            if response.status_code >= 500:
                print(
                    f"  [WARN] Server error {response.status_code} on {entity} "
                    f"(attempt {attempt}/{len(delays)}), retrying in {delay}s..."
                )
                last_exc = RuntimeError(f"HTTP {response.status_code} from {entity}")
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            print(
                f"  [WARN] Timeout on {entity} "
                f"(attempt {attempt}/{len(delays)}), retrying in {delay}s..."
            )
            last_exc = TimeoutError(f"Request timed out for {entity}")
            time.sleep(delay)
        except RuntimeError:
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            print(
                f"  [WARN] Request error on {entity}: {exc} "
                f"(attempt {attempt}/{len(delays)}), retrying in {delay}s..."
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Failed to fetch {entity} after {len(delays)} attempts. Last error: {last_exc}"
    )


def fetch_all(
    entity: str,
    select_fields: Optional[List[str]] = None,
    filter_expr: Optional[str] = None,
    expand_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch all records for an OData entity with automatic pagination.

    expand_fields: navigation properties to $expand (e.g. ["cust_legalEntity"]).
                   These return as inline nested objects rather than __deferred links.

    Returns a flat list of record dicts.
    """
    all_records: List[Dict[str, Any]] = []
    page = 1
    skip = 0

    while True:
        params: Dict[str, str] = {
            "$top": str(config.PAGE_SIZE),
            "$skip": str(skip),
        }
        if select_fields:
            # OData v2: expanded nav props must also appear in $select to be returned
            if expand_fields:
                combined = list(select_fields) + [
                    f for f in expand_fields if f not in select_fields
                ]
                params["$select"] = ",".join(combined)
            else:
                params["$select"] = ",".join(select_fields)
        if filter_expr:
            params["$filter"] = filter_expr
        if expand_fields:
            params["$expand"] = ",".join(expand_fields)

        url = _build_url(entity, params)
        data = _get_with_retry(url, entity)

        # OData v2 wraps results in d.results
        results = data.get("d", {}).get("results", [])
        count = len(results)

        print(f"  Fetching {entity} (page {page})... got {count} records")
        all_records.extend(results)

        if count < config.PAGE_SIZE:
            break

        skip += config.PAGE_SIZE
        page += 1

    return all_records

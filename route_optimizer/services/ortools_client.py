# -*- coding: utf-8 -*-
"""HTTP client for the external OR-Tools VRP microservice."""
import json
import urllib.error
import urllib.request


class OrtoolsServiceError(Exception):
    """Raised when the OR-Tools service returns an error or invalid payload."""


def _post_json(url, payload, timeout, api_key=None):
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Odoo-route-optimizer",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-API-KEY"] = api_key
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        raise OrtoolsServiceError(
            f"OR-Tools HTTP {e.code}: {e.reason}. {err_body[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise OrtoolsServiceError(f"OR-Tools connection error: {e.reason}") from e

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise OrtoolsServiceError("OR-Tools response is not valid JSON.") from e


def solve_vrp(service_url, payload, timeout=60, api_key=None):
    """
    POST JSON payload to the OR-Tools service (extended VRP contract).

    Expected response (minimum):
        {"success": true, "ordered_picking_ids": [<int>, ...]}
    or routes with node_indices, etc.
    """
    url = (service_url or "").strip()
    if not url:
        raise OrtoolsServiceError("OR-Tools service URL is not configured.")

    result = _post_json(url, payload, timeout, api_key=api_key)

    if result.get("success") is False:
        raise OrtoolsServiceError(result.get("error") or "OR-Tools service reported failure.")

    return result


def solve_simple_distance_api(service_url, locations, distance_matrix_int, timeout=60, api_key=None):
    """
    POST to a minimal service like::

        {"locations": [...], "distance_matrix": [[int, ...], ...]}

    Expects a response with ``optimized_route``: list of location labels in visit order
    (same strings as in ``locations``, depot may appear at start/end).
    """
    url = (service_url or "").strip()
    if not url:
        raise OrtoolsServiceError("OR-Tools service URL is not configured.")

    payload = {
        "locations": locations,
        "distance_matrix": distance_matrix_int,
    }
    result = _post_json(url, payload, timeout, api_key=api_key)

    if result.get("success") is False:
        raise OrtoolsServiceError(result.get("error") or "OR-Tools service reported failure.")

    return result

# -*- coding: utf-8 -*-
"""HTTP client for the external OR-Tools VRP microservice."""
import json
import urllib.error
import urllib.request


class OrtoolsServiceError(Exception):
    """Raised when the OR-Tools service returns an error or invalid payload."""


def solve_vrp(service_url, payload, timeout=60):
    """
    POST JSON payload to the OR-Tools service.

    Expected response (minimum):
        {
            "success": true,
            "ordered_picking_ids": [<int>, ...]   # optional if routes given
        }
    or:
        {
            "success": true,
            "routes": [
                {"vehicle_index": 0, "node_indices": [0, 2, 1, 0]},
                ...
            ]
        }
    """
    url = (service_url or "").strip()
    if not url:
        raise OrtoolsServiceError("OR-Tools service URL is not configured.")

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Odoo-route-optimizer",
            "Accept": "application/json",
        },
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
        result = json.loads(body)
    except json.JSONDecodeError as e:
        raise OrtoolsServiceError("OR-Tools response is not valid JSON.") from e

    if result.get("success") is False:
        raise OrtoolsServiceError(result.get("error") or "OR-Tools service reported failure.")

    return result

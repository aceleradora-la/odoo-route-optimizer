# -*- coding: utf-8 -*-
"""HTTP client for OSRM Table service (duration/distance matrices)."""
import json
import urllib.error
import urllib.request


class OsrmError(Exception):
    """Raised when OSRM returns an error or invalid payload."""


def fetch_table(base_url, profile, coordinates_lonlat, timeout=60):
    """
    Call OSRM Table API.

    :param base_url: e.g. https://router.project-osrm.org (no trailing slash)
    :param profile: e.g. driving
    :param coordinates_lonlat: list of (longitude, latitude) floats, depot first
    :return: dict with keys durations, distances (lists of lists), raw response
    """
    base = (base_url or "").rstrip("/")
    if not base:
        raise OsrmError("OSRM base URL is not configured.")
    if len(coordinates_lonlat) < 2:
        raise OsrmError("At least two coordinates are required (depot + one stop).")

    coord_str = ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in coordinates_lonlat)
    path = f"/table/v1/{profile}/{coord_str}"
    url = f"{base}{path}?annotations=duration,distance"

    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Odoo-route-optimizer"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise OsrmError(f"OSRM HTTP error: {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise OsrmError(f"OSRM connection error: {e.reason}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise OsrmError("OSRM response is not valid JSON.") from e

    if data.get("code") != "Ok":
        raise OsrmError(data.get("message") or f"OSRM error code: {data.get('code')}")

    durations = data.get("durations")
    distances = data.get("distances")
    if not durations or not distances:
        raise OsrmError("OSRM response missing durations or distances.")

    return {
        "durations": durations,
        "distances": distances,
        "raw": data,
    }

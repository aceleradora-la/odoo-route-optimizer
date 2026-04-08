# -*- coding: utf-8 -*-
"""HTTP client for OSRM Table service (duration/distance matrices)."""
import json
import urllib.error
import urllib.request


class OsrmError(Exception):
    """Raised when OSRM returns an error or invalid payload."""


def normalize_osrm_base_url(base_url):
    """
    OSRM Table URLs must be built as {base}/table/v1/{profile}/{coords}.

    Users often paste ``http://host:5000/table/v1/`` — that duplicates the path
    and OSRM returns HTTP 400. Strip any trailing ``/table/v1`` (and ``/table``).
    """
    u = (base_url or "").strip().rstrip("/")
    if not u:
        return u
    lower = u.lower()
    while True:
        if lower.endswith("/table/v1"):
            u = u[: -len("/table/v1")].rstrip("/")
        elif lower.endswith("/table"):
            u = u[: -len("/table")].rstrip("/")
        else:
            break
        lower = u.lower()
    return u


def fetch_table(base_url, profile, coordinates_lonlat, timeout=60):
    """
    Call OSRM Table API.

    :param base_url: Server root only, e.g. http://195.179.231.4:5000 (NOT .../table/v1/)
    :param profile: e.g. driving
    :param coordinates_lonlat: list of (longitude, latitude) floats, depot first
    :return: dict with keys durations, distances (lists of lists), raw response
    """
    base = normalize_osrm_base_url(base_url)
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
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        hint = ""
        if e.code == 400:
            hint = (
                " If you set the base URL with /table/v1/, remove it: use only the server root "
                "(e.g. http://HOST:5000). Odoo appends /table/v1/{profile}/coordinates automatically."
            )
        raise OsrmError(
            f"OSRM HTTP error: {e.code} {e.reason}. {err_body[:400]}{hint}"
        ) from e
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

    # Some OSRM deployments are called with annotations=distance only; fill missing matrix.
    if not distances and not durations:
        raise OsrmError("OSRM response missing durations and distances.")
    if not durations:
        durations = distances
    if not distances:
        distances = durations

    return {
        "durations": durations,
        "distances": distances,
        "raw": data,
    }

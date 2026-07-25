# -*- coding: utf-8 -*-
"""
HTTP clients for Google paid routing services.

- Routes API (computeRouteMatrix): drop-in replacement for the OSRM matrix.
  Auth: API key header (X-Goog-Api-Key).
- Route Optimization API (optimizeTours): full VRP solver (matrix + solve in one call).
  Auth: OAuth2 service account (JWT bearer grant) — API keys are not accepted.

Both return plain dicts; route_service.py maps them to the module's internal contracts.
"""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
ROUTE_OPTIMIZATION_URL = "https://routeoptimization.googleapis.com/v1/projects/{project}:optimizeTours"
OAUTH_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# In-process OAuth token cache: {sa_key_hash: (access_token, expires_at_epoch)}
_TOKEN_CACHE = {}


class GoogleApiError(Exception):
    """Raised when a Google API returns an error or invalid payload."""


def _http_json(url, payload, headers, timeout):
    data = json.dumps(payload).encode("utf-8")
    all_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Odoo-route-optimizer",
        "Accept": "application/json",
    }
    all_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, method="POST", headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        raise GoogleApiError(f"Google HTTP {e.code}: {e.reason}. {err_body[:500]}") from e
    except urllib.error.URLError as e:
        raise GoogleApiError(f"Google connection error: {e.reason}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise GoogleApiError("Google response is not valid JSON.") from e


def _duration_to_seconds(value):
    """Google returns durations as strings like '3600s'."""
    if value is None:
        return None
    try:
        return float(str(value).rstrip("s"))
    except (TypeError, ValueError):
        return None


def fetch_route_matrix(api_key, coordinates_lonlat, timeout=60):
    """
    Call Routes API computeRouteMatrix and return the SAME contract as
    osrm_client.fetch_table: {"durations": [[s]], "distances": [[m]], "raw": ...}.

    :param coordinates_lonlat: list of (longitude, latitude), depot first.

    The API caps origins*destinations per request (625 elements without live
    traffic), so origins are paginated in chunks when n is large.
    """
    key = (api_key or "").strip()
    if not key:
        raise GoogleApiError("Google API key is not configured.")
    n = len(coordinates_lonlat)
    if n < 2:
        raise GoogleApiError("At least two coordinates are required (depot + one stop).")

    def waypoint(lon, lat):
        return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lon}}}}

    destinations = [waypoint(lon, lat) for lon, lat in coordinates_lonlat]
    durations = [[None] * n for _ in range(n)]
    distances = [[None] * n for _ in range(n)]
    raw_all = []

    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,condition",
    }

    max_elements = 625
    chunk = max(1, max_elements // n)
    for start in range(0, n, chunk):
        origin_slice = coordinates_lonlat[start:start + chunk]
        payload = {
            "origins": [waypoint(lon, lat) for lon, lat in origin_slice],
            "destinations": destinations,
            "travelMode": "DRIVE",
        }
        rows = _http_json(ROUTE_MATRIX_URL, payload, headers, timeout)
        raw_all.append(rows)
        if not isinstance(rows, list):
            raise GoogleApiError(
                "Unexpected computeRouteMatrix response (expected a list of elements)."
            )
        for el in rows:
            # Zero-valued indices are omitted by Google's JSON encoding.
            oi = int(el.get("originIndex", 0)) + start
            di = int(el.get("destinationIndex", 0))
            if el.get("condition") and el["condition"] != "ROUTE_EXISTS":
                continue  # unreachable pair -> stays None (handled downstream)
            durations[oi][di] = _duration_to_seconds(el.get("duration"))
            dist = el.get("distanceMeters")
            distances[oi][di] = float(dist) if dist is not None else None

    # Diagonal is never returned; it's zero by definition.
    for i in range(n):
        if durations[i][i] is None:
            durations[i][i] = 0.0
        if distances[i][i] is None:
            distances[i][i] = 0.0

    return {"durations": durations, "distances": distances, "raw": raw_all}


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _service_account_token(sa_json_str, timeout=30):
    """
    Exchange a GCP service-account key for an OAuth2 access token
    (JWT bearer grant, signed locally with `cryptography` — no extra deps).
    Tokens are cached in-process until 60s before expiry.
    """
    raw = (sa_json_str or "").strip()
    if not raw:
        raise GoogleApiError("Google service account JSON is not configured.")
    cache_key = hashlib.sha256(raw.encode()).hexdigest()
    cached = _TOKEN_CACHE.get(cache_key)
    now = time.time()
    if cached and cached[1] - 60 > now:
        return cached[0]

    try:
        sa = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GoogleApiError("Service account JSON is not valid JSON.") from e
    client_email = sa.get("client_email")
    private_key_pem = sa.get("private_key")
    token_uri = sa.get("token_uri") or "https://oauth2.googleapis.com/token"
    if not client_email or not private_key_pem:
        raise GoogleApiError("Service account JSON is missing client_email or private_key.")

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:
        raise GoogleApiError("Python 'cryptography' library is required for Google OAuth.") from e

    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    iat = int(now)
    claims = _b64url(
        json.dumps(
            {
                "iss": client_email,
                "scope": OAUTH_SCOPE,
                "aud": token_uri,
                "iat": iat,
                "exp": iat + 3600,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    try:
        private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        raise GoogleApiError(f"Could not sign OAuth JWT with the service account key: {e}") from e
    assertion = f"{header}.{claims}.{_b64url(signature)}"

    form = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode("ascii")
    req = urllib.request.Request(
        token_uri,
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        raise GoogleApiError(f"OAuth token request failed: HTTP {e.code}. {err_body[:400]}") from e
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        raise GoogleApiError(f"OAuth token request failed: {e}") from e

    access_token = token_data.get("access_token")
    if not access_token:
        raise GoogleApiError("OAuth response did not include an access_token.")
    expires_at = now + float(token_data.get("expires_in") or 3600)
    _TOKEN_CACHE[cache_key] = (access_token, expires_at)
    return access_token


def optimize_tours(project_id, sa_json_str, model_payload, timeout=120):
    """
    Call Route Optimization API optimizeTours with {"model": model_payload}.
    Returns the parsed response (routes[].visits[].shipmentIndex etc.).
    """
    project = (project_id or "").strip()
    if not project:
        raise GoogleApiError("Google project id is not configured.")
    token = _service_account_token(sa_json_str)
    url = ROUTE_OPTIMIZATION_URL.format(project=project)
    result = _http_json(
        url,
        {"model": model_payload},
        {"Authorization": f"Bearer {token}"},
        timeout,
    )
    if result.get("error"):
        raise GoogleApiError(str(result["error"])[:500])
    return result

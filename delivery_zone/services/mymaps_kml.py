# -*- coding: utf-8 -*-
"""
Cliente para leer zonas desde un Google My Maps público (export KML).

No requiere API key ni OAuth: My Maps expone el mapa como KML si está compartido
como «cualquiera con el enlace». Se descarga, se parsean los polígonos por capa/
placemark y se hace point-in-polygon (ray casting puro, sin dependencias).
"""
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

KML_URL = "https://www.google.com/maps/d/kml?mid={mid}&forcekml=1"
_KML_NS = "{http://www.opengis.net/kml/2.2}"


class MyMapsError(Exception):
    """Error al descargar o parsear el KML de My Maps."""


def extract_mid(raw):
    """Acepta el mid pelado o una URL de My Maps y devuelve el mid.

    Ejemplos válidos:
      1AbCdEf...
      https://www.google.com/maps/d/edit?mid=1AbCdEf...&usp=sharing
      https://www.google.com/maps/d/viewer?mid=1AbCdEf...
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "mid=" in value:
        parsed = urllib.parse.urlparse(value)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("mid"):
            return qs["mid"][0].strip()
    return value


def fetch_kml(mid, timeout=30):
    """Descarga el KML del mapa. `forcekml=1` evita el KMZ comprimido."""
    clean = extract_mid(mid)
    if not clean:
        raise MyMapsError("El ID del mapa (mid) de My Maps no está configurado.")
    url = KML_URL.format(mid=urllib.parse.quote(clean, safe=""))
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Odoo-delivery-zone"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code in (403, 404):
            hint = (
                " Verificá que el mapa esté compartido como «cualquiera con el enlace» "
                "y que el mid sea correcto."
            )
        raise MyMapsError(f"Google My Maps HTTP {e.code}: {e.reason}.{hint}") from e
    except urllib.error.URLError as e:
        raise MyMapsError(f"No se pudo conectar con Google My Maps: {e.reason}") from e


def _parse_coords(coord_text):
    """'lon,lat,alt lon,lat,alt ...' -> [[lon, lat], ...]"""
    ring = []
    for token in (coord_text or "").split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                ring.append([float(parts[0]), float(parts[1])])
            except (TypeError, ValueError):
                continue
    return ring


def parse_zones(kml_str):
    """
    Devuelve [{"name": str, "rings": [[[lon,lat],...], ...]}, ...].

    Cada Placemark con uno o más Polygon se convierte en una zona. El nombre sale
    del <name> del Placemark; si falta, del Folder contenedor.
    """
    try:
        root = ET.fromstring(kml_str)
    except ET.ParseError as e:
        raise MyMapsError("El KML de My Maps no es válido.") from e

    zones = []
    for placemark in root.iter(f"{_KML_NS}Placemark"):
        name_el = placemark.find(f"{_KML_NS}name")
        name = (name_el.text or "").strip() if name_el is not None else ""
        rings = []
        for polygon in placemark.iter(f"{_KML_NS}Polygon"):
            outer = polygon.find(
                f"{_KML_NS}outerBoundaryIs/{_KML_NS}LinearRing/{_KML_NS}coordinates"
            )
            if outer is not None and outer.text:
                ring = _parse_coords(outer.text)
                if len(ring) >= 3:
                    rings.append(ring)
        if rings:
            zones.append({"name": name or "Zona sin nombre", "rings": rings})
    return zones


def _point_in_ring(lon, lat, ring):
    """Ray casting estándar sobre un anillo [[lon,lat],...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-16) + xi
        ):
            inside = not inside
        j = i
    return inside


def _ring_area(ring):
    """Área por fórmula del shoelace (valor absoluto, en grados²)."""
    area = 0.0
    n = len(ring)
    j = n - 1
    for i in range(n):
        area += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1])
        j = i
    return abs(area) / 2.0


def point_in_zone(lon, lat, zones, tie_breaker="smallest_area"):
    """
    Devuelve el nombre de la zona que contiene (lon, lat), o None.

    zones: lista de {"name", "rings"}.
    tie_breaker: 'smallest_area' (gana la zona más chica si el punto cae en varias)
                 o 'first_match' (la primera del listado).
    """
    matches = []
    for zone in zones:
        for ring in zone.get("rings") or []:
            if _point_in_ring(lon, lat, ring):
                matches.append((zone["name"], _ring_area(ring)))
                break
    if not matches:
        return None
    if tie_breaker == "first_match" or len(matches) == 1:
        return matches[0][0]
    return min(matches, key=lambda m: m[1])[0]

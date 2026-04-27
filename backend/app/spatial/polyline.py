"""Pure-Python decoder for Google's Encoded Polyline Algorithm Format.

See: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
"""

from __future__ import annotations


def decode_polyline(s: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode an encoded polyline string into a list of (lat, lon) tuples.

    Args:
        s: Encoded polyline string.
        precision: Coordinate precision (5 for Google, 6 for OSRM/Valhalla).

    Returns:
        List of (lat, lon) coordinate pairs.
    """
    if not s:
        return []
    factor = 10 ** precision
    coords: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    length = len(s)
    while index < length:
        for _ in range(2):
            result = 0
            shift = 0
            while True:
                if index >= length:
                    return coords
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if _ == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords

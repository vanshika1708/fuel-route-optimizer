import hashlib
import json
import re

from django.conf import settings
from django.core.cache import cache


def normalize_location(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def geocode_cache_key(query: str) -> str:
    digest = hashlib.sha256(normalize_location(query).encode("utf-8")).hexdigest()
    return f"geocode:{digest}"


def route_cache_key(start: tuple[float, float], finish: tuple[float, float]) -> str:
    normalized = json.dumps(
        [[round(start[0], 5), round(start[1], 5)], [round(finish[0], 5), round(finish[1], 5)]],
        separators=(",", ":"),
    )
    digest = hashlib.sha256(normalized.encode("ascii")).hexdigest()
    return f"route:{digest}"


def cache_timeout() -> int:
    return settings.CACHE_TIMEOUT_SECONDS


def clear_route_caches() -> None:
    """Testing helper for local cache backends with a known key prefix."""
    cache.clear()
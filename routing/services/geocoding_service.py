import logging
import math
import threading
import time
from dataclasses import asdict, dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from routing.services.cache_service import cache_timeout, geocode_cache_key
from routing.services.provider_errors import GeocodingError, NonUSLocationError

logger = logging.getLogger(__name__)
_nominatim_lock = threading.Lock()
_last_nominatim_request = 0.0


@dataclass(frozen=True)
class GeoLocation:
    latitude: float
    longitude: float
    country_code: str
    display_name: str


class GeocodingService:
    """Geocode locations with Nominatim, cache results, and enforce US-only output."""

    def geocode(self, query: str) -> GeoLocation:
        normalized = " ".join(query.split())
        if not normalized:
            raise GeocodingError("Location cannot be empty.")

        key = geocode_cache_key(normalized)
        cached = cache.get(key)
        if cached is not None:
            logger.info("Geocoding cache hit")
            location = GeoLocation(**cached)
            if location.country_code != "us":
                raise NonUSLocationError("Location is outside the United States.")
            return location

        logger.info("Geocoding cache miss")
        started = time.monotonic()
        self._throttle_request()
        try:
            response = requests.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={
                    "q": normalized,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": 1,
                },
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
                timeout=settings.EXTERNAL_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            results = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Nominatim request failed after %.3fs: %s", time.monotonic() - started, exc)
            raise GeocodingError("Unable to geocode the supplied location.") from exc

        if not isinstance(results, list) or not results:
            raise GeocodingError("Unable to geocode the supplied location.")
        result = results[0]
        try:
            latitude = float(result["lat"])
            longitude = float(result["lon"])
            country_code = str(result["address"]["country_code"]).lower()
            display_name = str(result.get("display_name", ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingError("Nominatim returned an invalid location.") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise GeocodingError("Nominatim returned invalid coordinates.")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise GeocodingError("Nominatim returned coordinates outside valid bounds.")
        if country_code != "us":
            raise NonUSLocationError("Location is outside the United States.")

        location = GeoLocation(latitude, longitude, country_code, display_name)
        cache.set(key, asdict(location), cache_timeout())
        logger.info("Nominatim geocoding completed in %.3fs", time.monotonic() - started)
        return location

    @staticmethod
    def _throttle_request() -> None:
        global _last_nominatim_request
        with _nominatim_lock:
            elapsed = time.monotonic() - _last_nominatim_request
            wait = settings.NOMINATIM_MIN_INTERVAL_SECONDS - elapsed
            if wait > 0:
                time.sleep(wait)
            _last_nominatim_request = time.monotonic()
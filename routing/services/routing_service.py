import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from routing.services.cache_service import cache_timeout, route_cache_key
from routing.services.provider_errors import RoutingProviderError

logger = logging.getLogger(__name__)
METERS_PER_MILE = 1609.344
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class RouteResult:
    distance_meters: float
    duration_seconds: float
    geometry: dict[str, Any]

    @property
    def coordinates(self) -> list[list[float]]:
        return self.geometry["coordinates"]


class RoutingService:
    """Request and cache one driving route from the configurable OSRM provider."""

    def get_route(self, start: tuple[float, float], finish: tuple[float, float]) -> RouteResult:
        key = route_cache_key(start, finish)
        cached = cache.get(key)
        if cached is not None:
            logger.info("Routing cache hit")
            return RouteResult(**cached)

        logger.info("Routing cache miss; requesting OSRM route")
        started = time.monotonic()
        coordinates = f"{start[1]},{start[0]};{finish[1]},{finish[0]}"
        try:
            response = requests.get(
                f"{settings.OSRM_BASE_URL}/route/v1/driving/{coordinates}",
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
                timeout=settings.EXTERNAL_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("OSRM request failed after %.3fs: %s", time.monotonic() - started, exc)
            raise RoutingProviderError("Routing provider unavailable.") from exc

        try:
            route = payload["routes"][0]
            distance = float(route["distance"])
            duration = float(route["duration"])
            geometry = route["geometry"]
            points = geometry["coordinates"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingProviderError("Routing provider returned an invalid route.") from exc
        if (
            payload.get("code") != "Ok"
            or not math.isfinite(distance)
            or not math.isfinite(duration)
            or distance <= 0
            or duration < 0
            or geometry.get("type") != "LineString"
            or not isinstance(points, list)
            or len(points) < 2
        ):
            raise RoutingProviderError("Routing provider returned an invalid route.")
        for point in points:
            if (
                not isinstance(point, list)
                or len(point) < 2
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in point[:2])
                or not -180 <= point[0] <= 180
                or not -90 <= point[1] <= 90
            ):
                raise RoutingProviderError("Routing provider returned invalid geometry coordinates.")

        result = RouteResult(distance, duration, geometry)
        cache.set(key, result.__dict__, cache_timeout())
        logger.info("OSRM route completed in %.3fs", time.monotonic() - started)
        return result
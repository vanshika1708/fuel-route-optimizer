import logging
from dataclasses import dataclass
from decimal import Decimal

from routing.models import FuelStation
from routing.services.route_geometry_service import RouteGeometryService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FuelCandidate:
    station: FuelStation
    distance_from_start_miles: float
    distance_from_route_miles: float


class FuelService:
    """Find geocoded station records within a buffered route using local geometry."""

    def find_candidates(
        self,
        coordinates: list[list[float]],
        corridor_miles: float,
        route_distance_miles: float | None = None,
    ) -> list[FuelCandidate]:
        geometry = RouteGeometryService(coordinates, route_distance_miles)
        min_longitude, min_latitude, max_longitude, max_latitude = geometry.bounding_box(corridor_miles)
        stations = FuelStation.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            geocoded_country_code="us",
            retail_price__gt=Decimal(0),
            latitude__range=(min_latitude, max_latitude),
            longitude__range=(min_longitude, max_longitude),
        ).only(
            "id",
            "opis_truckstop_id",
            "truckstop_name",
            "latitude",
            "longitude",
            "retail_price",
            "city",
            "state",
        )

        candidates: list[FuelCandidate] = []
        for station in stations.iterator(chunk_size=1000):
            point = geometry.project(float(station.longitude), float(station.latitude))
            if point.distance_from_route_miles <= corridor_miles:
                candidates.append(
                    FuelCandidate(
                        station=station,
                        distance_from_start_miles=point.distance_from_start_miles,
                        distance_from_route_miles=point.distance_from_route_miles,
                    )
                )
        candidates.sort(key=lambda candidate: (candidate.distance_from_start_miles, candidate.station.pk))
        logger.info("Route geometry found %d candidate stations", len(candidates))
        return candidates
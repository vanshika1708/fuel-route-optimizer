import math
from dataclasses import dataclass

METERS_PER_MILE = 1609.344
MILES_PER_DEGREE_LATITUDE = 69.093
EARTH_RADIUS_METERS = 6_371_008.8


@dataclass(frozen=True)
class ProjectedPoint:
    distance_from_start_miles: float
    distance_from_route_miles: float


class RouteGeometryService:
    """Measure route progress and point-to-route distance without external calls."""

    def __init__(self, coordinates: list[list[float]], route_distance_miles: float | None = None) -> None:
        if len(coordinates) < 2:
            raise ValueError("A route needs at least two coordinates.")
        self.coordinates = [(float(point[0]), float(point[1])) for point in coordinates]
        self.segment_lengths = [
            self._haversine_miles(first, second)
            for first, second in zip(self.coordinates, self.coordinates[1:], strict=False)
        ]
        geometric_distance = sum(self.segment_lengths)
        if route_distance_miles is not None and geometric_distance > 0:
            scale = route_distance_miles / geometric_distance
            self.segment_lengths = [length * scale for length in self.segment_lengths]
        self.cumulative_miles = [0.0]
        for segment_length in self.segment_lengths:
            self.cumulative_miles.append(self.cumulative_miles[-1] + segment_length)

    @property
    def total_distance_miles(self) -> float:
        return self.cumulative_miles[-1]

    def bounding_box(self, corridor_miles: float) -> tuple[float, float, float, float]:
        longitudes, latitudes = zip(*self.coordinates, strict=True)
        min_latitude = max(-90.0, min(latitudes) - corridor_miles / MILES_PER_DEGREE_LATITUDE)
        max_latitude = min(90.0, max(latitudes) + corridor_miles / MILES_PER_DEGREE_LATITUDE)
        mean_latitude = math.radians((min(latitudes) + max(latitudes)) / 2)
        longitude_scale = max(0.2, abs(math.cos(mean_latitude)))
        longitude_margin = corridor_miles / (MILES_PER_DEGREE_LATITUDE * longitude_scale)
        return (
            min(longitudes) - longitude_margin,
            min_latitude,
            max(longitudes) + longitude_margin,
            max_latitude,
        )

    def project(self, longitude: float, latitude: float) -> ProjectedPoint:
        best_distance = math.inf
        best_progress = 0.0
        for index, (first, second) in enumerate(
            zip(self.coordinates, self.coordinates[1:], strict=False)
        ):
            mean_latitude = math.radians((first[1] + second[1] + latitude) / 3)
            longitude_scale = MILES_PER_DEGREE_LATITUDE * math.cos(mean_latitude)
            segment_x = (second[0] - first[0]) * longitude_scale
            segment_y = (second[1] - first[1]) * MILES_PER_DEGREE_LATITUDE
            point_x = (longitude - first[0]) * longitude_scale
            point_y = (latitude - first[1]) * MILES_PER_DEGREE_LATITUDE
            segment_squared = segment_x * segment_x + segment_y * segment_y
            if segment_squared == 0:
                fraction = 0.0
            else:
                fraction = min(1.0, max(0.0, (point_x * segment_x + point_y * segment_y) / segment_squared))
            delta_x = point_x - fraction * segment_x
            delta_y = point_y - fraction * segment_y
            distance = math.hypot(delta_x, delta_y)
            if distance < best_distance:
                best_distance = distance
                best_progress = self.cumulative_miles[index] + fraction * self.segment_lengths[index]
        return ProjectedPoint(best_progress, best_distance)

    @staticmethod
    def _haversine_miles(first: tuple[float, float], second: tuple[float, float]) -> float:
        lon1, lat1 = map(math.radians, first)
        lon2, lat2 = map(math.radians, second)
        delta_latitude = lat2 - lat1
        delta_longitude = lon2 - lon1
        value = (
            math.sin(delta_latitude / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_longitude / 2) ** 2
        )
        return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(value))) / METERS_PER_MILE
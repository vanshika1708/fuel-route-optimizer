class GeocodingError(Exception):
    """Raised when Nominatim cannot provide a valid geocoding result."""


class NonUSLocationError(GeocodingError):
    """Raised when a geocoding result is outside the United States."""


class RoutingProviderError(Exception):
    """Raised when OSRM fails or returns an invalid route."""


class NoFeasibleFuelPlanError(Exception):
    """Raised when stations cannot bridge the route within vehicle range."""
import logging
from typing import Any

from django.core.management.base import BaseCommand

from routing.models import FuelStation
from routing.services.geocoding_service import GeocodingService
from routing.services.provider_errors import GeocodingError

logger = logging.getLogger(__name__)
US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


class Command(BaseCommand):
    help = "Enrich US station rows with cached Nominatim city-level coordinates."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum distinct city/state pairs to process.",
        )
        parser.add_argument(
            "--state",
            action="append",
            default=None,
            help="Restrict enrichment to a US state code; may be repeated.",
        )
        parser.add_argument(
            "--city",
            action="append",
            default=None,
            help="Restrict enrichment to a city name; may be repeated.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit: int | None = options["limit"]
        if limit is not None and limit < 1:
            self.stderr.write(self.style.ERROR("--limit must be a positive integer."))
            return

        queryset = FuelStation.objects.filter(latitude__isnull=True, state__in=US_STATE_CODES)
        if options["state"]:
            queryset = queryset.filter(state__in=[state.upper() for state in options["state"]])
        if options["city"]:
            queryset = queryset.filter(city__in=options["city"])
        pairs = list(queryset.values_list("city", "state").distinct().order_by("state", "city"))
        if limit is not None:
            pairs = pairs[:limit]

        geocoder = GeocodingService()
        updated = failed = 0
        for city, state in pairs:
            query = f"{city}, {state}, United States"
            try:
                location = geocoder.geocode(query)
            except GeocodingError as exc:
                failed += 1
                logger.warning("Could not geocode station city %s, %s: %s", city, state, exc)
                continue

            changed = FuelStation.objects.filter(city=city, state=state, latitude__isnull=True).update(
                latitude=location.latitude,
                longitude=location.longitude,
                geocoded_country_code=location.country_code,
                location_precision="city",
            )
            updated += changed

        self.stdout.write(
            f"City/state pairs processed: {len(pairs)}\n"
            f"Updated station rows: {updated}\nFailed pairs: {failed}"
        )
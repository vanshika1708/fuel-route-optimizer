import csv
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation

CSV_COLUMNS = (
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
)
FIELD_MAX_LENGTHS = {"truckstop_name": 255, "address": 255, "city": 120}
QUERY_BATCH_SIZE = 500
WRITE_BATCH_SIZE = 500
MAX_ROW_WARNINGS = 20


def _fingerprint(values: list[str]) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Command(BaseCommand):
    help = "Import fuel prices from the supplied OPIS CSV dataset."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--csv",
            dest="csv_path",
            type=Path,
            default=settings.BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
            help="Path to the source CSV file.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        csv_path: Path = options["csv_path"]
        if not csv_path.is_file():
            raise CommandError(f"CSV file not found: {csv_path}")

        imported = skipped = duplicates = errors = warning_count = 0
        pending: list[FuelStation] = []
        seen: set[str] = set()

        try:
            source = csv_path.open("r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise CommandError(f"Could not open CSV file: {exc}") from exc

        with source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                raise CommandError("CSV file is empty or has no header row.")
            if tuple(header.strip() for header in reader.fieldnames) != CSV_COLUMNS:
                expected = ", ".join(CSV_COLUMNS)
                actual = ", ".join(reader.fieldnames)
                raise CommandError(f"Unexpected CSV columns. Expected: {expected}. Found: {actual}.")

            for line_number, row in enumerate(reader, start=2):
                try:
                    if None in row:
                        raise ValueError("row contains more fields than the header")
                    station = self._parse_station(row)
                except (InvalidOperation, TypeError, ValueError) as exc:
                    skipped += 1
                    if warning_count < MAX_ROW_WARNINGS:
                        self.stderr.write(self.style.WARNING(f"Skipping CSV row {line_number}: {exc}"))
                    warning_count += 1
                    continue

                fingerprint = station.source_fingerprint
                if fingerprint in seen:
                    duplicates += 1
                    continue
                seen.add(fingerprint)
                pending.append(station)

        fingerprints = [station.source_fingerprint for station in pending]
        existing: set[str] = set()
        for offset in range(0, len(fingerprints), QUERY_BATCH_SIZE):
            batch = fingerprints[offset : offset + QUERY_BATCH_SIZE]
            existing.update(
                FuelStation.objects.filter(source_fingerprint__in=batch).values_list(
                    "source_fingerprint", flat=True
                )
            )

        new_stations = [station for station in pending if station.source_fingerprint not in existing]
        duplicates += len(pending) - len(new_stations)
        with transaction.atomic():
            for offset in range(0, len(new_stations), WRITE_BATCH_SIZE):
                FuelStation.objects.bulk_create(
                    new_stations[offset : offset + WRITE_BATCH_SIZE],
                    batch_size=WRITE_BATCH_SIZE,
                    ignore_conflicts=True,
                )
        imported = len(new_stations)

        if warning_count > MAX_ROW_WARNINGS:
            self.stderr.write(
                self.style.WARNING(f"Suppressed {warning_count - MAX_ROW_WARNINGS} additional row warnings.")
            )
        self.stdout.write(
            f"Imported: {imported}\nSkipped: {skipped}\nDuplicates: {duplicates}\nErrors: {errors}"
        )

    @staticmethod
    def _parse_station(row: dict[str, str | None]) -> FuelStation:
        values = {column: (row.get(column) or "").strip() for column in CSV_COLUMNS}
        if any(not value for value in values.values()):
            missing = [column for column, value in values.items() if not value]
            raise ValueError(f"missing required value(s): {', '.join(missing)}")

        opis_id = int(values["OPIS Truckstop ID"])
        rack_id = int(values["Rack ID"])
        price = Decimal(values["Retail Price"])
        if opis_id < 0 or rack_id < 0 or not price.is_finite() or price <= 0:
            raise ValueError("IDs must be non-negative and retail price must be positive")

        name = values["Truckstop Name"]
        address = values["Address"]
        city = values["City"]
        if len(name) > FIELD_MAX_LENGTHS["truckstop_name"]:
            raise ValueError("Truckstop Name exceeds 255 characters")
        if len(address) > FIELD_MAX_LENGTHS["address"]:
            raise ValueError("Address exceeds 255 characters")
        if len(city) > FIELD_MAX_LENGTHS["city"]:
            raise ValueError("City exceeds 120 characters")

        state = values["State"].upper()
        if len(state) != 2:
            raise ValueError("State must contain a two-character state or province code")

        fingerprint_values = [
            str(opis_id),
            name,
            address,
            city,
            state,
            str(rack_id),
            format(price.normalize(), "f"),
        ]
        return FuelStation(
            opis_truckstop_id=opis_id,
            truckstop_name=name,
            address=address,
            city=city,
            state=state,
            rack_id=rack_id,
            retail_price=price,
            source_fingerprint=_fingerprint(fingerprint_values),
        )
import csv

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from routing.models import FuelStation

HEADERS = [
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
]


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def valid_row(**overrides):
    row = {
        "OPIS Truckstop ID": "101",
        "Truckstop Name": "TEST TRUCKSTOP",
        "Address": "I-80 EXIT 1",
        "City": "Test City",
        "State": "NY",
        "Rack ID": "22",
        "Retail Price": "3.25999999",
    }
    return row | overrides


@pytest.mark.django_db
def test_import_valid_rows_skips_duplicate_and_malformed(tmp_path):
    path = tmp_path / "fuel.csv"
    write_csv(
        path,
        [
            valid_row(),
            valid_row(),
            valid_row(**{"OPIS Truckstop ID": "bad"}),
            valid_row(**{"Retail Price": ""}),
            valid_row(**{"Address": "SECOND LOCATION", "Retail Price": "4.00"}),
        ],
    )

    call_command("import_fuel_prices", csv=path)

    assert FuelStation.objects.count() == 2
    assert FuelStation.objects.get(retail_price="3.25999999").latitude is None
    assert FuelStation.objects.filter(opis_truckstop_id=101).count() == 2


@pytest.mark.django_db
def test_import_is_idempotent(tmp_path):
    path = tmp_path / "fuel.csv"
    write_csv(path, [valid_row()])

    call_command("import_fuel_prices", csv=path)
    call_command("import_fuel_prices", csv=path)

    assert FuelStation.objects.count() == 1


def test_import_rejects_missing_coordinates_header(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("station,latitude,longitude,price\n", encoding="utf-8")

    with pytest.raises(CommandError, match="Unexpected CSV columns"):
        call_command("import_fuel_prices", csv=path)
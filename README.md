# FuelRoute Optimizer

A Django REST API that geocodes US locations with OpenStreetMap Nominatim, obtains a driving route from OSRM, searches locally imported fuel prices, and plans range-feasible fuel purchases.

## Project Overview

`POST /api/v1/route/` accepts a US start and finish location and returns route geometry, distance, duration, selected stops, fuel consumed, fuel purchased, and fuel expenditure. `GET /api/v1/health/` is a lightweight health check.

## Architecture

- `GeocodingService` calls Nominatim, checks the returned country, throttles requests, and caches normalized queries.
- `RoutingService` makes one OSRM driving request per uncached coordinate pair and caches distance, duration, and GeoJSON.
- `RouteGeometryService` projects points onto route segments and computes cumulative route progress locally.
- `FuelService` uses a SQLite bounding-box prefilter and local geometric distance checks.
- `FuelOptimizationService` uses the standard minimum-cost gas-station strategy over the available ordered route candidates. It uses already-paid initial fuel first, targets the nearest reachable cheaper station, and otherwise buys only enough to finish or fills to extend reach.

External providers are isolated behind services, use explicit timeouts, and can be replaced independently.

## Technology Stack

Python 3.12+, Django 6.1, Django REST Framework, SQLite, requests, python-dotenv, pytest, pytest-django, and Ruff.

## Why OSRM and OpenStreetMap

OSRM provides driving routes using OpenStreetMap road data without a paid API key. Nominatim provides open-data geocoding. Public endpoints have usage policies and availability limits; deployers with sustained traffic should operate or select compliant provider instances and set a descriptive `NOMINATIM_USER_AGENT`.

## CSV Schema Discovered

The supplied file has **8,151 data rows** and exactly these seven columns:

| CSV column | Observed values | Model representation |
| --- | --- | --- |
| `OPIS Truckstop ID` | integer-like; 6,738 unique values | `opis_truckstop_id` (`BigIntegerField`, indexed, not unique) |
| `Truckstop Name` | text | `truckstop_name` |
| `Address` | text | `address` |
| `City` | text | `city` |
| `State` | two-character state/province code | `state` |
| `Rack ID` | integer-like | `rack_id` (`IntegerField`) |
| `Retail Price` | decimal numeric, range 2.68733333 to 6.399 | `retail_price` (`DecimalField`, 8 decimal places) |

There are no missing values in the inspected file. There are 678 repeated-ID groups; most repeated IDs identify differing rows, so OPIS ID alone cannot be unique. There are 26 redundant exact duplicate rows. Import identity is a SHA-256 fingerprint of all seven normalized source fields. The dataset contains 4,271 unique city/state pairs, including Canadian provinces; only US state codes are eligible for US enrichment.

**The CSV has no latitude or longitude columns.** The model therefore keeps the seven source fields faithfully and stores optional `latitude`, `longitude`, country code, and `location_precision` as separate geocoding enrichment. Coordinates produced from a city/state query are explicitly marked `city`; they are approximate city locations, not exact truck-stop entrances. Consequently, route proximity and stop coordinates are approximate until a station-level coordinate source is supplied. The importer does not invent coordinates.

## Database Model

`FuelStation` stores each distinct source row, exact decimal retail price, and nullable indexed geocoded coordinates. A composite source fingerprint makes repeated imports safe without discarding distinct rows sharing an OPIS ID. The admin exposes station records for inspection.

## Setup

Use a Python 3.12+ interpreter. Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py import_fuel_prices
python manage.py runserver
```

Import is offline and repeatable. To add approximate coordinates for US city/state rows, run `python manage.py geocode_fuel_stations --limit 10` to trial a small batch, or omit `--limit` to process all currently unlocated US city/state pairs. Nominatim requests are cached and spaced by at least 1.1 seconds; a full first enrichment can take over an hour and should be run deliberately. Failed pairs remain eligible for a later retry.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEBUG` | `False` in settings; local `.env.example` sets `True` | Django debug mode |
| `SECRET_KEY` | development-only fallback when debug is on | Django secret; provide a strong secret in deployment |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma-separated host allowlist |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | OSRM-compatible routing service |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` | Nominatim-compatible geocoder |
| `NOMINATIM_USER_AGENT` | local development identifier | Descriptive provider User-Agent |
| `ROUTE_CORRIDOR_MILES` | `25` | Maximum station distance from route |
| `MAX_RANGE_MILES` | `500` | Vehicle range per full tank |
| `VEHICLE_MPG` | `10` | Fuel economy |
| `CACHE_TIMEOUT_SECONDS` | `86400` | Geocoding/route cache lifetime |
| `EXTERNAL_REQUEST_TIMEOUT_SECONDS` | `12` | HTTP timeout |
| `NOMINATIM_MIN_INTERVAL_SECONDS` | `1.1` | Minimum interval between Nominatim calls |
| `CACHE_URL` | empty | Optional `redis://`/`rediss://` Redis cache URL |
| `SQLITE_PATH` | `db.sqlite3` in project root | Optional database file path |

## API Examples

Health check:

```http
GET /api/v1/health/
```

```json
{"status": "ok"}
```

Route request:

```http
POST /api/v1/route/
Content-Type: application/json

{"start": "New York, NY", "finish": "Chicago, IL"}
```

The response contains `request`, `route` (miles, minutes, GeoJSON LineString), `vehicle`, `fuel_stops`, and `fuel_summary`. Each stop includes source station ID/name, city-level precision when applicable, price, route position, gallons bought, cost, and range reason. Errors use `{ "error": "..." }` with 400, 422, or 502 status codes as appropriate.

## Sample Response Shape

Values below describe fields only; runtime values always come from the geocoder, OSRM, local imported records, and optimizer.

```json
{
  "request": {"start": "New York, NY", "finish": "Chicago, IL"},
  "route": {
    "distance_miles": null,
    "duration_minutes": null,
    "geometry": {"type": "LineString", "coordinates": []}
  },
  "vehicle": {
    "max_range_miles": 500,
    "mpg": 10,
    "tank_capacity_gallons": 50,
    "initial_fuel_gallons": 50
  },
  "fuel_stops": [],
  "fuel_summary": {
    "total_distance_miles": null,
    "total_fuel_consumed_gallons": null,
    "initial_fuel_gallons": 50,
    "total_fuel_purchased_gallons": null,
    "ending_fuel_gallons": null,
    "total_fuel_cost": null
  }
}
```

The shape is illustrative; the example's stop list and totals are not a promised result. A request can return 422 when the locally geocoded station records cannot bridge a required leg.

## Fuel Optimization Algorithm

The optimizer minimizes the **additional fuel purchase cost** over the supplied candidate stations. The initial tank is treated as already paid for: it is consumed normally, but its cost is never included. Fuel consumption is route distance divided by MPG; each purchase is limited by remaining tank capacity, and every traveled leg must fit both the available fuel and maximum vehicle range.

Candidates are ordered by projected miles from the route start, then station record ID for deterministic ties. This is a one-dimensional route model: a candidate can be used only in forward route order. The nearest-cheaper decision is precomputed over that order. At each paid station, if a cheaper station lies within maximum range, the planner buys only enough to reach it. Otherwise, it buys enough to reach the destination when that is within one tank, or fills to capacity to maximize reach. If the destination is reachable with current fuel, the planner buys nothing.

This is the standard gas-station greedy algorithm. With fixed route positions, unlimited availability at each candidate, constant MPG, linear per-gallon prices, freely chosen purchase quantities, no station detour cost, and no per-purchase fees, buying past the nearest cheaper reachable station cannot improve cost: the same gallons can instead be bought at that cheaper station. If there is no cheaper station within range, filling maximizes how far the current purchase can carry the vehicle. Repeating those choices yields a minimum-cost plan over the supplied ordered candidate model. It does **not** guarantee a globally cheapest real-world trip when candidate coordinates or route positions are inaccurate, stations have detours or availability constraints, or prices/costs include fees.

For example, if the vehicle reaches a $4.00 station with enough fuel to reach a $3.00 station 150 miles farther along, it buys only the shortfall needed to cover those 150 miles. It does not fill the tank at $4.00. If no cheaper station is reachable and the destination is beyond range, it buys up to capacity so the next purchase can be delayed as far as possible.

For $n$ candidates, sorting and route-position indexing take $O(n\log n)$ time; the nearest-cheaper scan and fueling traversal are linear, with binary searches per transition. Optimizer space use is $O(n)$. Candidate database filtering and geometric projection happen separately in `FuelService`; no routing or geocoding requests are made per station.

The CSV has no station coordinates. City/state enrichment produces approximate city-level points, not exact truck-stop entrances. Therefore the guarantee applies only to the candidates, prices, and projected positions actually supplied to the optimizer; the real-world result remains approximate until precise station coordinates and current prices are available.

## Fueling Assumptions

- The vehicle begins with a full 50-gallon tank. `500 miles / 10 MPG = 50 gallons`.
- The opening tank is already paid for and is never included in route fuel cost.
- Route fuel consumption is `distance_miles / 10` and is distinct from purchased gallons.
- Purchases are made only at selected stations and never exceed tank capacity.
- Every leg between start, selected stations, and finish must fit the 500-mile maximum range.
- Fuel prices are the CSV `Retail Price` values. Costs use decimal arithmetic and round to cents when serialized in the API response.
- The default candidate corridor is 25 miles. City-level geocoding makes this proximity approximate, as disclosed above.
- If the finish is reachable with fuel already in the tank, the planner does not add an unnecessary stop or purchase.

## Performance and External Calls

- One Nominatim request per uncached endpoint location, up to two for a route request.
- One OSRM request per uncached route; route cache keys use normalized rounded coordinates.
- Django local-memory cache is the default; configure `CACHE_URL` to enable Redis (install the Django Redis extra appropriate to your deployment if required).
- Station selection uses a database bounding box, then local segment projection; it never calls a routing API per station.
- CSV records are bulk inserted in batches. Nominatim enrichment is opt-in, throttled, and resumable.
- Logs include provider cache misses/failures, routing/geocoding timings, candidate counts, selected-stop counts, and total route processing time; user location text is not logged.

## Testing and Linting

```powershell
pytest
ruff check .
python manage.py check
python manage.py makemigrations --check
```

Provider and API tests mock HTTP and do not contact live OSRM or Nominatim.

## Postman

Import [`postman/FuelRoute-Optimizer.postman_collection.json`](postman/FuelRoute-Optimizer.postman_collection.json) into Postman. It includes health, New York to Chicago, Los Angeles to San Francisco, long-distance, and invalid-location requests.

## Docker

Copy `.env.example` to `.env`, then run `docker compose up --build`. The SQLite database is stored in a named volume. Import station records inside the container with `docker compose exec web python manage.py import_fuel_prices`; coordinate enrichment remains a separate deliberate command.

## Future Improvements

- Obtain licensed or open station-level coordinates and refreshable price timestamps.
- Replace city centroids with precise station geocoding or a vetted station coordinate dataset.
- Use PostGIS for large station inventories and geospatial corridor queries.
- Add provider circuit breakers, distributed rate-limit coordination, and request-level throttling.
- Add CI, deployment manifests, and operational metrics.
from django.db import models


class FuelStation(models.Model):
	"""A source CSV station row with optional geocoded location enrichment."""

	opis_truckstop_id = models.BigIntegerField(db_index=True)
	truckstop_name = models.CharField(max_length=255)
	address = models.CharField(max_length=255)
	city = models.CharField(max_length=120)
	state = models.CharField(max_length=2, db_index=True)
	rack_id = models.IntegerField()
	retail_price = models.DecimalField(max_digits=12, decimal_places=8, db_index=True)
	source_fingerprint = models.CharField(max_length=64, unique=True, editable=False)
	latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, db_index=True)
	longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, db_index=True)
	geocoded_country_code = models.CharField(max_length=2, blank=True)
	location_precision = models.CharField(max_length=8, default="city")

	class Meta:
		ordering = ("retail_price", "opis_truckstop_id")
		indexes = [  # noqa: RUF012
			models.Index(fields=("latitude", "longitude"), name="station_lat_lon_idx"),
			models.Index(fields=("state", "city"), name="station_state_city_idx"),
		]

	def __str__(self) -> str:
		return f"{self.truckstop_name} ({self.city}, {self.state})"

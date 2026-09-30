from django.contrib import admin

from routing.models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
	list_display = ("opis_truckstop_id", "truckstop_name", "city", "state", "retail_price")
	list_filter = ("state",)
	search_fields = ("truckstop_name", "city", "state", "address")
	readonly_fields = ("source_fingerprint", "location_precision")

from django.urls import path

from routing.views import RouteView, health

urlpatterns = [
    path("health/", health, name="health"),
    path("route/", RouteView.as_view(), name="route"),
]
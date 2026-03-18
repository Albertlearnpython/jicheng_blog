from django.urls import path

from . import feishu_views
from . import views

urlpatterns = [
    path("", views.health, name="service-health"),
    path("health/", views.health, name="service-health-alias"),
    path("api/feishu/events/", feishu_views.feishu_events, name="feishu-events"),
    path("blog/api/feishu/events/", feishu_views.feishu_events, name="feishu-events-legacy"),
]

from django.urls import path

from .consumers import TerminalConsumer


websocket_urlpatterns = [
    path("blog/ws/terminal/<str:token>/", TerminalConsumer.as_asgi()),
]

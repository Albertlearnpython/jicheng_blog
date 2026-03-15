from django.urls import path

from . import feishu_views
from . import views

urlpatterns = [
    path('', views.home, name='blog-home'),
    path('chat/', views.chat_page, name='chat-page'),
    path('t/<str:code>', views.terminal_short_page, name='terminal-short-page'),
    path('terminal/<str:token>/', views.terminal_page, name='terminal-page'),
    path('api/chat/', views.chat_api_v2, name='chat-api'),
    path('api/terminal/<str:token>/', views.terminal_api, name='terminal-api'),
    path('api/feishu/events/', feishu_views.feishu_events, name='feishu-events'),
    path('post/<int:pk>/', views.post_detail, name='post-detail'),
]

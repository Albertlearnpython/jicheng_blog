from django.urls import path

from . import feishu_views
from . import views

urlpatterns = [
    path('', views.home, name='blog-home'),
    path('chat/', views.chat_page, name='chat-page'),
    path('api/chat/', views.chat_api, name='chat-api'),
    path('api/feishu/events/', feishu_views.feishu_events, name='feishu-events'),
    path('post/<int:pk>/', views.post_detail, name='post-detail'),
]

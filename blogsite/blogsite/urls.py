from django.contrib import admin
from django.urls import include, path

from blog import views as blog_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", blog_views.landing, name="site-home"),
    path("blog/", include("blog.urls")),
]

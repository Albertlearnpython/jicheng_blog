from django.contrib import admin

from .models import Post, RemoteChangeRequest


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "date_posted")
    search_fields = ("title", "content", "author__username")


@admin.register(RemoteChangeRequest)
class RemoteChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("approval_token", "status", "chat_id", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("approval_token", "prompt", "chat_id", "user_open_id")

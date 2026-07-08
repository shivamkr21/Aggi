from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Book, Conversation, Message, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name = "Profile"
    fields = ("position",)


class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("role", "source", "rewritten_query", "citations", "content", "created_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "is_deleted", "created_at", "updated_at")
    list_filter = ("is_deleted",)
    inlines = [MessageInline]


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "order", "is_active")
    list_editable = ("order", "is_active")
    list_display_links = ("title",)
    search_fields = ("title", "author")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "source", "rewritten_query", "created_at")
    list_filter = ("role", "source")

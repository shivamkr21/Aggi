from django.urls import path

from . import views

urlpatterns = [
    path("", views.chat_home, name="chat_home"),
    path("chat/<int:conversation_id>/", views.chat_view, name="chat"),
    path("chat/<int:conversation_id>/ask/", views.ask_view, name="ask"),
    path("chat/<int:conversation_id>/delete/", views.delete_conversation_view, name="delete_conversation"),
    path("chat/<int:conversation_id>/rename/", views.rename_conversation_view, name="rename_conversation"),
    path("new/", views.new_conversation_view, name="new_conversation"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
]

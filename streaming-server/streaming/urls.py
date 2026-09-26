from django.urls import path
from . import views

app_name = "streaming"

urlpatterns = [
    path("", views.login_view, name="login"),
    path("camera/", views.camera_view, name="camera"),
    path("snapshot/", views.snapshot_view, name="snapshot"),
    path("logout/", views.logout_view, name="logout"),
]

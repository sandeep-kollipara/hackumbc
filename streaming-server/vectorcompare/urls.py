from django.urls import include, path

urlpatterns = [
    path("", include("streaming.urls")),
]

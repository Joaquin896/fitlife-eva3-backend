from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "FitLife · Administración"
admin.site.site_title = "FitLife Admin"
admin.site.index_title = "Gestión del centro deportivo"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/", include("gym.api_urls")),
    path("api-auth/", include("rest_framework.urls")),
    path("", include("gym.urls")),
]

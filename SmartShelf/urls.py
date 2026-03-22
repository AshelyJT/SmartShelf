"""
URL configuration for SmartShelf project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from ProjectSmartShelf import views as pss_views
from checkout import views as checkout_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("ProjectSmartShelf/", include("ProjectSmartShelf.urls")),
    path("checkout/", include("checkout.urls")),
    path("", pss_views.login_view, name="staff_login"),
    path("logout/", pss_views.staff_logout_view, name="staff_logout"),
]

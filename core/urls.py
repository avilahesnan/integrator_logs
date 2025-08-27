"""
URL configuration for core project.

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
from django.urls import path, re_path
from logs.views import receive_log, last_logs, pharmacy_logs, index


urlpatterns = [
    path('', index, name='index'),
    path('admin-management/', admin.site.urls),
    path('api/log-sync/', receive_log, name='receive_log'),
    path('api/last-logs/', last_logs, name='last_logs'),
    path('api/logs/<str:store_id>/', pharmacy_logs, name='pharmacy_logs'),
    re_path(r'^.*$', index),
]

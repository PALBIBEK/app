from django.contrib import admin
from django.contrib.staticfiles.views import serve
from django.urls import path, re_path
from lib import shop
urlpatterns = [path("admin/", admin.site.urls), *shop.urls, re_path(r"^static/(?P<path>.*)$", serve, {"insecure": True})]

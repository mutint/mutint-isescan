from django.urls import re_path

from mutint_isescan import views

urlpatterns = [
    re_path(r'^runs$', views.runs, name='isescan_runs'),
    re_path(r'^runs/(?P<pk>\d+)/delete$', views.run_delete, name='isescan_run_delete'),
]

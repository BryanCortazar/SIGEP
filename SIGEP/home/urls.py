from django.urls import path
from . import views

app_name = 'home'

urlpatterns = [
    path('', views.index, name='index'),
    path('ranking/data/', views.ranking_data, name='ranking_data'),
]

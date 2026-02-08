from django.urls import path
from . import views

app_name = 'principal'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('registrar/', views.registrar_view, name='registrar'),
    path('recuperar-cuenta/', views.recuperar_cuenta_view, name='recuperar'),
]
from django.shortcuts import render

def login_view(request):
     return render(request, 'principal/login.html')

def registrar_view(request):
    return render(request, 'principal/registrar.html')

def recuperar_cuenta_view(request):
    return render(request, 'principal/recuperar_cuenta.html')

def index(request):
    return render(request, 'principal/index.html')

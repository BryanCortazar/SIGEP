@echo off
echo Activando entorno virtual...

REM Cambia al directorio donde está este archivo
cd /d "%~dp0"

REM Verifica si existe el entorno virtual
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: No se encontro el entorno virtual "venv".
    echo Asegurate de que la carpeta venv exista.
    pause
    exit /b
)

REM Activa el entorno virtual
call venv\Scripts\activate.bat

echo Entorno virtual activado correctamente.
cmd

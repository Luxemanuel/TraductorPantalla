@echo off
chcp 65001 >nul
title Traductor de Pantalla
cd /d "%~dp0"
set "VERSION=2"

rem ---- Buscar Python (prefiere 3.12 / 3.11 / 3.13) ----
set "PY="
for %%v in (3.12 3.11 3.13) do (
  if not defined PY (
    py -%%v -c "import sys" >nul 2>nul && set "PY=py -%%v"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto sinpython

if not exist "venv\Scripts\python.exe" (
  echo Creando entorno por primera vez...
  %PY% -m venv venv || goto sinpython
)

rem ---- Instalar (solo la primera vez o tras una actualizacion) ----
set "INST="
if exist "venv\.instalado" set /p INST=<"venv\.instalado"
if not "%INST%"=="%VERSION%" (
  echo.
  echo ================================================================
  echo   Primera vez: instalando el programa y los idiomas.
  echo   Descarga 1-2 GB y puede tardar entre 5 y 15 minutos.
  echo   No cierres esta ventana.
  echo ================================================================
  echo.
  "venv\Scripts\python.exe" -m pip install --upgrade pip
  "venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fallo
  "venv\Scripts\python.exe" descargar_idiomas.py || goto fallo
  >"venv\.instalado" echo %VERSION%
  echo.
  echo Creando acceso directo en el Escritorio...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Traductor de Pantalla.lnk')); $s.TargetPath='%~dp0INICIAR_Windows.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()" >nul 2>nul
  echo.
  echo Instalacion terminada. Abriendo el traductor...
  timeout /t 3 >nul
)

start "" "venv\Scripts\pythonw.exe" traductor_pantalla.py
exit /b 0

:sinpython
echo.
echo No encontre Python. Instalalo desde https://www.python.org/downloads/
echo (version 3.12 recomendada) y marca la casilla "Add Python to PATH".
echo Luego vuelve a abrir este archivo.
pause
exit /b 1

:fallo
echo.
echo Algo fallo durante la instalacion. Revisa tu conexion a internet y
echo vuelve a abrir este archivo (continuara donde se quedo).
pause
exit /b 1

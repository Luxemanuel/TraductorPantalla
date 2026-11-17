@echo off
chcp 65001 >nul
title Traductor de Pantalla - Diagnostico
cd /d "%~dp0"
rem Abre el traductor mostrando los mensajes, por si algo falla.
if not exist "venv\Scripts\python.exe" (
  echo Primero abre INICIAR_Windows.bat para instalar el programa.
  pause
  exit /b 1
)
echo Abriendo el traductor en modo diagnostico. Si aparece un error, haz
echo una captura de esta ventana.
echo.
"venv\Scripts\python.exe" traductor_pantalla.py
echo.
echo --- Ultimas lineas de registro.txt ---
if exist registro.txt powershell -NoProfile -Command "Get-Content registro.txt -Tail 25"
pause

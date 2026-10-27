@echo off
rem Genera el respaldo del dataset definitivo (contiene datos del cliente: no subir a Git).
cd /d "%~dp0"
call venv\Scripts\activate.bat
python manage.py exportar_dataset_definitivo
echo.
pause

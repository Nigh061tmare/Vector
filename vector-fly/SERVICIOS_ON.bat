@echo off
rem Arranca sidecar (4711) + visor (4712) del cerebro de la mosca
set "FLY_DATA=C:\Users\Jose Luis\fly-data"
set "PY=C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe"
start "fly_server" /min "%PY%" "C:\Users\Jose Luis\vector-fly\fly_server.py" --port 4711 --device cpu --rate 10
timeout /t 12 /nobreak >nul
start "fly_live" /min "%PY%" "C:\Users\Jose Luis\vector-fly\fly_live.py"

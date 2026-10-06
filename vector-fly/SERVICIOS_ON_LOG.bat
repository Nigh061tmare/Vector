@echo off
rem Arranca sidecar (4711) + visor (4712) del cerebro-mosca, con logs a archivo
set "FLY_DATA=C:\Users\Jose Luis\fly-data"
set "PY=C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe"
set "DIR=C:\Users\Jose Luis\vector-fly"
rem Vision semantica: VL LOCAL (llama.cpp :8081) y ciclo cada 2.5s
set "SCENE_PROVIDER=llamacpp"
set "SCENE_EVERY=2.5"
set "VECTOR_SNAPSHOT_URL=http://127.0.0.1:8000/api/snapshot"

start "fly_server" /min cmd /c ""%PY%" "%DIR%\fly_server.py" --port 4711 --device cpu --rate 10 > "%DIR%\fly_server.out.log" 2>&1"
timeout /t 14 /nobreak >nul
start "fly_live" /min cmd /c ""%PY%" "%DIR%\fly_live.py" > "%DIR%\fly_live.out.log" 2>&1"

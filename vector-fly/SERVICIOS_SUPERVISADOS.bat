@echo off
rem Como SERVICIOS_ON_LOG.bat, pero cada proceso se RELANZA solo si muere (sin test en hardware real).
set "FLY_DATA=C:\Users\Jose Luis\fly-data"
set "PY=C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe"
set "DIR=C:\Users\Jose Luis\vector-fly"
set "SCENE_PROVIDER=llamacpp"
set "SCENE_EVERY=2.5"
set "VECTOR_SNAPSHOT_URL=http://127.0.0.1:8000/api/snapshot"
rem FLY_WEBCAM=1 reactiva el respaldo de webcam local (este PC no tiene)
set "FLY_WEBCAM=0"

start "fly_server (sup)" /min cmd /c "call :loop_server"
timeout /t 14 /nobreak >nul
start "fly_live (sup)" /min cmd /c "call :loop_live"
exit /b 0

:loop_server
"%PY%" "%DIR%\fly_server.py" --port 4711 --device cpu --rate 10 >> "%DIR%\fly_server.out.log" 2>&1
echo [%date% %time%] fly_server termino, relanzo en 3 s >> "%DIR%\fly_server.out.log"
timeout /t 3 /nobreak >nul
goto loop_server

:loop_live
"%PY%" "%DIR%\fly_live.py" >> "%DIR%\fly_live.out.log" 2>&1
echo [%date% %time%] fly_live termino, relanzo en 3 s >> "%DIR%\fly_live.out.log"
timeout /t 3 /nobreak >nul
goto loop_live

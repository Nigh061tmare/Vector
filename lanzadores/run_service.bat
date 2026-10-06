@echo off
rem Lanzador de servicio Vector Ultra 13.5 (replica Activar Vector.bat, con log)
set "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=%USERPROFILE%\.anki_vector\Vector-F9E8-0090963d.cert"
set "GRPC_SSL_TARGET_NAME_OVERRIDE_ARG=Vector-F9E8"
set "GRPC_ARG_KEEPALIVE_TIME_MS=10000"
set "GRPC_ARG_KEEPALIVE_TIMEOUT_MS=5000"
set "GRPC_ARG_KEEPALIVE_PERMIT_WITHOUT_CALLS=1"
set "GRPC_ARG_HTTP2_MAX_PINGS_WITHOUT_DATA=0"

net use Z: "\\192.168.1.85\Vault Obsidian" 1234 /user:192.168.1.85\vector /persistent:yes >nul 2>&1
echo ===== VECTOR SERVICE INICIO %date% %time% ===== >> "C:\Users\Jose Luis\vector_autonomo_out.log"
"%USERPROFILE%\VectorSDK\venv\Scripts\python.exe" "%USERPROFILE%\vector_network_guard.py" >> "C:\Users\Jose Luis\vector_autonomo_out.log" 2>&1
"%USERPROFILE%\VectorSDK\venv\Scripts\python.exe" -u "%USERPROFILE%\vector_autonomo.py" >> "C:\Users\Jose Luis\vector_autonomo_out.log" 2>&1
echo ===== VECTOR SERVICE FIN %date% %time% EXIT=%ERRORLEVEL% ===== >> "C:\Users\Jose Luis\vector_autonomo_out.log"

@echo off
title VECTOR ULTRA 13.5 — NEURO-AUTONOMOUS COGNITIVE ENGINE
color 0A

echo ============================================================
echo  VECTOR ULTRA 13.5 -- NEURO-AUTONOMOUS COGNITIVE ENGINE
echo  [1] Web Dashboard : http://localhost:8000 / http://192.168.1.51:8000
echo  [2] Discord 2.0   : Bot Vector#5073 (!ayuda / !foto / !estado)
echo  [3] Obsidian Vault: RAG BM25 Indexado en RAM
echo  [4] Watchdog Core : Auto-Heal, Keepalive y Reconexion sin Caidas
echo ============================================================

:: 1. Conectar unidad de red Z: si hace falta
net use Z: "\\192.168.1.85\Vault Obsidian" 1234 /user:192.168.1.85\vector /persistent:yes >nul 2>&1

:: 2. Variables de entorno gRPC + Keepalive TCP anti-desconexión
set "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=%USERPROFILE%\.anki_vector\Vector-F9E8-0090963d.cert"
set "GRPC_SSL_TARGET_NAME_OVERRIDE_ARG=Vector-F9E8"
set "GRPC_ARG_KEEPALIVE_TIME_MS=10000"
set "GRPC_ARG_KEEPALIVE_TIMEOUT_MS=5000"
set "GRPC_ARG_KEEPALIVE_PERMIT_WITHOUT_CALLS=1"
set "GRPC_ARG_HTTP2_MAX_PINGS_WITHOUT_DATA=0"

:: 3. Verificación y reparación preventiva de IP y Token (Network Guard)
echo [*] Comprobando integridad de conexion e IP con Vector...
"%USERPROFILE%\VectorSDK\venv\Scripts\python.exe" "%USERPROFILE%\vector_network_guard.py"

:: 4. Lanzar motor principal
"%USERPROFILE%\VectorSDK\venv\Scripts\python.exe" "%USERPROFILE%\vector_autonomo.py"

pause

@echo off
title VECTOR ULTRA 13.5 - ARRANQUE TOTAL
color 0A
cd /d "%~dp0"
echo ============================================================
echo    VECTOR ULTRA 13.5  -  ARRANQUE TOTAL DEL SISTEMA
echo ============================================================
echo.

echo  [1/3] Vision local VL (Qwen2.5-VL) en :8081  ^(segundo plano^)
netstat -ano | findstr ":8081" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
    start "VL-Qwen2.5" /min cmd /c "C:\Users\Jose Luis\llama-cpp\VL_ON.bat"
    echo        lanzado.
) else (
    echo        ya estaba en marcha.
)

echo.
echo  [2/3] Cerebro-mosca: sidecar :4711 + visor :4712  ^(segundo plano^)
netstat -ano | findstr ":4711" | findstr LISTENING >nul 2>&1
if errorlevel 1 (
    start "FlyBrain" /min cmd /c "C:\Users\Jose Luis\vector-fly\SERVICIOS_ON_LOG.bat"
    echo        lanzado.
) else (
    echo        ya estaba en marcha.
)

echo.
echo ============================================================
echo   CENTRO DE MANDO  ->  http://localhost:8000
echo   Visor neuronas   ->  http://localhost:4712
echo ============================================================
echo.
echo  [3/3] Nucleo Vector Ultra  ^(la web aparece en unos segundos^)
echo        - motor autonomo + voz + IA + camara en vivo
echo        - Dashboard web :8000
echo        - Discord 2.0
echo        - Protocolo de chirps ^(altavoz + microfono^)
echo.
echo  Nota: el cerebro-mosca y la vision tardan ~30-60s en cargar.
echo        El nucleo los engancha solo cuando esten listos.
echo.

set "TALK_ON=1"
set "TALK_MIC=K38"

rem pequena pausa para que el sidecar abra el puerto antes de engancharlo
timeout /t 8 /nobreak >nul

echo  Arrancando nucleo... ^(Ctrl+C para detener^)
echo.
rem El nucleo se lanza con su lanzador canonico (variables gRPC + log a fichero)
call "C:\Users\Jose Luis\VectorSDK\run_service.bat"

echo.
echo  El nucleo se ha detenido.
pause

@echo off
setlocal enabledelayedexpansion
title Vector-Fly :: Entrenamiento Continuo
rem ============================================================
rem  Entrena la mosca en bucle: reanuda desde la cache y sube
rem  el objetivo de episodios poco a poco.
rem  PARAR: crea el archivo STOP_CONTINUO.flag en esta carpeta,
rem         o cierra esta ventana.
rem ============================================================
set "ROOT=C:\Users\Jose Luis\vector-fly"
set "PY=C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe"
set "LOG=%ROOT%\decision_continuo.log"
set /a TARGET=1000
set /a STEP=400
set /a MAXEP=3000

echo ===== CONTINUO INICIO %date% %time% ===== >> "%LOG%"
:loop
if exist "%ROOT%\STOP_CONTINUO.flag" goto fin
if %TARGET% GTR %MAXEP% goto fin
echo. >> "%LOG%"
echo ==== [%date% %time%] objetivo %TARGET% episodios ==== >> "%LOG%"
"%PY%" -u "%ROOT%\train_decision.py" %TARGET% >> "%LOG%" 2>&1
echo ==== [%date% %time%] terminado objetivo %TARGET% ==== >> "%LOG%"
set /a TARGET+=STEP
timeout /t 15 /nobreak >nul
goto loop
:fin
echo ===== CONTINUO FIN %date% %time% ===== >> "%LOG%"
echo done > "%ROOT%\DECISION_DONE.flag"

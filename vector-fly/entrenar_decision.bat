@echo off
setlocal
title Vector-Fly :: Entrenar Decision
rem ============================================================
rem  Entrena el readout de decision de la mosca (navegacion).
rem  Uso:
rem     doble clic              -> 600 episodios
rem     ENTRENAR_DECISION.bat 1200
rem     ENTRENAR_DECISION.bat 600 --fresh   (ignora la cache)
rem  La cache (decision_dataset.npz) evita repetir la recoleccion
rem  si el entreno se corta: al relanzar continua donde iba.
rem ============================================================
set "ROOT=C:\Users\Jose Luis\vector-fly"
set "PY=C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe"
set "LOG=%ROOT%\decision_train.log"
set "FLAG=%ROOT%\DECISION_DONE.flag"
if "%~1"=="" (set "EP=600") else (set "EP=%~1")
if not exist "%PY%" (echo ERROR: no encuentro el python del venv & pause & exit /b 1)
del "%FLAG%" >nul 2>&1
cd /d "%ROOT%"
echo ============================================================
echo   VECTOR-FLY :: ENTRENAR DECISION
echo   episodios : %EP%
echo   python    : %PY%
echo   log       : %LOG%
echo ============================================================
echo.
"%PY%" -u train_decision.py %EP% %2
set RC=%ERRORLEVEL%
echo.
echo ============================================================
echo   TERMINADO  (codigo %RC%)
echo ============================================================
echo done > "%FLAG%"
pause
@echo off
rem Entrenamiento en segundo plano (sin pausa, con marca de fin)
set "ROOT=C:\Users\Jose Luis\vector-fly"
set "PY=C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe"
set "LOG=%ROOT%\decision_train.log"
del "%ROOT%\DECISION_DONE.flag" >nul 2>&1
echo ===== INICIO %date% %time% ===== > "%LOG%"
"%PY%" -u "%ROOT%\train_decision.py" %1 >> "%LOG%" 2>&1
echo EXIT=%ERRORLEVEL% >> "%LOG%"
echo ===== FIN %date% %time% ===== >> "%LOG%"
echo done > "%ROOT%\DECISION_DONE.flag"

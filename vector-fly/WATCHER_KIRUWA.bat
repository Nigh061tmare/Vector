@echo off
title Watcher @kiruwaaaaaa (Flyctor)
cd /d "C:\Users\Jose Luis\vector-fly"
echo Vigilando los posts de @kiruwaaaaaa cada 15 min...
echo Log: C:\Users\Jose Luis\vector-fly\kiruwa_posts.log
echo (Ctrl+C para parar)
"C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe" -u "C:\Users\Jose Luis\vector-fly\watcher_kiruwa.py"
pause

@echo off
rem Servidor VL local (Qwen2.5-VL-3B) en :8081 para la vision semantica de la mosca
cd /d "C:\Users\Jose Luis\llama-cpp"
llama-server.exe ^
  -m "models\Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf" ^
  --mmproj "models\mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf" ^
  -ngl 999 -c 4096 --host 127.0.0.1 --port 8081 ^
  > "C:\Users\Jose Luis\llama-cpp\vl_server.log" 2>&1

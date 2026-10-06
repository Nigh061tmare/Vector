# VECTOR ULTRA 13.5 — ESTADO, ARQUITECTURA Y LECCIONES

> Documento de referencia para no repetir errores. Actualizado: 2026-09-21.

---

## 1. TOPOLOGÍA Y RUTAS

| Elemento | Valor |
|---|---|
| Host | Windows 11 Mini PC `192.168.1.51` |
| Usuario | `Jose Luis` |
| Directorio raíz | `C:\Users\Jose Luis\` |
| **VENV obligatorio** | `C:\Users\Jose Luis\VectorSDK\venv\Scripts\python.exe` |
| VENV cerebro-mosca | `C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe` |
| Robot | `Vector-F9E8` · serial `0090963d` · IP `192.168.1.82` |
| Certificado gRPC | `C:\Users\Jose Luis\.anki_vector\Vector-F9E8-0090963d.cert` |
| Wire-Pod (servidor) | `192.168.1.85:8080` (y `.82`) · Tailscale `100.100.148.91` · usuario `pepde` |
| Vault Obsidian | `\\192.168.1.85\Vault Obsidian` (unidad `Z:`) |
| Lanzador oficial | `Desktop\Vector\Activar Vector.bat` |
| **Lanzador total** | `Desktop\Vector\TODO_ON.bat` |

### Variables gRPC OBLIGATORIAS
Sin estas dos, el handshake TLS falla y el SDK reporta
"Make sure you're on the same network" **aunque el robot esté bien**:
```bat
set "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=%USERPROFILE%\.anki_vector\Vector-F9E8-0090963d.cert"
set "GRPC_SSL_TARGET_NAME_OVERRIDE_ARG=Vector-F9E8"
```

---

## 2. SERVICIOS Y PUERTOS

| Puerto | Servicio | Lanzador |
|---|---|---|
| 8000 | **NEXUS** (web unificada) + API | dentro del núcleo |
| 4711 | FlyBrain sidecar (connectome) | `vector-fly\SERVICIOS_ON_LOG.bat` |
| 4712 | Visor de neuronas | idem |
| 8081 | VL local Qwen2.5-VL-3B | `llama-cpp\VL_ON.bat` |
| 11434 | Ollama | servicio propio |

### Orden de arranque (IMPORTANTE)
`TODO_ON.bat` lanza VL y cerebro **en segundo plano** y el núcleo **de inmediato**.
La web aparece en **~16 s**. Antes tardaba 74 s porque esperaba 30 s + 35 s.
El núcleo **engancha el cerebro-mosca cuando esté listo** (reintento cada 15 s en
`FlyReflex.online`).

---

## 3. ARCHIVOS CLAVE

| Archivo | Función |
|---|---|
| `vector_autonomo.py` (~5100 líneas) | Núcleo: motor autónomo, IA, voz, watchdog |
| `vector_dashboard.py` | Web (FastAPI) + NEXUS + MJPEG |
| `vector_nexus.html` | Interfaz unificada (se sirve desde `/`) |
| `vector_talk.py` | Protocolo de chirps: síntesis y decodificación |
| `vector_talk_service.py` | Emisor (altavoz) + receptor (micrófono) |
| `vector_fly.py` | `FlyReflex`: puente núcleo ↔ cerebro-mosca |
| `vector_network_guard.py` | Auto-heal de IP y token Wire-Pod |
| `vector_rag.py` | RAG BM25 del Vault |
| `vector_log.py` | Logging (UTF-8 forzado) |
| `vector-fly/fly_core.py` | Connectome MaleCNS (166.700 neuronas) |
| `vector-fly/fly_server.py` | Sidecar a 50 Hz |
| `vector-fly/fly_live.py` | Visor + visión semántica |
| `VectorSDK\run_service.bat` | Lanzador canónico del núcleo (con log) |

---

## 4. LECCIONES APRENDIDAS (BUGS REALES Y SU CAUSA)

### 4.1 Cámara (el más importante)
1. **`robot.camera.latest_image` BLOQUEA** (hasta 5 s) y **lanza excepción** si no
   hay frame. Nunca llamarlo desde el bucle principal → **hilo dedicado**.
2. **El SDK puede devolver SIEMPRE la misma imagen sin lanzar error.** Hay que
   detectarlo con `robot.camera.latest_image_id` (cambia con cada frame).
   Sin esto la web muestra una foto congelada creyendo que va bien.
3. **El stream del robot se atasca.** Un `init_camera_feed()` directo NO basta:
   la secuencia que funciona es `close_camera_feed()` → **esperar 3 s** →
   `init_camera_feed()`. Verificado empíricamente.
4. **El hilo de cámara debe ser SINGLETON.** Cada reconexión crea un `Robot`
   nuevo; si no se para el hilo anterior se acumulan hilos zombis (crítico 24h).
5. `capture_single_image()` **puede colgarse indefinidamente** — no usarlo.
6. **El generador MJPEG no debe quedarse sin emitir**: si no hay frame, emitir un
   placeholder; si no, el navegador se cuelga (timeout).

### 4.2 Red / autenticación
7. Error `401 UNAUTHENTICATED` → renovar token con Wire-Pod
   (`vector_network_guard.renovar_token_wirepod`).
8. El SDK **cachea el GUID** al crear el `Robot`; tras renovar hay que reconectar.
9. Vector cambia de IP → `vector_network_guard` la resuelve por mDNS y actualiza
   `sdk_config.ini`.

### 4.3 Cerebro-mosca
10. **Crash `operands could not be broadcast (128,) (160,)`**: la visión semántica
    genera un pano de 128 y la cámara de 160. Guardar la forma antes de operar.
11. **La escena se descartaba** por una indentación rota en `fly_server.py`.
12. La ventana de habituación debe ser ≥ el periodo de la visión (si no, parpadea).

### 4.4 Web
13. **Desajuste de nombres de campo**: `/api/vault` esperaba `query` y el NEXUS
    enviaba `consulta` → búsqueda vacía → 40 s de escaneo. Aceptar ambos.
14. **La primera consulta RAG tarda ~30 s** (carga del índice) → precalentar en
    segundo plano al arrancar. Ahora responde en ~2 ms.
15. El token del dashboard debe ser **persistente** en `.env` (si está vacío se
    genera uno aleatorio en cada arranque y los clientes fallan con 401).
16. Comparar el token con `hmac.compare_digest` (evita timing attacks).

### 4.5 Logging
17. Con la salida redirigida, Windows usa cp1252 y **los emojis rompen el logger**
    → forzar UTF-8 en stdout/stderr (`vector_log.py`).
18. **Los `print()` se bufferizan** al redirigir; usar `python -u` o `log.info`.

### 4.6 Audio / chirps
19. **El feed del micrófono de Vector NO está implementado en el SDK**
    (`enable_audio_feed` es un TODO). Se usa el **micrófono del PC** (`K38`).
20. El altavoz de Vector solo acepta **WAV 8000-16025 Hz, 16 bits, mono**.
21. El decodificador debe **validar la frecuencia** contra el vocabulario y exigir
    confianza mínima, o el ruido ambiente se clasifica como señales (falsos
    positivos medidos: 25/25 antes del arreglo, 0/25 después).

---

## 5. PROTOCOLO DE CHIRPS (Flyctor)

### Bucle sensorial
```
cámara → VL → cerebro-mosca → ALTAVOZ        (emitir)
micrófono → decodificador → cerebro-mosca    (oír)
```

### Vocabulario
| id | significado | forma | frecuencia | duración |
|---|---|---|---|---|
| `objeto` | "encontré un objeto" | 1 chirp | 1200 Hz | 120 ms |
| `acercate` | "acércate" | 2 chirps | 1200 Hz | 120 ms |
| `bloqueado` | "camino bloqueado" | 1 tono largo | 800 Hz | 600 ms |
| `ayuda` | "necesito ayuda" | 4 chirps rápidos | 1600 Hz | 60 ms |

### Mapa señal → evento sensorial de la mosca
| señal | bearing | distancia | amenaza |
|---|---|---|---|
| `objeto` | 0° | 260 mm | 0.0 |
| `acercate` | 0° | 190 mm | 0.0 |
| `bloqueado` | 0° | 90 mm | 0.95 |
| `ayuda` | 0° | 140 mm | 0.70 |

### API
- `GET /api/talk` → estado (vocabulario, contadores, historial)
- `POST /api/talk {"signal": "objeto"}` → emite por el altavoz de Vector

---

## 6. ARRANQUE Y PARADA

**Arrancar todo:** doble clic en `Desktop\Vector\TODO_ON.bat`
**Solo el núcleo:** `VectorSDK\run_service.bat`
**Log:** `C:\Users\Jose Luis\vector_autonomo_out.log`

**Parar:**
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match "vector_autonomo|fly_server|fly_live" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-Process llama-server | Stop-Process -Force
```

---

## 7. COMPROBACIÓN RÁPIDA (CHECKLIST)

```powershell
# 1. Servicios
foreach ($p in 8081,4711,4712,8000) { Get-NetTCPConnection -LocalPort $p -State Listen }

# 2. Salud
Invoke-RestMethod http://127.0.0.1:8000/api/health
#   → motor_online, camara_viva, frame_edad_s

# 3. Cámara viva (el frame debe CAMBIAR de md5 entre muestras)
Invoke-WebRequest http://127.0.0.1:8000/api/snapshot

# 4. Cerebro-mosca
Invoke-RestMethod http://127.0.0.1:8000/api/fly

# 5. Chirps
Invoke-RestMethod http://127.0.0.1:8000/api/talk
```

---

## 8. PENDIENTE / LIMITACIONES CONOCIDAS

- **Cámara del robot degradada** (2026-09-21): entrega ~0,07 fps en vez de 15-30.
  Verificado con conexión directa (0 frames en 64 s). **Requiere reinicio físico
  de Vector** (botón de la espalda 15-20 s + cargador). No es un fallo del software.
- **El micrófono de Vector no es accesible** por el SDK → se usa el del PC.
- Un **segundo Vector** requeriría su propio PC con micrófono para conversar.

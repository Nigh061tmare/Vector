# VECTOR-FLY — Cerebro de mosca real controlando a Vector

Integra el **connectome MaleCNS v1.0** (166.700 neuronas, 25,6 M conexiones,
Janelia FlyEM) como **reflejo sensoriomotor** de Vector, replicando y
superando el proyecto viral de la mosca en el robot Vector.

## Qué hace

La cámara/ToF de Vector alimenta las **neuronas visuales reales** de la mosca
(LPLC2 = looming, LC4 = amenaza/escape, LC10a = persecución). La actividad de
las **neuronas descendentes** (DNp01/02, DNg01) se traduce a órdenes de rueda.
Resultado: Vector **huye de forma refleja** cuando algo se le acerca, sin IA,
sin guion: neurodinámica real a 50 Hz.

## Arquitectura

```
Vector (venv VectorSDK)              Sidecar (venv flybrain, Py3.13)
┌──────────────────────┐   TCP      ┌────────────────────────────┐
│ vector_fly.py        │◄──────────►│ fly_server.py              │
│  FlyReflex           │  4711      │  fly_core.py → flybrain    │
│  ToF/bearing →       │            │  MaleCNS v1.0 (166.700 n)  │
│  → ruedas Vector     │            │  50 Hz, CPU 147 Hz         │
└──────────────────────┘            └────────────────────────────┘
```

## Rendimiento medido (i7-9700K, CPU)

| Métrica | Valor |
|---|---|
| Neuronas | 166.700 |
| Pasos | 6,82 ms/paso (147 Hz) |
| Tiempo real | 2,93× (la mosca va más rápido que la biología) |
| Reflejo escape | latencia 2 pasos = 40 ms |
| Fallback GPU | CuPy `cupy-cuda12x` instalado (RTX 3060) |

## Uso

```powershell
# 1. Arrancar el sidecar (cerebro de mosca)
$env:FLY_DATA="C:\Users\Jose Luis\fly-data"
& "C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe" "C:\Users\Jose Luis\vector-fly\fly_server.py" --port 4711 --device cpu

# 2. Probar el reflejo (sin robot)
& "C:\Users\Jose Luis\VectorSDK\venv\Scripts\python.exe" "C:\Users\Jose Luis\vector_fly.py"

# 3. Demo visual (genera MP4 + PNG)
& "C:\Users\Jose Luis\flybrain-venv\Scripts\python.exe" "C:\Users\Jose Luis\vector-fly\demo_escape.py"

# 4. Activar en Vector: en .env -> MODO_FLYBRAIN=1
```

## Direccionalidad verificada

| Amenaza | Rueda Izq | Rueda Der | Giro |
|---|---|---|---|
| IZQUIERDA | +1.00 | +0.38 | derecha (huye) |
| DERECHA | +0.38 | +1.00 | izquierda (huye) |

## Archivos

| Archivo | Rol |
|---|---|
| `vector-fly/fly_core.py` | Núcleo: connectome + encoder visual + decoder motor |
| `vector-fly/fly_server.py` | Sidecar TCP, loop 50 Hz, telemetría |
| `vector-fly/fly_client.py` | Cliente stdlib (sin deps) |
| `vector-fly/demo_escape.py` | Demo visual (MP4/PNG) + narración Ollama |
| `vector-fly/smoke_test.py` | Test del reflejo de escape |
| `vector-fly/bench_gpu.py` | Benchmark CPU/GPU |
| `../vector_fly.py` | Integración con Vector (`FlyReflex`) |

## Datos

`C:\Users\Jose Luis\fly-data` (247 MB):
- `brain.npz` (51 MB): neuronas, tipos, grupos motores, descendentes
- `weights.npz` (196 MB): matriz de sinapsis del connectome

## Modelo IA local elegido

**`qwen3.5:9b`** (Ollama) — 46 tok/s, mejor calidad/velocidad en español.
Cadena: Ollama local → OpenRouter (2 claves) → Gemini.

---

## Capacidades añadidas (v2)

### 👁️ Retina (la mosca ve)
`fly_live.py` capturadores + `fly_core.camera_inject()` convierten una imagen
(1-D o 2-D) en estímulo para las 6.006 fotorreceptores. Verificado: una barra
oscura que se acerca dispara LC4 (0,08→0,30) y activa el modo escape.
El visor dibuja el **panorama exacto** que entra al cerebro.

### 🧠 Readout entrenado (aprendizaje por reservorio)
`train_readout.py` entrena dos clasificadores lineales sobre la actividad de
2.000 neuronas muestreadas (brain congelado + readout aprendido):

| Tarea | CV AUC | Modelo |
|---|---|---|
| Looming vs nada | **1.000** | `models/loom.npz` |
| Lado izq vs der | **1.000** | `models/side.npz` |

En vivo: `LRN_loom` pasó de 0,06 (reposo) a **0,96** con un estímulo looming.
Se muestra en el visor y en el snapshot del sidecar.

### 💬🎭 Reacción expresiva + memoria
Al detectar escape (flanco), Vector dice una frase, pone ojos de "asustado" y
registra el suceso con `rec(m, "susto", ...)` en `memoria_vector.json`.

### 📊 Panel FlyBrain en el dashboard
`vector_dashboard.py` incluye `/api/fly` (proxy) y un panel con la retina,
LC4, LPLC2, escape, modo y ruedas, más enlace al visor completo.

## Visor en vivo

- `Desktop\Neuronas en vivo.bat` → http://127.0.0.1:4712/
- Muestra: banda de actividad (128 regiones), poblaciones, descendentes,
  ruedas, modo, retina y readouts aprendidos. Botones de estímulo tipo looming.

## Lanzadores

| Lanzador | Función |
|---|---|
| `Desktop\FlyBrain Vector.bat` | Arranca el sidecar (cerebro) |
| `Desktop\Neuronas en vivo.bat` | Arranca el visor web + sidecar |
| `Desktop\Activar Vector.bat` | Arranca Vector (usa `MODO_FLYBRAIN=1`) |

## Config (.env)

```
MODO_FLYBRAIN=0          # 1 = reflejo activo en el bucle de Vector
FLY_HOST=127.0.0.1
FLY_PORT=4711
FLY_VISION=1             # alimentar el cerebro con la cámara de Vector
OLLAMA_MODEL_TEXTO=qwen3.5:9b
```


# Auditoría Fase 0 (honesta, sin robot)

**Entorno de la auditoría:** contenedor Linux en la nube. No hay robot, ni Windows, ni red local,
y **el repositorio no contiene `vector-fly/`** (`fly_core.py`, `fly_server.py`, `fly_live.py`, `fly_client.py`),
ni `.env`/`sdk_config.ini`. No se pudieron arrancar los 4 servicios ni capturar FPS/spikes/telemetría "antes".
Lo que sigue es análisis estático + tests con robot simulado. Git ya existía (1 commit); no se reinicializó.

## Verificado contra el SDK real (`pip install anki_vector`, v0.6)
- `robot.motors.set_wheel_motors(left_mmps, right_mmps, left_accel, right_accel)` **existe**: el supuesto
  "el SDK no expone ruedas individuales" es falso. No hacen falta arcos emulados con `turn`+`go_forward`.
- `enable_audio_feed` existe como flag en `Robot(...)` pero el feed de audio de Vector sigue sin estar usable
  desde el SDK (coincide con tu lección 4.6-19). No lo he reprobado en hardware.

## Causa de los "tropezones" (código real)
1. Ruedas del reflejo mosca: `set_wheel_motors(v,v)` → `sleep(0.35/0.6)` → `(0,0)`, sin aceleración (arranque/parada
   instantáneos) y cada 1.2 s / 5 s. → **Arreglado para el modo wander** con `rodar_suave()` (rampas); el escape
   se deja a propósito instantáneo (es un reflejo).
2. `avanzar_seguro` avanza en tramos de 12 mm (`TRAMO_MM`) con una lectura ToF bloqueante entre tramos: cada tramo
   es un RPC con aceleración/parada propias. **No tocado** (necesita probar en el robot; propuesta: bucle continuo con
   `MotionController` + ToF a 10 Hz).
3. El clip independiente `±120` por rueda deformaba los arcos al saturar. → `diff_drive` normaliza por el pico.

## Bug de batería (confirmado leyendo el código)
`bat_volts()` devolvía 0.0 ante fallo y el bloque "0.6 Gestión de batería" hacía `volts < 3.55` ⇒ **tomaba 0 V como
batería baja y mandaba `drive_on_charger()`**; la telemetría publicaba 0 %. → Ahora hay caché (≤120 s), umbral de lectura
válida (2.5 V) y no se decide con lecturas fallidas. Con tests.

## Pendiente / no verificable aquí (sin código fuente o sin robot)
Visor `fly_live.py` (fallback webcam), 401 gRPC en clientes externos, TTL de escena 8 s/habituación 7 s,
FSM de comportamiento, mapa de ocupación, chirps ampliados, supervisor, UI. Los tres `fly_*.py` hay que subirlos al repo.

## Deuda detectada
7 copias de seguridad `.py/.bak` (≈800 KB) y `vector_autonomo_out.log` (857 KB) versionados; `vector_autonomo.py` 5180 líneas
con `except Exception: pass` generalizado; ruta `C:\Users\Jose Luis\vector-fly` hardcodeada en `vector_fly.py`;
`test*.py` sueltos son scripts manuales, no tests.

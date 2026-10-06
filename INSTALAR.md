# Cómo incorporar los cambios en tu PC (Windows)

Todo está en `Vector_Ultra_ENTREGA.zip`, con la **misma estructura de carpetas** que tu PC:
lo de la raíz va a `C:\Users\Jose Luis\`, lo de `vector-fly\` a `C:\Users\Jose Luis\vector-fly\`.

## 0. Antes de nada (2 minutos)
1. **Para los servicios** (cierra TODO_ON). Haz una copia: `xcopy "C:\Users\Jose Luis" ...` o, mínimo,
   copia `vector_autonomo.py`, `vector_dashboard.py`, `vector_nexus.html`, `vector_talk.py`,
   `vector_network_guard.py` y `vector-fly\fly_core.py`, `vector-fly\fly_live.py` a una carpeta `backup_antes\`.
2. ⚠️ **Tu `vector_autonomo.py` puede haber cambiado desde que subiste el RAR.** Si lo has editado después,
   NO lo sobrescribas a ciegas: aplica `CAMBIOS.patch` (o compara con WinMerge/VS Code) y fusiona.
3. ⚠️ **Seguridad:** el repo de GitHub contiene `vector_autonomo_out.log` con GUIDs reales y el
   `global_guid` de Wire-Pod estaba en el código. Renueva tokens (`python vector_network_guard.py`) y, si el
   repo es público, hazlo privado o purga el historial. Nuevo: el GUID global se lee de la variable de entorno
   `WIREPOD_GLOBAL_GUID` (si no existe, usa el valor antiguo).

## 1. Copiar
Descomprime el zip **encima** de `C:\Users\Jose Luis\` (acepta reemplazar los 7 archivos modificados).
Archivos nuevos: `vector_motion.py vector_behavior.py vector_gaze.py vector_map.py vector_life.py
vector_facts.py vector_vad.py vector_supervisor.py smoke_*.py tests\ docs\`.

## 2. Dependencias
Nada obligatorio nuevo: usan `numpy` y `PIL` que ya tienes en `VectorSDK\venv`. Para correr los tests:
```bat
"C:\Users\Jose Luis\VectorSDK\venv\Scripts\python.exe" -m pip install pytest
"C:\Users\Jose Luis\VectorSDK\venv\Scripts\python.exe" -m pytest -q tests
```
(Los tests de `fly_live`/escena necesitan `opencv-python-headless`; si fallan por eso, ignóralos con `--ignore`.)

## 3. Comprobar SIN riesgo (sin mover el robot)
```bat
python smoke_movimiento.py --dry     rem perfil de rampas
python smoke_vida.py --sim           rem simulación 2-D: mapa + línea de tiempo
python vector_talk.py                rem 9/9 chirps
```

## 4. Probar con el robot (en este orden, suelo libre, a mano del botón)
1. Arranca como siempre (`TODO_ON.bat`). Todo lo nuevo gordo está **apagado por defecto**; ya notarás:
   rampas en el wander del cerebro-mosca, batería sin falsos "0 V", escucha más robusta, visor que no muere.
2. `python smoke_movimiento.py` (Vector arranca, curva y frena). Si lo sientes brusco o lento, ajusta
   `ACCEL_MMPS2`, `DECEL_MMPS2`, `DAMPING_ALPHA` en `vector_motion.py`.
3. Activa la vida animal: `set MODO_VIDA=1` antes de lanzar el núcleo (en `run_service.bat`).
   NEXUS mostrará el panel **Vida**. Quitarlo = quitar la variable.
4. Avance continuo (opcional): `set AVANZAR_CONTINUO=1`.
5. Servicios que se reinician solos: usa `vector-fly\SERVICIOS_SUPERVISADOS.bat` en vez de `SERVICIOS_ON_LOG.bat`, y/o
   `python vector_supervisor.py` en una ventana aparte.

## 5. Qué cambia, en una línea cada uno
- **Nuevos comandos de voz:** "recuerda que mi gato se llama Misu" / "¿qué te dije del gato?".
- **Chirps:** 9 señales; responde ACK a las ajenas y no se oye a sí mismo.
- **Variables:** `MODO_VIDA`, `AVANZAR_CONTINUO`, `FLY_WEBCAM` (0 por defecto), `VECTOR_SEED`, `VECTOR_VIDA_STATE`.
- **Se guarda:** `vida_estado.json` (mapa y objetos recordados) junto a `vector_autonomo.py`.

## 6. Volver atrás
Restaura `backup_antes\` y borra `MODO_VIDA`. Los módulos nuevos son inertes si nadie los importa.

## 7. Qué NO está probado (dímelo si algo falla y lo corrijo)
Hardware real (ruedas, cabeza, pantalla, ToF, pose), micrófono/Vosk, Qwen-VL, Windows `.bat`, el cerebro-mosca
con `flybrain` real. Todo lo demás tiene tests (ver `docs/VIDA_ANIMAL.md`).

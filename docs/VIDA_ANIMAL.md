# Vida animal de Vector (arquitectura nueva)

```
sensores ──► vector_life.Vida.step() ──► Behavior (FSM + personalidad + estigmergia + atasco)
 (ToF, pose,                │                     │ decision: linear/turn, ojos, razon
  bateria, escena VL)       │                     ▼
                            ├──► MotionController ──► robot.motors.set_wheel_motors(L, R, accel)  [rampas 20 Hz]
                            ├──► GazeController  ──► robot.motors.set_head_motor  [sacadas + microtemblor]
                            ├──► EyeState        ──► robot.screen.set_screen_with_image_data  [pupila/parpado]
                            └──► OccupancyGrid + ObjectMemory  [mapa local, objetos recordados]
```

| Modulo | Responsabilidad | Tests |
|---|---|---|
| `vector_motion.py` | (linear,turn)->L/R conservando arco, rampas, hombre muerto, parada por borde/levantado | `test_vector_motion.py` |
| `vector_behavior.py` | FSM DORMIR/DESPERTAR/EXPLORAR/ACERCARSE/JUGAR/HUIR/VUELTA_A_BASE, personalidad por semilla, atasco, estigmergia, timeline | `test_vector_behavior.py` |
| `vector_gaze.py` | sacadas/fijaciones, pupila (miedo contrae, sorpresa dilata), parpadeo | `test_gaze_map.py` |
| `vector_map.py` | rejilla log-odds, frontera, zona segura, memoria de objetos con movimiento y olvido | `test_gaze_map.py` |
| `vector_life.py` | integra todo; `vivir()` lo llama el nucleo; simulador sin robot | `test_vector_life.py` |
| `vector_talk.py` | 9 chirps + `ChirpProtocol` (ACK, eco propio) | `test_vector_talk.py` |
| `vector_vad.py` | VAD adaptativo + wake word | `test_vad.py` |
| `vector_supervisor.py` | relanza servicios caidos con backoff | `test_supervisor.py` |

## Activarlo (opt-in)
`set MODO_VIDA=1` antes de lanzar el nucleo: `ejecutar()` usa `vector_life.vivir()` en vez de `explorar()`.
Si falla, vuelve solo a `explorar()`. NEXUS muestra el panel "Vida" (`/api/vida`) cuando esta activo.

## Estado de verificacion
Probado: logica pura y simulacion 2-D (tests). NO probado: hardware real, camara, gRPC, audio, Windows.
Parametros a afinar en el robot: `ACCEL_MMPS2`/`DECEL_MMPS2` (vector_motion), `TOF_*` y `MIN_DWELL_S` (vector_behavior),
`FIX_MEDIAN_S` (vector_gaze).

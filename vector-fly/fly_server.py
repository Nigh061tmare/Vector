#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR-FLY :: SERVIDOR SIDECAR (cerebro de mosca como servicio)
# ============================================================
# Ejecuta el connectome MaleCNS v1.0 en tiempo real y lo expone
# por TCP con JSON por lineas, para que Vector (u otro) lo use sin
# cargar flybrain en su propio proceso.
#
#   python fly_server.py --port 4711
#
# Protocolo (una linea JSON por mensaje):
#   Cliente -> Servidor:
#     {"cmd":"sensory","opp":[dx,size],"threat":0.0,"shots":[]}
#     {"cmd":"camera","frame":[floats 0..1]}     # banda 1-D ya reducida
#     {"cmd":"reset","seed":64}
#     {"cmd":"status"}
#     {"cmd":"ping"}
#     {"cmd":"config","telemetry_hz":10,"mode":"rt"}
#   Servidor -> Cliente:
#     {"type":"hello","brain":{...}}
#     {"type":"telemetry","snapshot":{...},"motor":{...}}
#     {"type":"status","brain":{...},"fps":...}
#     {"type":"pong"}
#     {"type":"error","detail":"..."}
# ============================================================
from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import threading
import time
from typing import Any, Dict, Optional

from fly_core import FlyCore, cargar_core_desde_env


class FlyEngine:
    """Bucle autonomo a 50 Hz que mantiene el estado mas reciente."""

    def __init__(self, core: FlyCore, realtime: bool = True, telemetry_hz: float = 10.0) -> None:
        self.core = core
        self.realtime = realtime
        self.telemetry_hz = max(0.5, float(telemetry_hz))
        self._lock = threading.Lock()
        self._stop = threading.Event()

        # ultimo estimulo sensorial (persistente entre pasos)
        self._opp: Optional[list] = None
        self._opp_vel: tuple = (0.0, 0.0)
        self._opp_ts: float = 0.0
        self.extrapolate: bool = True
        self._threat: float = 0.0
        self._shots: list = []
        self._pending_camera: Optional[Any] = None
        self._prev_cam: Optional[Any] = None
        # escena semantica (vision tipo 'Astra') + deteccion de novedad
        self._pending_scene: Optional[list] = None
        self._seen_objects: Dict[str, float] = {}
        self.novelty_ttl: float = 45.0
        self.last_scene: Dict[str, Any] = {}

        self.snapshot: Dict[str, Any] = core.snapshot()
        self.motor: Dict[str, Any] = core.motor_command(self.snapshot)
        self.hist: list = [0] * 128
        self.hist_bins: int = 128
        self.eye: list = []
        self.eye_hz: float = 0.0
        self.seq = 0
        self.fps = 0.0
        self._thread: Optional[threading.Thread] = None

    # ---- control externo -------------------------------------
    def set_sensory(self, opp=None, threat: float = 0.0, shots=()) -> None:
        now = time.perf_counter()
        with self._lock:
            new_opp = [float(opp[0]), float(opp[1])] if opp is not None else None
            # velocidad angular del objeto (para extrapolar el looming entre mensajes)
            if new_opp is not None and self._opp is not None:
                dtm = max(now - self._opp_ts, 1e-3)
                self._opp_vel = ((new_opp[0] - self._opp[0]) / dtm,
                                 (new_opp[1] - self._opp[1]) / dtm)
            elif new_opp is not None:
                self._opp_vel = (0.0, 0.0)
            self._opp = new_opp
            self._opp_ts = now
            self._threat = float(threat or 0.0)
            self._shots = list(shots or ())

    def set_camera(self, frame) -> None:
        with self._lock:
            self._pending_camera = frame

    def set_scene(self, objects) -> None:
        """Recibe una escena semantica y marca los objetos NUEVOS (novedad).

        `objects`: [{'name':str, 'side':-1..1, 'near':0..1}]
        Un objeto es 'novel' si no se veia desde hace novelty_ttl segundos.
        """
        now = time.time()
        objs: list = []
        with self._lock:
            for o in (objects or ()):
                if not isinstance(o, dict):
                    continue
                name = str(o.get("name", "")).strip().lower()[:24]
                if not name:
                    continue
                last = self._seen_objects.get(name, 0.0)
                novel = (now - last) > self.novelty_ttl
                self._seen_objects[name] = now
                try:
                    side = max(-1.0, min(1.0, float(o.get("side", 0.0))))
                    near = max(0.0, min(1.0, float(o.get("near", 0.25))))
                except Exception:
                    side, near = 0.0, 0.25
                objs.append({"name": name, "side": side, "near": near,
                             "novel": bool(o.get("novel", novel))})
            # limpiar memoria de objetos muy antiguos
            if len(self._seen_objects) > 64:
                cut = now - 6 * self.novelty_ttl
                self._seen_objects = {k: v for k, v in self._seen_objects.items()
                                      if v >= cut}
        if objs:
            self._pending_scene = objs
            self.last_scene = {"t": round(now, 3), "objects": objs,
                               "novel": [o["name"] for o in objs if o["novel"]]}

    def reset(self, seed: Optional[int] = None) -> None:
        with self._lock:
            self.core.reset(seed)
            self._opp, self._threat, self._shots = None, 0.0, []
            self._pending_camera = None
            self._pending_scene = None
            self._seen_objects = {}
            self.last_scene = {}
            self.snapshot = self.core.snapshot()
            self.motor = self.core.motor_command(self.snapshot)

    # ---- bucle ------------------------------------------------
    def start(self) -> None:
        def _supervise() -> None:
            while not self._stop.is_set():
                try:
                    self._run()
                except Exception as _e:
                    import traceback as _tb
                    print("[fly-engine] crash:", _e, _tb.format_exc()[-400:], flush=True)
                    time.sleep(0.2)
                if not self._stop.is_set():
                    time.sleep(0.1)
        self._thread = threading.Thread(target=_supervise, name="fly-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        dt = self.core.dt
        next_t = time.perf_counter()
        fps_t0 = next_t
        fps_n = 0
        while not self._stop.is_set():
            with self._lock:
                cam = self._pending_camera
                self._pending_camera = None
                scene = self._pending_scene
                self._pending_scene = None
                # extrapolar la posicion del objeto con su velocidad angular
                if self._opp is not None and self.extrapolate:
                    self._opp[0] += self._opp_vel[0] * dt
                    self._opp[1] += self._opp_vel[1] * dt
                    if abs(self._opp[0]) > 200.0:
                        self._opp[0] = (1.0 if self._opp[0] > 0 else -1.0) * 200.0
                    if self._opp[1] < 0.0:
                        self._opp[1] = 0.0
                opp = list(self._opp) if self._opp is not None else None
                threat, shots = self._threat, self._shots

            eye_drive = None
            if scene is not None:
                # vision semantica: 'Astra ve, la mosca decide'
                try:
                    inject = self.core.scene_inject(scene)
                except Exception:
                    inject = []
                    import traceback
                    traceback.print_exc()
            elif cam is not None:
                eye_drive, inject = self.core.vision_drive(cam, prev=self._prev_cam)
                self._prev_cam = cam
                try:
                    import numpy as _np
                    arr = _np.asarray(cam, dtype=float)
                    if arr.ndim == 2:
                        arr = arr.mean(axis=0)
                    arr = arr.ravel()
                    if arr.size != 160:
                        idx = _np.linspace(0, arr.size - 1, 160).astype(int)
                        arr = arr[idx]
                    arr = arr - arr.min()
                    m = float(arr.max())
                    self.eye = (arr / m).round(3).tolist() if m > 1e-6 else [0.0] * 160
                except Exception:
                    pass
            elif opp is not None or shots or threat > 0:
                inject = self.core.visual_inject(opp=opp, threat=threat, shots=shots)
            else:
                inject = []

            try:
                snap = self.core.step(inject=inject, eye_drive=eye_drive)
                motor = self.core.motor_command(snap)
            except Exception as _e:
                import traceback as _tb
                print("[fly-engine] step error:", _e, _tb.format_exc()[-300:], flush=True)
                time.sleep(0.05)
                continue
            try:
                hist = self.core.spike_histogram(self.hist_bins)
            except Exception:
                hist = self.hist

            with self._lock:
                self.snapshot = snap
                self.motor = motor
                self.hist = hist
                self.seq += 1

            fps_n += 1
            now = time.perf_counter()
            if now - fps_t0 >= 1.0:
                self.fps = fps_n / (now - fps_t0)
                fps_t0, fps_n = now, 0

            if self.realtime:
                next_t += dt
                slack = next_t - time.perf_counter()
                if slack > 0:
                    time.sleep(slack)
                else:
                    next_t = time.perf_counter()   # nos retrasamos: resincronizar

    def payload(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "type": "telemetry",
                "seq": self.seq,
                "fps": round(self.fps, 1),
                "t": time.time(),
                "snapshot": dict(self.snapshot),
                "motor": dict(self.motor),
                "hist": list(self.hist),
                "eye": list(self.eye),
                "scene": dict(self.last_scene),
                "bmap": self.core.spike_map(),
                "spikes": self.core.spike_sample(256),
            }


class _Handler(socketserver.StreamRequestHandler):
    engine: FlyEngine = None            # inyectado por el servidor

    def _send(self, obj: Dict[str, Any]) -> None:
        try:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            raise

    def handle(self) -> None:
        eng = self.engine
        self._send({"type": "hello", "brain": eng.core.brain_summary(),
                    "map": eng.core.brain_map_meta(),
                    "protocol": 1, "dt": eng.core.dt})
        last_tele = 0.0
        buf = b""
        try:
            while True:
                # 1) leer sin bloquear (timeout corto) y sin rfile (mas robusto)
                self.connection.settimeout(0.02)
                closed = False
                try:
                    chunk = self.connection.recv(65536)
                    if not chunk:
                        closed = True
                    else:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            if line.strip():
                                try:
                                    msg = json.loads(line.decode("utf-8").strip() or "{}")
                                except Exception as e:
                                    self._send({"type": "error", "detail": f"JSON invalido: {e}"})
                                    continue
                                self._handle_cmd(msg)
                except socket.timeout:
                    pass
                except OSError:
                    closed = True
                if closed:
                    break

                # 2) emitir telemetria a la tasa configurada
                period = 1.0 / eng.telemetry_hz
                now = time.perf_counter()
                if now - last_tele >= period:
                    last_tele = now
                    self._send(eng.payload())
                else:
                    time.sleep(0.001)
        except Exception:
            pass

    def _handle_cmd(self, msg: Dict[str, Any]) -> None:
        eng = self.engine
        cmd = (msg.get("cmd") or "").lower()
        if cmd == "sensory":
            eng.set_sensory(
                opp=msg.get("opp"),
                threat=msg.get("threat", 0.0),
                shots=msg.get("shots", []),
            )
        elif cmd == "camera":
            eng.set_camera(msg.get("frame"))
        elif cmd == "scene":
            eng.set_scene(msg.get("objects") or [])
            self._send({"type": "ack", "cmd": "scene",
                        "novel": list((eng.last_scene or {}).get("novel", []))})
        elif cmd == "reset":
            eng.reset(msg.get("seed"))
            self._send({"type": "ack", "cmd": "reset"})
        elif cmd == "status":
            self._send({"type": "status", "brain": eng.core.brain_summary(),
                        "fps": round(eng.fps, 1), "seq": eng.seq})
        elif cmd == "config":
            if "telemetry_hz" in msg:
                eng.telemetry_hz = max(0.5, float(msg["telemetry_hz"]))
            if "realtime" in msg:
                eng.realtime = bool(msg["realtime"])
            self._send({"type": "ack", "cmd": "config", "telemetry_hz": eng.telemetry_hz})
        elif cmd == "mapmeta":
            self._send({"type": "mapmeta", "map": eng.core.brain_map_meta()})
        elif cmd == "ping":
            self._send({"type": "pong", "t": time.time()})
        else:
            self._send({"type": "error", "detail": f"comando desconocido: {cmd!r}"})


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    ap = argparse.ArgumentParser(description="Vector-Fly sidecar (cerebro de mosca MaleCNS v1.0)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.getenv("FLY_PORT", "4711")))
    ap.add_argument("--device", default=os.getenv("FLY_DEVICE", "cpu"), choices=["cpu", "cuda", "auto"])
    ap.add_argument("--rate", type=float, default=float(os.getenv("FLY_TELEMETRY_HZ", "10")),
                    help="Hz de telemetria enviada al cliente")
    ap.add_argument("--no-realtime", action="store_true",
                    help="corre tan rapido como pueda (sin esperar al reloj)")
    args = ap.parse_args()

    core = FlyCore(
        data=os.getenv("FLY_DATA") or None,
        device=args.device,
        seed=int(os.getenv("FLY_SEED", "64")),
        dt=float(os.getenv("FLY_DT", str(FlyCore.DT))),
    )
    engine = FlyEngine(core, realtime=not args.no_realtime, telemetry_hz=args.rate)
    _Handler.engine = engine
    server = _Server((args.host, args.port), _Handler)

    print("=" * 64)
    print(" VECTOR-FLY SIDECAR")
    print("=" * 64)
    print(f"  escuchando en {args.host}:{args.port}")
    print(f"  telemetria   {args.rate} Hz")
    print(f"  rt           {not args.no_realtime}")
    print("  Ctrl+C para detener")
    print("=" * 64)

    engine.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo sidecar...")
    finally:
        engine.stop()
        server.shutdown()


if __name__ == "__main__":
    main()

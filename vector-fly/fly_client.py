#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR-FLY :: CLIENTE (solo stdlib)
# ============================================================
# Se conecta al sidecar del cerebro de mosca y expone una API
# sencilla y a prueba de fallos. Diseñado para el proceso de
# Vector (no necesita flybrain ni numpy).
#
#   from fly_client import FlyClient
#   fly = FlyClient()
#   if fly.connect():
#       fly.see(opp=(-40, 25), threat=0.6)   # objeto a la izquierda
#       m = fly.motor()                      # {"left":..,"right":..,"escape":..}
#       fly.close()
# ============================================================
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Dict, Optional, Sequence, Tuple


class FlyClient:
    """Cliente TCP thread-safe. Tolera caidas y reconexion."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4711,
                 timeout: float = 2.0, auto_reconnect: bool = True) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.auto_reconnect = auto_reconnect

        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader: Optional[threading.Thread] = None

        self.brain: Dict[str, Any] = {}
        self.snapshot: Dict[str, Any] = {}
        self.motor: Dict[str, Any] = {"left": 0.0, "right": 0.0, "linear": 0.0,
                                      "turn": 0.0, "escape": False, "mode": "idle"}
        self.hist: list = [0] * 128
        self.eye: list = []
        self.bmap: list = []
        self.spikes: list = []
        self.brain_map: Dict[str, Any] = {}
        self.last_seen: float = 0.0
        self.fps: float = 0.0
        self.seq: int = 0
        self.connected: bool = False
        self.error: str = ""

    # ---------------------------------------------------------
    def connect(self) -> bool:
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._sock = s
            self._stop.clear()
            self._reader = threading.Thread(target=self._read_loop, name="fly-client", daemon=True)
            self._reader.start()
            # esperar el hello
            t0 = time.time()
            while not self.brain and time.time() - t0 < 3.0:
                time.sleep(0.02)
            self.connected = True
            self.error = ""
            return True
        except Exception as e:
            self.error = str(e)
            self.connected = False
            return False

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
        self.connected = False

    # ---------------------------------------------------------
    def _send(self, obj: Dict[str, Any]) -> bool:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            s = self._sock
            if s is None:
                return False
            try:
                s.sendall(data)
                return True
            except Exception as e:
                self.error = str(e)
                self.connected = False
                return False

    def _read_loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            with self._lock:
                s = self._sock
            if s is None:
                break
            try:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._on_message(line)
            except socket.timeout:
                continue
            except Exception as e:
                self.error = str(e)
                break
        self.connected = False
        if self.auto_reconnect and not self._stop.is_set():
            time.sleep(0.5)
            try:
                self.connect()
            except Exception:
                pass

    def _on_message(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        t = msg.get("type")
        if t == "hello":
            self.brain = msg.get("brain", {})
            if msg.get("map"):
                self.brain_map = msg["map"]
        elif t == "telemetry":
            self.snapshot = msg.get("snapshot", {})
            self.motor = msg.get("motor", self.motor)
            self.hist = msg.get("hist", self.hist)
            self.eye = msg.get("eye", self.eye)
            bmap = msg.get("bmap")
            if bmap is not None:
                self.bmap = bmap
            spikes = msg.get("spikes")
            if spikes is not None:
                self.spikes = spikes
            self.fps = msg.get("fps", 0.0)
            self.seq = msg.get("seq", 0)
            self.last_seen = time.time()
            self.connected = True
        elif t == "status":
            self.brain = msg.get("brain", self.brain)
        elif t == "mapmeta":
            if msg.get("map"):
                self.brain_map = msg["map"]

    # ---------------------------------------------------------
    # API publica
    # ---------------------------------------------------------
    def see(self, opp: Optional[Tuple[float, float]] = None, threat: float = 0.0,
            shots: Sequence[Tuple[str, float, float]] = ()) -> bool:
        """Informa a la mosca de lo que ve. opp=(dx,size); dx<0 = izquierda."""
        msg: Dict[str, Any] = {"cmd": "sensory", "threat": float(threat)}
        if opp is not None:
            msg["opp"] = [float(opp[0]), float(opp[1])]
        if shots:
            msg["shots"] = [[str(k), float(dx), float(sz)] for k, dx, sz in shots]
        return self._send(msg)

    def camera(self, band) -> bool:
        """Envia una panoramica 1-D (lista de floats 0..1)."""
        return self._send({"cmd": "camera", "frame": [float(x) for x in band]})

    def scene(self, objects) -> bool:
        """Envia una escena semantica (vision): [{'name','side','near'}]."""
        objs = []
        for o in (objects or ()):
            if isinstance(o, dict) and o.get("name"):
                objs.append({"name": str(o["name"]),
                             "side": float(o.get("side", 0.0)),
                             "near": float(o.get("near", 0.25))})
        if not objs:
            return False
        return self._send({"cmd": "scene", "objects": objs})

    def step(self) -> Dict[str, Any]:
        """Devuelve el ultimo estado motor recibido (no bloquea)."""
        return self.motor

    def telemetry(self) -> Dict[str, Any]:
        return {"snapshot": self.snapshot, "motor": self.motor,
                "fps": self.fps, "seq": self.seq}

    def reset(self, seed: Optional[int] = None) -> bool:
        return self._send({"cmd": "reset", "seed": seed})

    def status(self) -> bool:
        return self._send({"cmd": "status"})

    def mapmeta(self) -> bool:
        """Pide la geometria + densidad del mapa cerebral (llega como type=mapmeta)."""
        return self._send({"cmd": "mapmeta"})

    def ping(self) -> bool:
        return self._send({"cmd": "ping"})

    def config(self, telemetry_hz: Optional[float] = None,
               realtime: Optional[bool] = None) -> bool:
        msg: Dict[str, Any] = {"cmd": "config"}
        if telemetry_hz is not None:
            msg["telemetry_hz"] = float(telemetry_hz)
        if realtime is not None:
            msg["realtime"] = bool(realtime)
        return self._send(msg)

    @property
    def alive(self) -> bool:
        """True si ha llegado telemetria en el ultimo segundo."""
        return (time.time() - self.last_seen) < 1.0

    @property
    def is_escaping(self) -> bool:
        return bool(self.motor.get("escape"))


def desde_env(prefix: str = "FLY") -> "FlyClient":
    import os
    return FlyClient(
        host=os.getenv(f"{prefix}_HOST", "127.0.0.1"),
        port=int(os.getenv(f"{prefix}_PORT", "4711")),
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Cliente de prueba Vector-Fly")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4711)
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()

    cli = FlyClient(args.host, args.port)
    print(f"Conectando a {args.host}:{args.port} ...")
    if not cli.connect():
        raise SystemExit(f"No conecta: {cli.error}")
    b = cli.brain
    print(f"Cerebro: {b.get('neurons'):,} neuronas @ {1/b.get('dt_ms',20)*1000:.0f} Hz "
          f"({b.get('device')})")
    print(f"{'t(s)':>5} {'dx':>7} {'LC4':>7} {'escape':>7} {'mode':>7} {'L':>6} {'R':>6}")
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            t = time.time() - t0
            # un objeto que se acerca por la izquierda y luego por la derecha
            dx = (-70 + 45 * t) if t < 2.0 else (20 + 45 * (t - 2.0))
            size = 8 + 14 * t
            cli.see(opp=(dx, size), threat=min(1.0, t / 3.0))
            m = cli.step()
            snap = cli.snapshot
            print(f"{t:5.1f} {dx:7.1f} {snap.get('LC4',0):7.3f} "
                  f"{snap.get('escape',0):7.3f} {m.get('mode',''):>7} "
                  f"{m.get('left',0):+6.2f} {m.get('right',0):+6.2f}")
            time.sleep(0.2)
    finally:
        cli.close()

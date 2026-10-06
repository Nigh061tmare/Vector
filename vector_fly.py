#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VECTOR ULTRA 13.5 :: REFLEJO FLYBRAIN (MaleCNS v1.0)
# Puente entre el cerebro de mosca (sidecar fly_server.py) y Vector.
import json
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path

_FLY_DIR = Path(r"C:\Users\Jose Luis\vector-fly")
if str(_FLY_DIR) not in sys.path:
    sys.path.insert(0, str(_FLY_DIR))


class FlyReflex:
    """Reflejo optogenético: ToF -> looming -> escape -> ruedas de Vector."""

    def __init__(self, host="127.0.0.1", port=4711, enabled=None, camera_path=""):
        self.host, self.port = host, port
        if enabled is None:
            enabled = os.getenv("MODO_FLYBRAIN", "0").strip().lower() in ("1", "true", "si", "yes")
        self.enabled = bool(enabled)
        self.camera_path = camera_path
        self.vision = os.getenv("FLY_VISION", "1").strip().lower() in ("1", "true", "si", "yes")
        self._client = None
        self._lock = threading.Lock()
        self._cmd = {"left": 0.0, "right": 0.0, "escape": False, "mode": "idle"}
        self._last_rx = 0.0
        self._connect()

    def _connect(self):
        if not self.enabled:
            return False
        try:
            from fly_client import FlyClient
            c = FlyClient(self.host, self.port, timeout=1.0)
            if not c.connect():
                raise RuntimeError("connect() fallo")
            self._client = c
            return True
        except Exception as e:
            print("[vector_fly] sidecar no disponible:", e)
            self._client = None
            return False

    @property
    def online(self):
        """True si el sidecar responde.

        Reintenta la conexion periodicamente: el nucleo puede arrancar antes
        que el cerebro-mosca (que tarda ~30-60s en cargar el connectome), y
        debe engancharlo solo cuando este listo.
        """
        if self._client is not None and getattr(self._client, "connected", False):
            return True
        if not self.enabled:
            return False
        ahora = time.time()
        if ahora - getattr(self, "_last_retry", 0.0) >= 15.0:
            self._last_retry = ahora
            try:
                self._connect()
            except Exception:
                pass
        return self._client is not None and getattr(self._client, "connected", False)

    def feed_senses(self, bearing_deg, dist_mm, threat=None):
        """bearing_deg: >0 amenaza a la DERECHA, <0 a la IZQUIERDA. dist_mm frontal."""
        if not self.enabled or self._client is None:
            return None
        try:
            bearing_rad = math.radians(float(bearing_deg))
            dx_game = max(-110.0, min(110.0, bearing_rad * 62.0))
            dist_game = max(6.0, float(dist_mm) / 15.0)
            size = max(2.0, min(130.0, 520.0 / dist_game))
            if threat is None:
                threat = max(0.0, min(1.0, (70.0 - dist_game) / 60.0))
            if not self._client.see(opp=(dx_game, size), threat=float(threat)):
                return None
            cmd = self._client.step()
            if cmd:
                with self._lock:
                    self._cmd = dict(cmd)
                    self._last_rx = time.time()
            return cmd
        except Exception:
            self._client = None
            return None

    def feed_frame(self, img):
        """Envia la camara de Vector a los fotorreceptores de la mosca.

        `img`: PIL.Image (o array). Se reduce a un panorama 1-D de 160 columnas.
        """
        if not (self.enabled and self.vision) or self._client is None:
            return False
        try:
            import numpy as np
            try:
                img = img.convert("L").resize((160, 90))
            except AttributeError:
                pass
            arr = np.asarray(img, dtype=float)
            if arr.max() > 1.0:
                arr = arr / 255.0
            if arr.ndim == 3:
                arr = arr.mean(axis=2)
            h = arr.shape[0]
            band = arr[h // 3: (2 * h) // 3 or h, :].mean(axis=0)
            return bool(self._client.camera(band.round(3).tolist()))
        except Exception:
            return False

    def from_map(self, mapa):
        """Deriva (bearing, dist, threat) del Mapa (12 sectores ToF)."""
        try:
            with mapa.lock:
                sec = list(mapa.sec)
                head = mapa.head % len(sec)
            idx = max(range(len(sec)), key=lambda i: sec[i])
            prox = sec[idx]
            if prox <= 25.0:
                return None
            rel = idx - head
            if rel > len(sec) // 2:
                rel -= len(sec)
            # OJO: el indice de sector crece hacia la IZQUIERDA
            # (turn_in_place: angulo positivo = izquierda, y escanear()
            #  marca rel = round(a/360*SECTORES)).  Aqui '+' DEBE significar
            #  DERECHA para el cerebro de mosca (dx<0 = izquierda), asi que
            #  invertimos el signo.  Sin esto, la mosca gira HACIA el obstaculo.
            bearing = -rel * (360.0 / len(sec))
            dist_mm = 800.0 * (1.0 - prox / 100.0)
            threat = max(0.0, min(1.0, prox / 100.0))
            return bearing, dist_mm, threat
        except Exception:
            return None

    def tick(self, mapa=None, bearing_deg=None, dist_mm=None, threat=None):
        if not self.enabled:
            return None
        if bearing_deg is None and mapa is not None:
            s = self.from_map(mapa)
            if s is None:
                return None
            bearing_deg, dist_mm, threat = s
        if bearing_deg is None:
            return None
        cmd = self.feed_senses(bearing_deg, dist_mm, threat)
        if cmd:
            with self._lock:
                self._cmd = cmd
                self._last_rx = time.time()
        return cmd

    def last_command(self):
        with self._lock:
            return dict(self._cmd)

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


def reflex_standalone():
    """Autotest: simula una amenaza que se acerca y verifica el escape."""
    r = FlyReflex(enabled=True)
    if not r.online:
        return {"online": False, "error": "arranca fly_server.py primero"}
    fired, esc = 0, 0
    for i in range(80):
        dist = max(40.0, 900.0 - i * 22.0)
        th = max(0.0, min(1.0, (700.0 - dist) / 650.0))
        cmd = r.tick(bearing_deg=-35.0, dist_mm=dist, threat=th)
        if cmd:
            fired += 1
            if cmd.get("escape"):
                esc += 1
        time.sleep(0.03)
    out = {"online": True, "ticks": 80, "respuestas": fired, "escapes": esc,
           "ultimo": r.last_command()}
    r.close()
    return out


if __name__ == "__main__":
    print(json.dumps(reflex_standalone(), indent=2, ensure_ascii=False))

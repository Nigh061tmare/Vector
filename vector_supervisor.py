#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supervisor: comprueba cada 30 s los servicios de Vector y relanza los caidos.

Logica pura y testeable (sonda y lanzador inyectables).  Reglas:
  * un servicio se considera caido tras `fallos_para_reiniciar` sondas seguidas fallidas
    (evita relanzar por un fallo puntual);
  * backoff exponencial entre reinicios (30 s .. 10 min) y reset tras estar sano;
  * `max_reinicios_hora` por servicio: si lo supera, deja de reiniciarlo y avisa
    (un servicio que muere en bucle no se arregla relanzandolo).

Uso:  python vector_supervisor.py            (bucle)
      python vector_supervisor.py --once     (una pasada, imprime estado)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

CHECK_EVERY_S = 30.0
FAILS_TO_RESTART = 2
BACKOFF_MIN_S = 30.0
BACKOFF_MAX_S = 600.0
HEALTHY_RESET_S = 120.0
MAX_RESTARTS_PER_HOUR = 6

HOME = os.path.expanduser("~")


@dataclass
class Service:
    name: str
    port: int
    cmd: List[str]
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    http_path: str = ""          # si se indica, ademas de TCP se pide esta ruta
    startup_grace_s: float = 20.0


def default_services() -> List[Service]:
    """Servicios de Vector Ultra (rutas de Windows del proyecto; editar si cambian)."""
    py_core = os.path.join(HOME, "VectorSDK", "venv", "Scripts", "python.exe")
    py_fly = os.path.join(HOME, "flybrain-venv", "Scripts", "python.exe")
    fly = os.path.join(HOME, "vector-fly")
    return [
        Service("llama_vl", 8081, [os.path.join(HOME, "llama-cpp", "VL_ON.bat")], startup_grace_s=60),
        Service("fly_server", 4711, [py_fly, os.path.join(fly, "fly_server.py"), "--port", "4711",
                                     "--device", "cpu", "--rate", "10"], cwd=fly, startup_grace_s=60),
        Service("fly_live", 4712, [py_fly, os.path.join(fly, "fly_live.py")], cwd=fly),
        Service("nucleo", 8000, [os.path.join(HOME, "VectorSDK", "run_service.bat")],
                http_path="/api/health", startup_grace_s=40),
    ]


def tcp_probe(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def spawn(svc: Service) -> Any:
    env = {**os.environ, **svc.env}
    kw: Dict[str, Any] = {"cwd": svc.cwd or None, "env": env,
                          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED | NEW_PROCESS_GROUP
    return subprocess.Popen(svc.cmd, **kw)


@dataclass
class _State:
    fails: int = 0
    restarts: List[float] = field(default_factory=list)
    next_ok: float = 0.0
    backoff: float = BACKOFF_MIN_S
    healthy_since: Optional[float] = None
    grace_until: float = 0.0
    given_up: bool = False


class Supervisor:
    def __init__(self, services: List[Service],
                 probe: Callable[[Service], bool] = lambda s: tcp_probe(s.port),
                 launcher: Callable[[Service], Any] = spawn,
                 clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = print) -> None:
        self.services, self.probe, self.launcher, self.clock, self.log = services, probe, launcher, clock, log
        self.st: Dict[str, _State] = {s.name: _State() for s in services}

    def tick(self) -> Dict[str, str]:
        """Una pasada. Devuelve estado por servicio: ok|arrancando|caido|reiniciado|rendido."""
        now = self.clock()
        out: Dict[str, str] = {}
        for svc in self.services:
            st = self.st[svc.name]
            if st.given_up:
                out[svc.name] = "rendido"; continue
            try:
                alive = bool(self.probe(svc))
            except Exception:
                alive = False
            if alive:
                st.fails = 0
                st.healthy_since = st.healthy_since or now
                if now - st.healthy_since >= HEALTHY_RESET_S:
                    st.backoff = BACKOFF_MIN_S
                out[svc.name] = "ok"; continue
            st.healthy_since = None
            if now < st.grace_until:
                out[svc.name] = "arrancando"; continue
            st.fails += 1
            if st.fails < FAILS_TO_RESTART or now < st.next_ok:
                out[svc.name] = "caido"; continue
            st.restarts = [t for t in st.restarts if now - t < 3600.0]
            if len(st.restarts) >= MAX_RESTARTS_PER_HOUR:
                st.given_up = True
                self.log(f"[supervisor] {svc.name}: {MAX_RESTARTS_PER_HOUR} reinicios/hora; me rindo (mirar sus logs)")
                out[svc.name] = "rendido"; continue
            try:
                self.launcher(svc)
                self.log(f"[supervisor] {svc.name}: reiniciado (puerto {svc.port})")
                out[svc.name] = "reiniciado"
            except Exception as e:  # noqa: BLE001
                self.log(f"[supervisor] {svc.name}: no pude lanzar: {e}")
                out[svc.name] = "caido"
            st.restarts.append(now)
            st.fails = 0
            st.grace_until = now + svc.startup_grace_s
            st.next_ok = now + st.backoff
            st.backoff = min(BACKOFF_MAX_S, st.backoff * 2)
        return out

    def run(self) -> None:
        while True:
            self.tick()
            time.sleep(CHECK_EVERY_S)


if __name__ == "__main__":
    sup = Supervisor(default_services())
    if "--once" in sys.argv:
        print(json.dumps(sup.tick(), indent=2))
    else:
        sup.run()

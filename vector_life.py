#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vida de Vector: une Behavior + MotionController + Gaze + Eyes + Map.

Dos usos:
  * `vivir(robot, segundos)`: una "rebanada" de vida; la llama `ejecutar()` del
    nucleo cuando MODO_VIDA=1 (reemplaza a `explorar`, el resto del nucleo sigue
    igual y mantiene el control del robot: no hay dos controladores a la vez).
  * `python vector_life.py --sim`: simulacion cinematica SIN robot (humo/CI).

APIs del SDK usadas (verificadas en anki_vector): robot.proximity.last_sensor_reading
(.distance.distance_mm, .found_object), robot.pose.position.x/y y
robot.pose.rotation.angle_z.degrees, robot.status.is_on_charger/is_cliff_detected/
is_picked_up, robot.get_battery_state(), robot.motors.set_wheel_motors/set_head_motor,
robot.head_angle_rad.
"""
from __future__ import annotations

import math
import os
import random
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from vector_behavior import Behavior, Decision, Inputs, State
from vector_gaze import EyeState, GazeController, apply_head, show_eyes
from vector_map import ObjectMemory, OccupancyGrid
from vector_motion import MotionController, TICK_HZ

TICK_S = 1.0 / TICK_HZ
EYE_REFRESH_S = 0.5            # redibujar la cara (cada set_screen es un RPC)
BAT_VALIDA_V = 2.5


def _g(obj: Any, path: str, default: Any = None) -> Any:
    """getattr encadenado tolerante: _g(robot, 'status.is_on_charger')."""
    try:
        for part in path.split("."):
            obj = getattr(obj, part)
        return obj
    except Exception:
        return default


def sense(robot: Any, bat_cache: Dict[str, float]) -> Dict[str, Any]:
    """Lee el robot real. Cada lectura es tolerante: un fallo = valor neutro."""
    tof = None
    r = _g(robot, "proximity.last_sensor_reading")
    if r is not None and _g(r, "found_object", False):
        tof = _g(r, "distance.distance_mm")
    deg = _g(robot, "pose.rotation.angle_z.degrees", 0.0) or 0.0
    volts = None
    try:
        v = float(robot.get_battery_state().battery_volts or 0.0)
        if v >= BAT_VALIDA_V:
            bat_cache["v"] = v
        volts = bat_cache.get("v")
    except Exception:
        volts = bat_cache.get("v")
    return {
        "tof_mm": tof,
        "pos_mm": (float(_g(robot, "pose.position.x", 0.0) or 0.0),
                   float(_g(robot, "pose.position.y", 0.0) or 0.0)),
        "heading_deg": float(deg),
        "on_charger": bool(_g(robot, "status.is_on_charger", False)),
        "cliff": bool(_g(robot, "status.is_cliff_detected", False)),
        "picked_up": bool(_g(robot, "status.is_picked_up", False)),
        "battery_v": volts,
    }


class Vida:
    """Estado persistente de la vida de un robot (mapa, memoria, personalidad)."""

    def __init__(self, robot: Any, seed: int = 64, clock: Callable[[], float] = time.monotonic,
                 sense_fn: Callable[[Any, Dict[str, float]], Dict[str, Any]] = sense,
                 draw_eyes: bool = True) -> None:
        self.robot, self._clock, self._sense = robot, clock, sense_fn
        self.rng = random.Random(seed)
        self.behavior = Behavior(seed=seed, clock=clock, rng=random.Random(seed + 1))
        self.motion = MotionController(
            robot, guard=lambda: not (_g(robot, "status.is_cliff_detected", False)
                                      or _g(robot, "status.is_picked_up", False)),
            clock=clock)
        self.gaze = GazeController(clock, random.Random(seed + 2))
        self.eyes = EyeState(clock, random.Random(seed + 3))
        self.grid = OccupancyGrid()
        self.objects = ObjectMemory(clock)
        self._bat: Dict[str, float] = {}
        self._draw_eyes = draw_eyes
        self._t_eyes = 0.0
        self.last: Optional[Decision] = None
        self.hour_fn: Callable[[], int] = lambda: time.localtime().tm_hour
        self.scene: Dict[str, Any] = {}        # {"name","side","near","novel"} (del visor/VL)
        self.threat = 0.0
        self._homed = False

    # -- un paso ----------------------------------------------------------------------
    def step(self, dt: float) -> Decision:
        s = self._sense(self.robot, self._bat)
        pose = (s["pos_mm"][0], s["pos_mm"][1], s["heading_deg"])
        if s["on_charger"] and not self._homed:
            self.grid.set_home(pose[0], pose[1]); self._homed = True
        self.grid.update_tof(pose, s["tof_mm"])
        sc = self.scene or {}
        near = sc.get("near") if sc.get("name") else None
        if sc.get("name") and near is not None:
            x, y = ObjectMemory.project(pose, float(sc.get("side", 0.0)), float(near), s["tof_mm"])
            self.objects.observe(str(sc["name"]), x, y)
        x_in = Inputs(
            battery_v=s["battery_v"], on_charger=s["on_charger"], tof_mm=s["tof_mm"],
            threat=self.threat, object_near=near, object_side=float(sc.get("side", 0.0)),
            object_novel=bool(sc.get("novel")), hour=self.hour_fn(),
            cliff=s["cliff"], picked_up=s["picked_up"], pos_mm=s["pos_mm"],
            heading_deg=s["heading_deg"])
        d = self.behavior.update(x_in)
        self.last = d
        self._pose = pose

        # cuerpo: ruedas suaves + cabeza con sacadas + ojos
        self.motion.set_target(d.linear, d.turn, ttl=0.4)
        self.motion.tick(dt)
        interest = None
        if near is not None:
            interest = (10.0 + 15.0 * float(near), 0.8)     # mirar al objeto
        g = self.gaze.update(interest, arousal=min(1.0, self.behavior.miedo / 50.0 + 0.3))
        if d.state != State.DORMIR:
            apply_head(self.robot, g)
        self.eyes.set(d.eyes)
        ep = self.eyes.update()
        now = self._clock()
        if self._draw_eyes and now - self._t_eyes >= EYE_REFRESH_S:
            self._t_eyes = now
            show_eyes(self.robot, ep, gaze_x=-float(sc.get("side", 0.0)), duration_s=EYE_REFRESH_S + 0.3)
        return d

    def stop(self) -> None:
        self.motion.emergency_stop()


_VIDAS: Dict[int, Vida] = {}


def snapshot() -> Dict[str, Any]:
    """Estado legible para NEXUS (/api/vida). Vacio si MODO_VIDA no ha arrancado."""
    if not _VIDAS:
        return {"activo": False}
    v = list(_VIDAS.values())[-1]
    d = v.last
    l, r = v.motion.speeds
    pose = getattr(v, "_pose", (0.0, 0.0, 0.0))
    return {
        "activo": True,
        "estado": d.state.value if d else "-",
        "razon": d.razon if d else "",
        "ojos": d.eyes if d else "",
        "linear": round(d.linear, 2) if d else 0.0,
        "turn": round(d.turn, 2) if d else 0.0,
        "ruedas": {"left": round(l, 1), "right": round(r, 1)},
        "miedo": round(v.behavior.miedo, 1),
        "personalidad": v.behavior.p.descripcion(),
        "rasgos": {k: getattr(v.behavior.p, k) for k in
                   ("audacia", "curiosidad", "sociabilidad", "energia")},
        "cabeza_deg": round(v.gaze.pos, 1),
        "pupila": round(v.eyes.pupil, 2),
        "timeline": v.behavior.timeline(25),
        "mapa": v.grid.to_ascii(pose, half=12),
        "objetos": v.objects.summary()[:8],
    }


def vivir(robot: Any, segundos: float = 6.0, scene: Optional[Dict[str, Any]] = None,
          threat: float = 0.0) -> str:
    """Rebanada de vida para el nucleo. Devuelve el estado final (str) para `habito()`."""
    v = _VIDAS.get(id(robot))
    if v is None:
        v = _VIDAS[id(robot)] = Vida(robot, seed=int(os.getenv("VECTOR_SEED", "64")))
    v.scene = scene or {}
    v.threat = threat
    fin = time.monotonic() + segundos
    d: Optional[Decision] = None
    try:
        while time.monotonic() < fin:
            d = v.step(TICK_S)
            time.sleep(TICK_S)
    finally:
        v.stop()
    return "vida_" + (d.state.value.lower() if d else "nada")


# ----------------------------------------------------------------------------- simulador
class SimRobot:
    """Robot diferencial 2-D con paredes, para humo/CI sin hardware."""

    WHEEL_BASE_MM = 48.0

    def __init__(self, room: Tuple[float, float] = (1600.0, 1200.0), seed: int = 0) -> None:
        self.w, self.h = room
        self.x, self.y, self.th = 0.0, 0.0, 0.0
        self.l = self.r = 0.0
        self.head = 0.0
        self.volts = 4.0
        self.on_charger = False
        self.rng = random.Random(seed)
        self.collisions = 0
        self.status = type("S", (), {"is_on_charger": False, "is_cliff_detected": False,
                                     "is_picked_up": False})()
        self.motors = self
        self.head_angle_rad = 0.0

    # API fake del SDK
    def set_wheel_motors(self, l, r, left_wheel_accel=0.0, right_wheel_accel=0.0):
        self.l, self.r = l, r

    def set_head_motor(self, speed): self.head_angle_rad += speed * 0.05

    def get_battery_state(self):
        return type("B", (), {"battery_volts": self.volts})()

    def advance(self, dt: float) -> None:
        v = (self.l + self.r) / 2.0
        om = (self.r - self.l) / self.WHEEL_BASE_MM            # rad/s, + = antihorario
        self.th += om * dt
        nx = self.x + v * math.cos(self.th) * dt
        ny = self.y + v * math.sin(self.th) * dt
        if abs(nx) > self.w / 2 or abs(ny) > self.h / 2:
            self.collisions += 1; return                         # pared: no se mueve (atasco)
        self.x, self.y = nx, ny
        self.volts -= 0.00002 * abs(v) * dt

    def tof(self) -> Optional[float]:
        best = 1e9
        for ang in (self.th,):
            c, s = math.cos(ang), math.sin(ang)
            for lim, d, comp in ((self.w / 2, c, self.x), (-self.w / 2, c, self.x),
                                 (self.h / 2, s, self.y), (-self.h / 2, s, self.y)):
                if abs(d) > 1e-6:
                    t = (lim - comp) / d
                    if t > 0:
                        best = min(best, t)
        return best if best < 1200 else None


def simulate(seconds: float = 120.0, seed: int = 3, verbose: bool = True) -> Dict[str, Any]:
    """Simulacion acelerada (reloj virtual) de Vida sobre SimRobot."""
    rb = SimRobot(seed=seed)
    t = [0.0]
    clock = lambda: t[0]                                       # noqa: E731

    def sense_sim(robot: SimRobot, bat: Dict[str, float]) -> Dict[str, Any]:
        return {"tof_mm": robot.tof(), "pos_mm": (robot.x, robot.y),
                "heading_deg": math.degrees(robot.th) % 360.0, "on_charger": False,
                "cliff": False, "picked_up": False, "battery_v": robot.volts}

    vida = Vida(rb, seed=seed, clock=clock, sense_fn=sense_sim, draw_eyes=False)
    vida.hour_fn = lambda: 12
    dt = TICK_S
    estados: Dict[str, int] = {}
    for _ in range(int(seconds / dt)):
        d = vida.step(dt)
        rb.advance(dt)
        t[0] += dt
        estados[d.state.value] = estados.get(d.state.value, 0) + 1
    out = {"collisions": rb.collisions, "estados": estados,
           "celdas_vistas": sum(1 for row in vida.grid.l for v in row if abs(v) > 0.3),
           "atascos": sum(1 for e in vida.behavior.timeline(200) if "atascado" in e["razon"])}
    if verbose:
        print(vida.grid.to_ascii((rb.x, rb.y, math.degrees(rb.th)), half=14))
        print(out)
        for e in vida.behavior.timeline(8):
            print(f"  [{e['estado']:<13}] {e['razon']}")
    return out


if __name__ == "__main__":
    if "--sim" in sys.argv:
        simulate()
    else:
        print("Uso: python vector_life.py --sim   (simulacion sin robot)")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mapa cognitivo ligero: rejilla de ocupacion (log-odds) + memoria de objetos.

Sin SDK. La pose (x, y en mm, heading en grados) viene de `robot.pose`
(odometria del SDK: position.x/y, rotation.angle_z) o de la integracion de
`vector_motion`. La deriva de odometria es real: la rejilla es LOCAL y
aproximada, no SLAM; se corrige tambien con relocalizacion en la base.

Convenciones: heading 0 = eje +x del mundo, positivo antihorario (como el SDK).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CELL_MM = 50.0
GRID_N = 120                     # 120 x 50 mm = 6 m de lado
L_OCC = 0.85                     # incremento log-odds al ver obstaculo
L_FREE = -0.40                   # decremento al ver libre
L_MIN, L_MAX = -4.0, 4.0
OCC_THRESH = 1.0
FREE_THRESH = -0.8
TOF_MAX_MM = 1200.0              # alcance util del ToF de Vector
OBJ_TTL_S = 900.0                # un objeto no visto en 15 min se olvida
OBJ_MERGE_MM = 250.0             # observaciones a menos de esto = mismo objeto
MOVE_EPS_MM = 120.0              # desplazamiento que cuenta como "se movio"


@dataclass
class TrackedObject:
    name: str
    x: float
    y: float
    first_seen: float
    last_seen: float
    n_obs: int = 1
    moved: bool = False
    vx: float = 0.0
    vy: float = 0.0


class OccupancyGrid:
    def __init__(self, n: int = GRID_N, cell_mm: float = CELL_MM) -> None:
        self.n, self.cell = n, cell_mm
        self.l = [[0.0] * n for _ in range(n)]
        self.home: Optional[Tuple[float, float]] = None

    # -- coordenadas --------------------------------------------------------
    def to_cell(self, x_mm: float, y_mm: float) -> Optional[Tuple[int, int]]:
        i = int(math.floor(x_mm / self.cell)) + self.n // 2
        j = int(math.floor(y_mm / self.cell)) + self.n // 2
        return (i, j) if 0 <= i < self.n and 0 <= j < self.n else None

    def to_world(self, i: int, j: int) -> Tuple[float, float]:
        return ((i - self.n // 2 + 0.5) * self.cell, (j - self.n // 2 + 0.5) * self.cell)

    def _add(self, c: Optional[Tuple[int, int]], dl: float) -> None:
        if c:
            v = self.l[c[0]][c[1]] + dl
            self.l[c[0]][c[1]] = max(L_MIN, min(L_MAX, v))

    # -- actualizacion ---------------------------------------------------------
    def update_tof(self, pose: Tuple[float, float, float], dist_mm: Optional[float],
                   bearing_deg: float = 0.0) -> None:
        """Rayo ToF: celdas libres hasta `dist_mm`, ocupada en el impacto.

        dist_mm None o > alcance: solo libre (sin impacto).
        """
        x, y, h = pose
        a = math.radians(h + bearing_deg)
        hit = dist_mm is not None and dist_mm < TOF_MAX_MM
        d = dist_mm if hit else TOF_MAX_MM
        steps = int(d / (self.cell * 0.5))
        seen = set()
        for k in range(steps):
            c = self.to_cell(x + math.cos(a) * k * self.cell * 0.5,
                             y + math.sin(a) * k * self.cell * 0.5)
            if c and c not in seen:
                seen.add(c); self._add(c, L_FREE)
        if hit:
            self._add(self.to_cell(x + math.cos(a) * d, y + math.sin(a) * d), L_OCC)

    def mark_robot_free(self, pose: Tuple[float, float, float]) -> None:
        self._add(self.to_cell(pose[0], pose[1]), L_FREE)

    def set_home(self, x: float, y: float) -> None:
        self.home = (x, y)

    # -- consulta ------------------------------------------------------------------
    def state(self, x_mm: float, y_mm: float) -> str:
        c = self.to_cell(x_mm, y_mm)
        if not c:
            return "unknown"
        v = self.l[c[0]][c[1]]
        return "occupied" if v >= OCC_THRESH else "free" if v <= FREE_THRESH else "unknown"

    def is_safe(self, x_mm: float, y_mm: float) -> bool:
        """Zona segura: libre, o desconocida pero rodeada de celdas no ocupadas."""
        c = self.to_cell(x_mm, y_mm)
        if not c:
            return False
        if self.l[c[0]][c[1]] >= OCC_THRESH:
            return False
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                ii, jj = c[0] + di, c[1] + dj
                if 0 <= ii < self.n and 0 <= jj < self.n and self.l[ii][jj] >= OCC_THRESH:
                    return False
        return True

    def unexplored_frontier(self, pose: Tuple[float, float, float], radius_mm: float = 1500.0
                            ) -> Optional[Tuple[float, float]]:
        """Celda libre mas cercana adyacente a desconocido (frontera de exploracion)."""
        x, y, _ = pose
        best, best_d = None, 1e18
        r = int(radius_mm / self.cell)
        c0 = self.to_cell(x, y)
        if not c0:
            return None
        for i in range(max(1, c0[0] - r), min(self.n - 1, c0[0] + r)):
            for j in range(max(1, c0[1] - r), min(self.n - 1, c0[1] + r)):
                if self.l[i][j] > FREE_THRESH:
                    continue
                if any(-0.2 < self.l[i + di][j + dj] < 0.2
                       for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    wx, wy = self.to_world(i, j)
                    d = math.hypot(wx - x, wy - y)
                    if 150.0 < d < best_d:
                        best, best_d = (wx, wy), d
        return best

    def bearing_to(self, pose: Tuple[float, float, float], target: Tuple[float, float]) -> float:
        """Giro relativo (grados, -180..180; + = izquierda) para mirar a `target`."""
        ang = math.degrees(math.atan2(target[1] - pose[1], target[0] - pose[0]))
        return (ang - pose[2] + 180.0) % 360.0 - 180.0

    def to_ascii(self, pose: Optional[Tuple[float, float, float]] = None, half: int = 15) -> str:
        """Vista de depuracion/NEXUS (# ocupado, . libre, ' ' desconocido, R robot, H base)."""
        c0 = self.to_cell(pose[0], pose[1]) if pose else (self.n // 2, self.n // 2)
        ch = self.to_cell(*self.home) if self.home else None
        rows = []
        for j in range(c0[1] + half, c0[1] - half - 1, -1):
            row = []
            for i in range(c0[0] - half, c0[0] + half + 1):
                if (i, j) == c0 and pose:
                    row.append("R")
                elif (i, j) == ch:
                    row.append("H")
                elif not (0 <= i < self.n and 0 <= j < self.n):
                    row.append("?")
                else:
                    v = self.l[i][j]
                    row.append("#" if v >= OCC_THRESH else "." if v <= FREE_THRESH else " ")
            rows.append("".join(row))
        return "\n".join(rows)


class ObjectMemory:
    """Recuerda donde vio cada objeto, si se movio y cuando; olvida con el tiempo."""

    def __init__(self, clock=None) -> None:
        self._clock = clock or time.monotonic
        self.objects: List[TrackedObject] = []

    @staticmethod
    def project(pose: Tuple[float, float, float], side: float, near: float,
                tof_mm: Optional[float] = None, fov_deg: float = 90.0) -> Tuple[float, float]:
        """Posicion 3D->2D estimada: rumbo por posicion en imagen, distancia por ToF
        (si apunta al objeto) o por tamano aparente ('near' 0..1)."""
        bearing = -side * fov_deg / 2.0                  # side>0 = derecha = angulo negativo
        if tof_mm is not None and abs(side) < 0.25 and tof_mm < TOF_MAX_MM:
            dist = tof_mm
        else:
            dist = 100.0 + (1.0 - max(0.0, min(1.0, near))) * 1100.0
        a = math.radians(pose[2] + bearing)
        return pose[0] + math.cos(a) * dist, pose[1] + math.sin(a) * dist

    def observe(self, name: str, x: float, y: float) -> TrackedObject:
        now = self._clock()
        self.forget_old()
        best, bd = None, 1e18
        for o in self.objects:
            if o.name != name:
                continue
            d = math.hypot(o.x - x, o.y - y)
            if d < bd:
                best, bd = o, d
        if best is not None and bd < max(OBJ_MERGE_MM, MOVE_EPS_MM * 3):
            dt = max(1e-3, now - best.last_seen)
            if bd > MOVE_EPS_MM:
                best.moved = True
            best.vx, best.vy = (x - best.x) / dt, (y - best.y) / dt
            # suavizado para no seguir el ruido de proyeccion
            best.x += 0.5 * (x - best.x)
            best.y += 0.5 * (y - best.y)
            best.last_seen = now
            best.n_obs += 1
            return best
        o = TrackedObject(name, x, y, now, now)
        self.objects.append(o)
        return o

    def forget_old(self) -> None:
        now = self._clock()
        self.objects = [o for o in self.objects if now - o.last_seen <= OBJ_TTL_S]

    def where(self, name: str) -> Optional[TrackedObject]:
        self.forget_old()
        c = [o for o in self.objects if o.name == name]
        return max(c, key=lambda o: o.last_seen) if c else None

    def summary(self) -> List[Dict[str, object]]:
        now = self._clock()
        return [{"name": o.name, "x": round(o.x), "y": round(o.y),
                 "hace_s": round(now - o.last_seen), "movido": o.moved, "obs": o.n_obs}
                for o in sorted(self.objects, key=lambda o: -o.last_seen)]

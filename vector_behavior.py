#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comportamiento animal de Vector: FSM, personalidad, atascos y estigmergia.

Modulo PURO (sin SDK, sin hilos, reloj y RNG inyectables) para poder testearlo.
Decide *que quiere hacer* Vector; la ejecucion fisica la hace
`vector_motion.MotionController` (linear/turn en [-1, 1]).

Estados:
    DORMIR -> DESPERTAR -> EXPLORAR <-> ACERCARSE -> JUGAR
                              |  \\
                              |   -> HUIR (amenaza)
                              +----> VUELTA_A_BASE (bateria / calmarse) -> DORMIR

Cada decision lleva una `razon` en lenguaje natural (log de personalidad).
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

# --- Constantes ------------------------------------------------------------
V_BAT_BAJA = 3.60            # V: ir a cargar
V_BAT_OK = 4.05              # V: salir de la base
V_BAT_VALIDA = 2.5           # por debajo, la lectura es un fallo (no decidir)
TOF_FRENO_MM = 285.0
TOF_PELIGRO_MM = 155.0
TOF_COMODO_MM = 430.0
AMENAZA_HUIR = 0.65          # threat >= esto -> HUIR
AMENAZA_CALMA = 0.25         # threat < esto para poder calmarse
MIN_DWELL_S: Dict[str, float] = {      # permanencia minima (anti-parpadeo)
    "DORMIR": 20.0, "DESPERTAR": 2.5, "EXPLORAR": 4.0, "ACERCARSE": 3.0,
    "JUGAR": 5.0, "HUIR": 1.5, "VUELTA_A_BASE": 5.0,
}
HUIR_DURACION_S = 2.5
OBJETO_PERDIDO_S = 2.0        # objeto ausente este tiempo -> abandonar ACERCARSE
JUGAR_DURACION_S = 8.0
DESPERTAR_DURACION_S = 3.0
STUCK_CMD_MIN = 0.15         # |linear| ordenado para considerar "intenta avanzar"
STUCK_DESPLAZ_MM = 12.0      # menos que esto en la ventana = atascado
STUCK_VENTANA_S = 2.5
STIG_BINS = 12
STIG_TTL_MALO_S = 90.0
STIG_TTL_SEGURO_S = 300.0


class State(str, Enum):
    DORMIR = "DORMIR"
    DESPERTAR = "DESPERTAR"
    EXPLORAR = "EXPLORAR"
    ACERCARSE = "ACERCARSE"
    JUGAR = "JUGAR"
    HUIR = "HUIR"
    VUELTA_A_BASE = "VUELTA_A_BASE"


# --- Personalidad ----------------------------------------------------------
@dataclass(frozen=True)
class Personality:
    """Rasgos estables (0..1) derivados de una semilla: cada Vector es distinto."""
    audacia: float = 0.5        # tolera mas amenaza / se acerca mas
    curiosidad: float = 0.5     # prob. de explorar zonas nuevas, aprox. a objetos
    sociabilidad: float = 0.5   # busca juego/contacto
    energia: float = 0.5        # velocidad media y ganas de moverse
    sesgo_giro: float = 0.0     # -1 zurdo .. +1 diestro (tiende a girar a un lado)
    hora_dormir: int = 23
    hora_despertar: int = 7

    @staticmethod
    def from_seed(seed: int) -> "Personality":
        r = random.Random(seed)
        return Personality(
            audacia=round(r.uniform(0.2, 0.9), 3),
            curiosidad=round(r.uniform(0.3, 0.95), 3),
            sociabilidad=round(r.uniform(0.2, 0.9), 3),
            energia=round(r.uniform(0.3, 0.9), 3),
            sesgo_giro=round(r.uniform(-0.6, 0.6), 3),
            hora_dormir=r.choice([22, 23, 0]),
            hora_despertar=r.choice([6, 7, 8]),
        )

    def duerme_a(self, hora: int) -> bool:
        a, b = self.hora_dormir, self.hora_despertar
        return (hora >= a or hora < b) if a > b else (a <= hora < b)

    def descripcion(self) -> str:
        t = []
        t.append("audaz" if self.audacia > 0.65 else "timido" if self.audacia < 0.4 else "prudente")
        t.append("muy curioso" if self.curiosidad > 0.7 else "tranquilo")
        t.append("sociable" if self.sociabilidad > 0.6 else "independiente")
        return ", ".join(t)


# --- Estigmergia: marcas espaciales con caducidad ---------------------------
class Stigmergy:
    """Marca direcciones (bins de ángulo absoluto) como malas/seguras con TTL."""

    def __init__(self, clock=None) -> None:
        import time as _t
        self._clock = clock or _t.monotonic
        self._bad: Dict[int, float] = {}
        self._safe: Dict[int, float] = {}

    @staticmethod
    def _bin(angle_deg: float) -> int:
        return int(round((angle_deg % 360.0) / (360.0 / STIG_BINS))) % STIG_BINS

    def mark_bad(self, angle_deg: float, ttl: float = STIG_TTL_MALO_S) -> None:
        self._bad[self._bin(angle_deg)] = self._clock() + ttl

    def mark_safe(self, angle_deg: float, ttl: float = STIG_TTL_SEGURO_S) -> None:
        self._safe[self._bin(angle_deg)] = self._clock() + ttl

    def _live(self, d: Dict[int, float]) -> Dict[int, float]:
        now = self._clock()
        for k in [k for k, t in d.items() if t <= now]:
            del d[k]
        return d

    def penalty(self, angle_deg: float) -> float:
        """+1 direccion mala vigente, -0.5 segura vigente, 0 desconocida."""
        b = self._bin(angle_deg)
        if b in self._live(self._bad):
            return 1.0
        if b in self._live(self._safe):
            return -0.5
        return 0.0

    def best_heading(self, current_deg: float, candidates: Tuple[float, ...] = (-90, -45, 45, 90, 135, 180)) -> float:
        """Giro relativo (grados) a la direccion con menos penalizacion."""
        scored = sorted(candidates, key=lambda c: (self.penalty(current_deg + c), abs(c)))
        return scored[0]


# --- Deteccion de atasco ----------------------------------------------------
class StuckDetector:
    """Atascado = se ordena avanzar y no hay desplazamiento durante una ventana."""

    def __init__(self, clock=None) -> None:
        import time as _t
        self._clock = clock or _t.monotonic
        self._hist: Deque[Tuple[float, float, float, float]] = deque()  # t, cmd, x, y

    def update(self, cmd_linear: float, pos_mm: Tuple[float, float]) -> bool:
        now = self._clock()
        self._hist.append((now, cmd_linear, pos_mm[0], pos_mm[1]))
        while self._hist and now - self._hist[0][0] > STUCK_VENTANA_S * 2:
            self._hist.popleft()
        win = [h for h in self._hist if now - h[0] <= STUCK_VENTANA_S]
        if len(win) < 3 or win[-1][0] - win[0][0] < STUCK_VENTANA_S * 0.8:
            return False
        if any(abs(h[1]) < STUCK_CMD_MIN for h in win):
            return False          # no se ordeno avanzar todo el rato
        dx = win[-1][2] - win[0][2]
        dy = win[-1][3] - win[0][3]
        return math.hypot(dx, dy) < STUCK_DESPLAZ_MM

    def reset(self) -> None:
        self._hist.clear()


def escape_maneuver(rng: random.Random, bias: float = 0.0) -> List[Tuple[float, float, float]]:
    """Secuencia (linear, turn, duracion_s): retroceder, girar, reintentar."""
    lado = 1.0 if (rng.random() + bias * 0.4) > 0.5 else -1.0
    return [
        (-0.6, 0.0, 0.8),
        (0.0, lado * 0.9, rng.uniform(0.7, 1.3)),
        (0.4, -lado * 0.3, 0.6),
    ]


# --- Entradas y decisiones ---------------------------------------------------
@dataclass
class Inputs:
    battery_v: Optional[float] = None     # None / <2.5 V = lectura fallida
    on_charger: bool = False
    tof_mm: Optional[float] = None
    threat: float = 0.0                   # 0..1 (looming, chirp 'bloqueado'...)
    object_near: Optional[float] = None   # 0..1 si hay objeto de interes
    object_side: float = 0.0              # -1 izq .. +1 der
    object_novel: bool = False
    hour: int = 12
    noise: float = 0.0                    # 0..1
    cliff: bool = False
    picked_up: bool = False
    pos_mm: Tuple[float, float] = (0.0, 0.0)
    heading_deg: float = 0.0
    touched: bool = False


@dataclass
class Decision:
    state: State
    linear: float = 0.0
    turn: float = 0.0
    eyes: str = "tranquilo"
    head_deg: float = 10.0
    razon: str = ""
    changed: bool = False


@dataclass
class _Entry:
    t: float
    state: str
    razon: str


class Behavior:
    """FSM de comportamiento animal. Llamar a `update(inputs)` ~10 Hz."""

    def __init__(self, personality: Optional[Personality] = None, seed: int = 64,
                 clock=None, rng: Optional[random.Random] = None) -> None:
        import time as _t
        self.p = personality or Personality.from_seed(seed)
        self._clock = clock or _t.monotonic
        self.rng = rng or random.Random(seed)
        self.state = State.DESPERTAR
        self._t_state = self._clock()
        self.stig = Stigmergy(self._clock)
        self.stuck = StuckDetector(self._clock)
        self.log: Deque[_Entry] = deque(maxlen=200)
        self._plan: List[Tuple[float, float, float]] = []   # maniobra en curso
        self._plan_t0 = 0.0
        self._wander_turn = 0.0
        self._obj_lost_since: Optional[float] = None
        self.curiosidad = 50.0
        self.miedo = 0.0
        self.last_cmd_linear = 0.0
        self._say(f"Despierto. Soy {self.p.descripcion()}.")

    # -- utilidades -----------------------------------------------------------
    def _say(self, razon: str) -> None:
        self.log.append(_Entry(self._clock(), self.state.value, razon))

    def _go(self, new: State, razon: str) -> bool:
        if new == self.state:
            return False
        self.state = new
        self._t_state = self._clock()
        self._plan = []
        self.stuck.reset()
        self._say(razon)
        return True

    def _dwell(self) -> float:
        return self._clock() - self._t_state

    def _can_leave(self) -> bool:
        return self._dwell() >= MIN_DWELL_S[self.state.value]

    # -- bucle principal ---------------------------------------------------------
    def update(self, x: Inputs) -> Decision:
        now = self._clock()
        bat_ok = x.battery_v is not None and x.battery_v >= V_BAT_VALIDA
        changed = False

        # Fiabilidad ante todo: seguridad fisica y amenaza interrumpen sin dwell.
        if x.picked_up or x.cliff:
            self._plan = []
            d = Decision(self.state, 0.0, 0.0, "asustado", 10.0,
                         "Me levantaron/borde detectado: me quedo quieto.")
            if self.state != State.HUIR:
                self.miedo = min(100.0, self.miedo + 20.0)
            return d

        amenaza_eff = x.threat * (1.0 - 0.5 * self.p.audacia)
        if (amenaza_eff >= AMENAZA_HUIR * (1.0 - 0.3 * self.p.audacia)
                and self.state not in (State.HUIR, State.DORMIR)):
            changed = self._go(State.HUIR, "Algo se acerca demasiado deprisa: huyo.")
            self.miedo = min(100.0, self.miedo + 30.0)
            self._flee_dir = -1.0 if x.object_side > 0 else 1.0
        elif self.state == State.DORMIR and x.threat >= AMENAZA_HUIR:
            changed = self._go(State.HUIR, "Me despiertan de golpe: huyo.")

        # Transiciones normales
        if not changed:
            changed = self._transiciones(x, bat_ok)

        # Atasco (solo si se ordeno moverse)
        if self.state in (State.EXPLORAR, State.ACERCARSE, State.VUELTA_A_BASE, State.JUGAR):
            if not self._plan and self.stuck.update(self.last_cmd_linear, x.pos_mm):
                self.stig.mark_bad(x.heading_deg)
                self._plan = escape_maneuver(self.rng, self.p.sesgo_giro)
                self._plan_t0 = now
                self._say("Estoy atascado: marco esta direccion como mala y maniobro.")
                self.stuck.reset()

        d = self._accion(x)
        d.changed = changed
        self.last_cmd_linear = d.linear
        self.miedo = max(0.0, self.miedo - 0.3)
        return d

    # -- transiciones ----------------------------------------------------------------
    def _transiciones(self, x: Inputs, bat_ok: bool) -> bool:
        s = self.state
        dwell = self._dwell()
        bv: float = x.battery_v if (bat_ok and x.battery_v is not None) else 99.0
        bat_baja = bat_ok and bv <= V_BAT_BAJA and not x.on_charger

        if s == State.HUIR:
            if dwell >= HUIR_DURACION_S and x.threat < AMENAZA_CALMA:
                dest = State.VUELTA_A_BASE if self.miedo > 55 else State.EXPLORAR
                return self._go(dest, "Ya no hay peligro: me calmo.")
            return False

        if s == State.DORMIR:
            if x.hour is not None and not self.p.duerme_a(x.hour) and self._can_leave():
                return self._go(State.DESPERTAR, "Amanece: me desperezo.")
            return False

        if s == State.VUELTA_A_BASE and x.on_charger:   # llegar es un hecho fisico
            return self._go(State.DORMIR if self.p.duerme_a(x.hour) else State.DESPERTAR,
                            "Llegue a la base.")

        if not self._can_leave():
            return False

        if s == State.DESPERTAR:
            if dwell >= DESPERTAR_DURACION_S:
                if x.on_charger and bat_ok and bv < V_BAT_OK:
                    return False         # esperar carga antes de salir
                return self._go(State.EXPLORAR, "Estiramiento listo: a explorar.")
            return False

        if self.p.duerme_a(x.hour) and x.on_charger and x.threat < AMENAZA_CALMA:
            return self._go(State.DORMIR, "Es de noche y estoy en la base: duermo.")

        if bat_baja and s != State.VUELTA_A_BASE:
            return self._go(State.VUELTA_A_BASE, "Bateria baja: vuelvo a la base.")

        if s == State.VUELTA_A_BASE:
            if x.on_charger:
                return self._go(State.DORMIR if self.p.duerme_a(x.hour) else State.DESPERTAR,
                                "Llegue a la base.")
            return False

        if s == State.EXPLORAR:
            if x.object_near is not None and x.object_near > 0.1:
                pr = self.p.curiosidad * (1.0 if x.object_novel else 0.25)
                if self.rng.random() < pr:
                    return self._go(State.ACERCARSE, "Veo algo interesante: me acerco.")
            ruido_alto = x.noise > 0.6 and self.p.audacia < 0.5
            if ruido_alto:
                return self._go(State.VUELTA_A_BASE, "Mucho ruido: prefiero volver a la base.")
            return False

        if s == State.ACERCARSE:
            visible = x.object_near is not None and x.object_near >= 0.05
            if visible:
                self._obj_lost_since = None
            else:                       # histeresis: la VL parpadea; esperar antes de rendirse
                now = self._clock()
                if self._obj_lost_since is None:
                    self._obj_lost_since = now
                if now - self._obj_lost_since >= OBJETO_PERDIDO_S:
                    return self._go(State.EXPLORAR, "Perdi el objeto: sigo explorando.")
            if x.object_near is not None and x.object_near >= 0.8:
                if self.rng.random() < 0.3 + 0.6 * self.p.sociabilidad:
                    return self._go(State.JUGAR, "Estoy junto al objeto: juego.")
                return self._go(State.EXPLORAR, "Ya lo vi de cerca: sigo.")
            return False

        if s == State.JUGAR:
            if dwell >= JUGAR_DURACION_S:
                return self._go(State.EXPLORAR, "Se acabo el juego: exploro.")
        return False

    # -- acciones por estado ----------------------------------------------------------
    def _ou_turn(self, sigma: float = 0.25) -> float:
        """Ruido de giro suave (proceso Ornstein-Uhlenbeck): curvas, no zigzag."""
        self._wander_turn += (-0.15 * self._wander_turn) + self.rng.gauss(0, sigma * 0.35)
        return max(-1.0, min(1.0, self._wander_turn + 0.12 * self.p.sesgo_giro))

    def _evitar(self, x: Inputs, linear: float, turn: float) -> Tuple[float, float, str]:
        t = x.tof_mm
        if t is None:
            return linear * 0.5, turn, ""
        if t < TOF_PELIGRO_MM:
            lado = self.stig.best_heading(x.heading_deg)
            return -0.5, (0.9 if lado >= 0 else -0.9), "Obstaculo muy cerca: retrocedo."
        if t < TOF_FRENO_MM:
            lado = self.stig.best_heading(x.heading_deg)
            return 0.1, (0.8 if lado >= 0 else -0.8), "Obstaculo: giro buscando paso."
        if t < TOF_COMODO_MM:
            return min(linear, 0.3), turn, ""
        return linear, turn, ""

    def _accion(self, x: Inputs) -> Decision:
        s, p = self.state, self.p
        now = self._clock()

        # maniobra de atasco en curso
        if self._plan:
            t = now - self._plan_t0
            acc = 0.0
            for lin, trn, dur in self._plan:
                if t < acc + dur:
                    return Decision(s, lin, trn, "preocupado", 10.0)
                acc += dur
            self._plan = []

        if s == State.DORMIR:
            return Decision(s, 0.0, 0.0, "sueno", -20.0)
        if s == State.DESPERTAR:
            return Decision(s, 0.0, 0.0, "curioso", 20.0)
        if s == State.HUIR:
            lado = getattr(self, "_flee_dir", 1.0)
            return Decision(s, 0.9, 0.5 * lado, "asustado", 5.0)
        if s == State.VUELTA_A_BASE:
            return Decision(s, 0.5, self._ou_turn(0.15), "cansado", 10.0,
                            "Buscando la base.")
        if s == State.JUGAR:
            fase = (now - self._t_state) % 2.0
            return Decision(s, 0.35 if fase < 1.0 else -0.25,
                            0.8 if fase < 1.0 else -0.8, "feliz", 25.0)
        if s == State.ACERCARSE:
            near = x.object_near if x.object_near is not None else 0.0
            lin = max(0.0, min(0.6, 0.55 * (1.0 - near) + 0.1)) * (0.5 + 0.5 * p.audacia)
            trn = -0.9 * x.object_side
            lin, trn, r = self._evitar(x, lin, trn)
            return Decision(s, lin, trn, "curioso", 15.0, r)
        # EXPLORAR
        base = 0.35 + 0.35 * p.energia
        lin, trn, r = self._evitar(x, base, self._ou_turn())
        # evitar rumbos marcados como malos
        if self.stig.penalty(x.heading_deg) > 0 and not r:
            trn += 0.5 * (1 if self.rng.random() < 0.5 else -1)
        return Decision(s, lin, max(-1.0, min(1.0, trn)), "curioso" if self.curiosidad > 40 else "tranquilo",
                        10.0 + 8.0 * math.sin(now * 0.7), r)

    # -- consulta -------------------------------------------------------------------------
    def timeline(self, n: int = 20) -> List[Dict[str, object]]:
        """Ultimas decisiones en lenguaje natural (para NEXUS)."""
        return [{"t": round(e.t, 2), "estado": e.state, "razon": e.razon}
                for e in list(self.log)[-n:]]

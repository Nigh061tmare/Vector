#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Movimiento suave para Vector: rampas, amortiguacion y arcos reales.

El SDK (anki_vector.motors.AsyncMotors.set_wheel_motors) SI expone cada rueda
(mm/s) y su aceleracion (mm/s^2).  El codigo antiguo mandaba (v, v) -> sleep ->
(0, 0): arranque y parada instantaneos.  Este modulo:

  * convierte (linear, turn) a ruedas L/R conservando la proporcion del arco
    cuando se satura (el clip independiente deformaba las curvas);
  * limita la aceleracion (slew-rate) con frenado mas fuerte que arranque;
  * aplica amortiguacion de primer orden para quitar vibracion;
  * corre en un hilo a ~20 Hz con "hombre muerto": si nadie renueva el
    objetivo, el robot decelera solo hasta parar;
  * no depende del SDK: solo necesita un objeto con `.motors.set_wheel_motors`.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, Tuple

# --- Constantes (unidades: mm/s, mm/s^2, s) -------------------------------
MAX_WHEEL_MMPS: float = 120.0     # tope de seguridad por rueda
ACCEL_MMPS2: float = 220.0        # arranque
DECEL_MMPS2: float = 420.0        # frenada (mas rapida que el arranque)
DAMPING_ALPHA: float = 0.55       # 1.0 = sin filtro; menor = mas suave
TICK_HZ: float = 20.0
DEADMAN_S: float = 0.6            # sin nuevo objetivo -> objetivo 0
SEND_EPS_MMPS: float = 1.0        # no reenviar cambios menores
TURN_GAIN: float = 0.8            # misma ganancia que el codigo original


def diff_drive(linear: float, turn: float,
               max_mmps: float = MAX_WHEEL_MMPS,
               turn_gain: float = TURN_GAIN) -> Tuple[float, float]:
    """(linear, turn) en [-1, 1] -> (left, right) en mm/s.

    Misma convencion que el nucleo (`wl = lin - turn*0.8`, `wr = lin + turn*0.8`):
    turn > 0 => rueda derecha mas rapida => el robot gira a la IZQUIERDA.
    """
    lin = max(-1.0, min(1.0, float(linear)))
    trn = max(-1.0, min(1.0, float(turn)))
    left = lin - trn * turn_gain
    right = lin + trn * turn_gain
    peak = max(abs(left), abs(right), 1.0)
    # Normalizar por el pico conserva el radio del arco al saturar.
    return left / peak * max_mmps, right / peak * max_mmps


def slew(current: float, target: float, dt: float,
         accel: float = ACCEL_MMPS2, decel: float = DECEL_MMPS2) -> float:
    """Acerca `current` a `target` sin superar la aceleracion permitida."""
    if dt <= 0.0:
        return current
    diff = target - current
    # Frenar = reducir el modulo de la velocidad o cruzar el cero.
    braking = (current != 0.0) and (target * current < 0.0 or abs(target) < abs(current))
    limit = (decel if braking else accel) * dt
    if abs(diff) <= limit:
        return target
    return current + (limit if diff > 0 else -limit)


class WheelSmoother:
    """Estado (L, R) con rampa + amortiguacion.  Puro, sin hilos ni SDK."""

    def __init__(self, accel: float = ACCEL_MMPS2, decel: float = DECEL_MMPS2,
                 alpha: float = DAMPING_ALPHA) -> None:
        self.accel, self.decel, self.alpha = accel, decel, alpha
        self.left = 0.0
        self.right = 0.0

    def step(self, target_l: float, target_r: float, dt: float) -> Tuple[float, float]:
        ramp_l = slew(self.left, target_l, dt, self.accel, self.decel)
        ramp_r = slew(self.right, target_r, dt, self.accel, self.decel)
        a = self.alpha
        nl = self.left + (ramp_l - self.left) * a
        nr = self.right + (ramp_r - self.right) * a
        # Evita el "arrastre" asintotico: si ya casi llegamos, clavar el objetivo.
        if abs(nl - target_l) < 0.5:
            nl = target_l
        if abs(nr - target_r) < 0.5:
            nr = target_r
        self.left, self.right = nl, nr
        return nl, nr

    def reset(self) -> None:
        self.left = self.right = 0.0


class MotionController:
    """Hilo que lleva las ruedas hacia el objetivo con rampas.

    Uso:
        mc = MotionController(robot, guard=lambda: not (es_borde(robot) or es_up(robot)))
        mc.start()
        mc.set_target(linear=0.6, turn=-0.2)   # renovar cada < DEADMAN_S
        mc.stop()                              # parada suave
        mc.emergency_stop()                    # parada inmediata
    """

    def __init__(self, robot: Any,
                 guard: Optional[Callable[[], bool]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._robot = robot
        self._guard = guard
        self._clock = clock
        self._smooth = WheelSmoother()
        self._lock = threading.Lock()
        self._tgt = (0.0, 0.0)
        self._deadline = 0.0
        self._sent = (0.0, 0.0)
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.errors = 0

    # -- API publica -------------------------------------------------------
    def set_target(self, linear: float, turn: float, ttl: float = DEADMAN_S) -> None:
        l, r = diff_drive(linear, turn)
        with self._lock:
            self._tgt = (l, r)
            self._deadline = self._clock() + max(0.05, ttl)

    def stop(self) -> None:
        with self._lock:
            self._tgt = (0.0, 0.0)

    def emergency_stop(self) -> None:
        with self._lock:
            self._tgt = (0.0, 0.0)
        self._smooth.reset()
        self._send(0.0, 0.0, force=True)

    @property
    def speeds(self) -> Tuple[float, float]:
        return self._smooth.left, self._smooth.right

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="motion", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.emergency_stop()

    # -- Internos ----------------------------------------------------------
    def tick(self, dt: float) -> Tuple[float, float]:
        """Un paso del bucle (publico para poder testearlo sin hilo)."""
        now = self._clock()
        with self._lock:
            tl, tr = self._tgt
            expired = now > self._deadline
        if expired:
            tl = tr = 0.0
        if self._guard is not None:
            try:
                if not self._guard():
                    self._smooth.reset()
                    self._send(0.0, 0.0, force=True)
                    return 0.0, 0.0
            except Exception:
                pass
        l, r = self._smooth.step(tl, tr, dt)
        self._send(l, r)
        return l, r

    def _send(self, left: float, right: float, force: bool = False) -> None:
        sl, sr = self._sent
        if not force and abs(left - sl) < SEND_EPS_MMPS and abs(right - sr) < SEND_EPS_MMPS:
            return
        try:
            self._robot.motors.set_wheel_motors(
                left, right, left_wheel_accel=self._smooth.accel,
                right_wheel_accel=self._smooth.accel,
            )
            self._sent = (left, right)
        except Exception:
            self.errors += 1

    def _run(self) -> None:
        period = 1.0 / TICK_HZ
        last = self._clock()
        while not self._stop_evt.wait(period):
            now = self._clock()
            self.tick(now - last)
            last = now


# --- Avance continuo con ToF (alternativa a tramos de 12 mm) ------------------
TOF_CHECK_HZ = 10.0
FRENO_MM_DEFAULT = 285.0
PELIGRO_MM_DEFAULT = 155.0
COMODO_MM_DEFAULT = 430.0


def drive_distance(
    mc: "MotionController",
    total_mm: float,
    speed_mmps: float,
    read_tof: Callable[[], Optional[float]],
    unsafe: Callable[[], bool] = lambda: False,
    freno_mm: float = FRENO_MM_DEFAULT,
    peligro_mm: float = PELIGRO_MM_DEFAULT,
    comodo_mm: float = COMODO_MM_DEFAULT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Tuple[float, str]:
    """Avanza `total_mm` de forma CONTINUA (una sola rampa) vigilando el ToF a 10 Hz.

    Antes: tramos de 12 mm = un RPC con arranque/parada por tramo (a tropezones).
    Devuelve (mm_recorridos_estimados, motivo) con motivo en
    {"ok", "obstaculo", "peligro", "sin_lectura", "inseguro"}.  Para el robot con
    rampa de frenada salvo "peligro"/"inseguro", que son parada inmediata.
    """
    period = 1.0 / TOF_CHECK_HZ
    dt = 1.0 / TICK_HZ
    ticks_per_check = max(1, int(round(period / dt)))
    recorrido = 0.0
    motivo = "ok"
    last = clock()
    n = 0
    while recorrido < total_mm:
        if n % ticks_per_check == 0:
            if unsafe():
                mc.emergency_stop(); return recorrido, "inseguro"
            d = read_tof()
            if d is None:
                motivo = "sin_lectura"; break
            if d < peligro_mm:
                mc.emergency_stop(); return recorrido, "peligro"
            if d < freno_mm:
                motivo = "obstaculo"; break
            vel = min(speed_mmps, 26.0) if d < comodo_mm else speed_mmps
            mc.set_target(vel / MAX_WHEEL_MMPS, 0.0, ttl=period * 3)
        l, r = mc.tick(dt)
        sleep(dt)
        now = clock()
        recorrido += abs((l + r) / 2.0) * (now - last)
        last = now
        n += 1
    mc.stop()
    for _ in range(int(TICK_HZ)):                 # frenada suave (<= 1 s)
        l, r = mc.tick(dt); sleep(dt)
        now = clock(); recorrido += abs((l + r) / 2.0) * (now - last); last = now
        if abs(l) < 1.0 and abs(r) < 1.0:
            break
    mc.emergency_stop()
    return min(recorrido, total_mm * 1.2), motivo

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirada viva: sacadas de cabeza tipo insecto y ojos con pupila expresiva.

Puro y testeable (reloj/RNG inyectables).  APIs reales del SDK usadas por el
ejecutor (`apply_head`, `render_eyes`):
  * robot.head_angle_rad (lectura) y robot.motors.set_head_motor(rad/s)
  * robot.screen.set_screen_with_image_data(bytes, duration_sec)  (184x96 RGB565)
  * anki_vector.screen.convert_image_to_screen_data(PIL.Image)

Limites de cabeza del SDK: -22 .. +45 grados.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

HEAD_MIN_DEG = -22.0
HEAD_MAX_DEG = 45.0
SCREEN_W, SCREEN_H = 184, 96

# Dinamica de fijaciones/sacadas
FIX_MEDIAN_S = 0.9            # mediana de una fijacion (log-normal)
FIX_SIGMA = 0.55
FIX_MIN_S, FIX_MAX_S = 0.25, 3.5
SACCADE_S = 0.16              # duracion de una sacada (rapida)
SACCADE_AMP_DEG = 12.0        # amplitud tipica de la sacada de exploracion
MICRO_JITTER_DEG = 0.6        # microtemblor durante la fijacion
HEAD_KP = 6.0                 # ganancia P de rad/s por rad de error
HEAD_MAX_RADPS = 6.0


def clamp_head(deg: float) -> float:
    return max(HEAD_MIN_DEG, min(HEAD_MAX_DEG, deg))


@dataclass
class GazeTarget:
    head_deg: float
    saccade: bool          # True mientras dura la sacada (movimiento rapido)


class GazeController:
    """Genera objetivos de cabeza: fijaciones con microtemblor + sacadas.

    `interest` = (head_deg_objetivo, fuerza 0..1) sesga las sacadas hacia una
    fuente de interes (objeto, sonido); fuerza alta = mira directamente.
    """

    def __init__(self, clock=None, rng: Optional[random.Random] = None,
                 base_deg: float = 12.0) -> None:
        import time as _t
        self._clock = clock or _t.monotonic
        self.rng = rng or random.Random()
        self.base = base_deg
        self.pos = base_deg
        self._from = base_deg
        self._to = base_deg
        self._t_sacc = -1e9
        self._next_sacc = self._clock() + self._fix_time()
        self.n_saccades = 0

    def _fix_time(self, arousal: float = 0.5) -> float:
        # Mas activacion (miedo/curiosidad) = fijaciones mas cortas.
        med = FIX_MEDIAN_S * (1.4 - 0.8 * max(0.0, min(1.0, arousal)))
        t = self.rng.lognormvariate(math.log(med), FIX_SIGMA)
        return max(FIX_MIN_S, min(FIX_MAX_S, t))

    def update(self, interest: Optional[Tuple[float, float]] = None,
               arousal: float = 0.5) -> GazeTarget:
        now = self._clock()
        saccading = now - self._t_sacc < SACCADE_S
        if not saccading and now >= self._next_sacc:
            # nueva sacada
            if interest is not None and self.rng.random() < interest[1]:
                tgt = interest[0] + self.rng.gauss(0, 2.0)
            else:
                tgt = self.pos + self.rng.gauss(0, SACCADE_AMP_DEG)
                tgt = 0.7 * tgt + 0.3 * self.base          # tira suave a la base
            self._from, self._to = self.pos, clamp_head(tgt)
            self._t_sacc = now
            self._next_sacc = now + SACCADE_S + self._fix_time(arousal)
            self.n_saccades += 1
            saccading = True
        if saccading:
            k = min(1.0, (now - self._t_sacc) / SACCADE_S)
            k = k * k * (3 - 2 * k)                            # smoothstep
            self.pos = self._from + (self._to - self._from) * k
            return GazeTarget(clamp_head(self.pos), True)
        self.pos = self._to
        jitter = self.rng.gauss(0, MICRO_JITTER_DEG)
        return GazeTarget(clamp_head(self._to + jitter), False)


def head_motor_speed(current_rad: float, target_deg: float) -> float:
    """Controlador P: rad/s para `robot.motors.set_head_motor`."""
    err = math.radians(clamp_head(target_deg)) - current_rad
    return max(-HEAD_MAX_RADPS, min(HEAD_MAX_RADPS, HEAD_KP * err))


def apply_head(robot: Any, tgt: GazeTarget) -> None:
    """Ejecuta el objetivo con la API real del SDK (tolerante a fallos)."""
    try:
        robot.motors.set_head_motor(head_motor_speed(robot.head_angle_rad, tgt.head_deg))
    except Exception:
        pass


# --- Ojos --------------------------------------------------------------------
@dataclass(frozen=True)
class EyeStyle:
    hue: float             # 0..1 (set_eye_color)
    sat: float             # 0..1
    pupil: float           # radio relativo de la pupila (0.15 miedo .. 0.6 sorpresa)
    lid: float             # 0 abiertos .. 1 cerrados
    look_gain: float = 1.0 # cuanto sigue la mirada


EYE_STYLES: Dict[str, EyeStyle] = {
    "tranquilo": EyeStyle(0.42, 0.9, 0.35, 0.05),
    "curioso": EyeStyle(0.50, 0.9, 0.45, 0.0),
    "explorando": EyeStyle(0.50, 0.9, 0.48, 0.0),
    "sorpresa": EyeStyle(0.15, 0.7, 0.62, 0.0),
    "asustado": EyeStyle(0.05, 1.0, 0.16, 0.0, 1.6),
    "feliz": EyeStyle(0.33, 0.95, 0.40, 0.25),
    "cansado": EyeStyle(0.60, 0.5, 0.30, 0.55),
    "sueno": EyeStyle(0.65, 0.4, 0.28, 0.92),
    "preocupado": EyeStyle(0.10, 0.8, 0.30, 0.1),
}
#: constante de tiempo de la pupila (s): sorpresa dilata rapido, miedo contrae rapido
PUPIL_TAU_S: Dict[str, float] = {"sorpresa": 0.08, "asustado": 0.10}
PUPIL_TAU_DEFAULT_S = 0.45


class EyeState:
    """Estado de ojos con transiciones suaves de pupila y parpadeo."""

    def __init__(self, clock=None, rng: Optional[random.Random] = None) -> None:
        import time as _t
        self._clock = clock or _t.monotonic
        self.rng = rng or random.Random()
        self.name = "tranquilo"
        self.pupil = EYE_STYLES["tranquilo"].pupil
        self.lid = EYE_STYLES["tranquilo"].lid
        self._t = self._clock()
        self._next_blink = self._t + 2.0
        self._blink_until = 0.0

    def set(self, name: str) -> None:
        self.name = name if name in EYE_STYLES else "tranquilo"

    def update(self) -> Dict[str, float]:
        now = self._clock()
        dt = max(0.0, now - self._t); self._t = now
        st = EYE_STYLES[self.name]
        tau = PUPIL_TAU_S.get(self.name, PUPIL_TAU_DEFAULT_S)
        a = 1.0 - math.exp(-dt / tau)
        self.pupil += (st.pupil - self.pupil) * a
        self.lid += (st.lid - self.lid) * (1.0 - math.exp(-dt / 0.25))
        if now >= self._next_blink and self.name != "sueno":
            self._blink_until = now + 0.14
            self._next_blink = now + self.rng.uniform(2.0, 6.0)
        lid = 1.0 if now < self._blink_until else self.lid
        return {"pupil": self.pupil, "lid": lid, "hue": st.hue, "sat": st.sat}


def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return int(r * 255), int(g * 255), int(b * 255)


def render_eyes(pupil: float, lid: float, hue: float, sat: float,
                gaze_x: float = 0.0, gaze_y: float = 0.0):
    """Cara 184x96 como PIL.Image: dos ojos con iris, pupila y parpado.

    gaze_x/gaze_y en [-1, 1] desplazan iris+pupila (mirar a un lado).
    """
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    iris = hsv_to_rgb(hue, sat, 1.0)
    for cx in (SCREEN_W * 0.27, SCREEN_W * 0.73):
        cy = SCREEN_H * 0.5
        rx, ry = 30, 38
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=iris)
        ox, oy = gaze_x * 10.0, gaze_y * 8.0
        pr = max(3.0, pupil * ry * 1.1)
        d.ellipse([cx + ox - pr, cy + oy - pr, cx + ox + pr, cy + oy + pr], fill=(0, 0, 0))
        d.ellipse([cx + ox - pr * 0.35 + pr * 0.4, cy + oy - pr * 0.6,
                   cx + ox + pr * 0.15 + pr * 0.4, cy + oy - pr * 0.1], fill=(255, 255, 255))
        if lid > 0.01:                                   # parpado desde arriba
            h = int(2 * ry * max(0.0, min(1.0, lid)))
            d.rectangle([cx - rx - 1, cy - ry - 1, cx + rx + 1, cy - ry + h], fill=(0, 0, 0))
    return img


def show_eyes(robot: Any, params: Dict[str, float], gaze_x: float = 0.0,
              gaze_y: float = 0.0, duration_s: float = 0.2) -> bool:
    """Dibuja los ojos en la pantalla de Vector (APIs reales del SDK)."""
    try:
        import anki_vector
        img = render_eyes(params["pupil"], params["lid"], params["hue"], params["sat"],
                          gaze_x, gaze_y)
        data = anki_vector.screen.convert_image_to_screen_data(img)
        robot.screen.set_screen_with_image_data(data, duration_s, interrupt_running=True)
        return True
    except Exception:
        return False

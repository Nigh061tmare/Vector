#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VAD por energia adaptativa + deteccion de wake word. Puro y testeable.

No sustituye a un VAD neuronal (silero) -- no se puede verificar aqui -- pero
arregla lo que fallaba con ruido: umbral fijo, sin histeresis, sin tope de
duracion.  Trabaja con bloques PCM int16 mono (p. ej. 2000 muestras @16 kHz).
"""
from __future__ import annotations

import difflib
import re
from collections import deque
from typing import List, Optional, Tuple

import numpy as np

SR = 16000
MIN_ABS_RMS = 0.012          # suelo absoluto (fraccion de plena escala) bajo el cual nunca hay voz
START_RATIO = 3.0            # voz empieza cuando rms > suelo * START_RATIO
END_RATIO = 1.8              # y termina (histeresis) por debajo de suelo * END_RATIO
FLOOR_ALPHA = 0.05           # velocidad de adaptacion del suelo de ruido (solo sin voz)
FLOOR_INIT = 0.01
HANGOVER_S = 0.55            # silencio tras la voz antes de cerrar la frase
MIN_SPEECH_S = 0.25          # menos que esto = click/golpe, se descarta
MAX_UTTERANCE_S = 10.0       # corte forzado (evita buffers eternos con ruido continuo)
SPEECH_FLOOR_WIN = 8          # bloques: minimo reciente para seguir ruido que sube durante 'voz'
SPEECH_FLOOR_ALPHA = 0.10
SPEECH_FLOOR_AFTER_S = 1.5
PRE_ROLL_S = 0.3             # audio previo al inicio que se conserva (no cortar la 1a silaba)


def rms_i16(chunk: bytes) -> float:
    x = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


class EnergyVAD:
    def __init__(self) -> None:
        self.floor = FLOOR_INIT
        self.speaking = False
        self._buf: List[bytes] = []
        self._pre: List[Tuple[bytes, float]] = []
        self._speech_s = 0.0
        self._silence_s = 0.0
        self._total_s = 0.0
        self._recent: deque = deque(maxlen=SPEECH_FLOOR_WIN)

    def reset(self) -> None:
        self.speaking = False
        self._buf.clear(); self._pre.clear()
        self._speech_s = self._silence_s = self._total_s = 0.0
        self._recent.clear()

    def feed(self, chunk: bytes) -> Optional[bytes]:
        """Devuelve el audio de una frase completa cuando termina; si no, None."""
        dur = (len(chunk) // 2) / SR
        e = rms_i16(chunk)
        start_thr = max(MIN_ABS_RMS, self.floor * START_RATIO)
        end_thr = max(MIN_ABS_RMS * 0.6, self.floor * END_RATIO)

        if not self.speaking:
            if e >= start_thr:
                self.speaking = True
                self._buf = [c for c, _ in self._pre] + [chunk]
                self._speech_s, self._silence_s, self._total_s = dur, 0.0, dur
                self._pre.clear()
            else:
                self.floor += FLOOR_ALPHA * (e - self.floor)   # adaptar solo sin voz
                self.floor = max(1e-4, self.floor)
                self._pre.append((chunk, dur))
                while sum(d for _, d in self._pre) > PRE_ROLL_S and len(self._pre) > 1:
                    self._pre.pop(0)
            return None

        self._buf.append(chunk); self._total_s += dur
        # Ruido estacionario que sube: su minimo reciente es el nuevo suelo (la voz
        # real tiene pausas por debajo; el ruido continuo no).
        self._recent.append(e)
        if self._total_s >= SPEECH_FLOOR_AFTER_S and len(self._recent) == SPEECH_FLOOR_WIN:
            low = min(self._recent)
            if low > self.floor:
                self.floor += SPEECH_FLOOR_ALPHA * (low - self.floor)
            end_thr = max(MIN_ABS_RMS * 0.6, self.floor * END_RATIO)
        if e >= end_thr:
            self._speech_s += dur; self._silence_s = 0.0
        else:
            self._silence_s += dur
        if self._silence_s >= HANGOVER_S or self._total_s >= MAX_UTTERANCE_S:
            audio = b"".join(self._buf)
            ok = self._speech_s >= MIN_SPEECH_S
            self.reset()
            return audio if ok else None
        return None


# ---------------------------------------------------------------- wake word
WAKE = "vector"
_WAKE_VARIANTS = {"hector", "héctor", "sector", "vector", "véctor", "bector", "vektor", "victor", "vécto"}


def has_wake_word(text: str, threshold: float = 0.78) -> Tuple[bool, str]:
    """(encontrada, resto del texto sin la palabra). Tolera errores tipicos del STT."""
    toks = re.findall(r"[\wáéíóúñü]+", (text or "").lower())
    for i, t in enumerate(toks):
        if t in _WAKE_VARIANTS or difflib.SequenceMatcher(None, t, WAKE).ratio() >= threshold:
            return True, " ".join(toks[:i] + toks[i + 1:])
    return False, " ".join(toks)

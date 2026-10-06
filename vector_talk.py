"""
VECTOR ULTRA 13.5 :: PROTOCOLO DE COMUNICACION POR TONOS (estilo Flyctor)

Dos robots no intercambian mensajes por Wi-Fi: se hablan con chirps.
   camara -> VL -> cerebro-mosca -> ALTAVOZ
   microfono -> decodificador -> cerebro-mosca -> movimiento

Vocabulario (9 senales; cada una = (n_chirps, frecuencia, duracion)):
   objeto    = 1 chirp 1200 Hz  -> "encontre un objeto"
   acercate  = 2 chirps 1200 Hz -> "acercate"
   bloqueado = 1 tono largo 800 -> "camino bloqueado"
   ayuda     = 4 chirps rapidos -> "necesito ayuda"
   ack       = 1 chirp agudo    -> "recibido" (acuse)
   aqui      = 3 chirps         -> "estoy aqui"
   te_veo    = 2 chirps graves  -> "te veo"
   ven       = 2 chirps agudos  -> "ven"
   para      = 1 tono largo agudo -> "para / quieto"

Protocolo (ChirpProtocol): una senal ajena se acusa con `ack`; `ven` se
responde `aqui`; `ack` y el eco de la propia emision no generan respuesta.

El altavoz de Vector solo acepta WAV de 8000-16025 Hz, 16 bits, mono.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------- parametros
SR = 16000          # frecuencia de muestreo (dentro del rango de Vector)
AMP = 0.55          # amplitud relativa (evita clipping)

#: Vocabulario. Cada senal se define por frecuencia, numero de chirps,
#: duracion de cada chirp y silencio entre chirps (segundos).
SIGNALS: Dict[str, Dict[str, Any]] = {
    "objeto": {
        "freq": 1200, "chirps": 1, "dur": 0.12, "gap": 0.00,
        "label": "Encontre un objeto", "emoji": "🔍",
    },
    "acercate": {
        "freq": 1200, "chirps": 2, "dur": 0.12, "gap": 0.12,
        "label": "Acercate", "emoji": "➡️",
    },
    "bloqueado": {
        "freq": 800, "chirps": 1, "dur": 0.60, "gap": 0.00,
        "label": "Camino bloqueado", "emoji": "🚧",
    },
    "ayuda": {
        "freq": 1600, "chirps": 4, "dur": 0.06, "gap": 0.06,
        "label": "Necesito ayuda", "emoji": "🆘",
    },
    "ack": {
        "freq": 2000, "chirps": 1, "dur": 0.08, "gap": 0.00,
        "label": "Recibido", "emoji": "✅",
    },
    "aqui": {
        "freq": 1400, "chirps": 3, "dur": 0.10, "gap": 0.10,
        "label": "Estoy aqui", "emoji": "📍",
    },
    "te_veo": {
        "freq": 800, "chirps": 2, "dur": 0.12, "gap": 0.12,
        "label": "Te veo", "emoji": "👀",
    },
    "ven": {
        "freq": 1800, "chirps": 2, "dur": 0.12, "gap": 0.12,
        "label": "Ven", "emoji": "🫴",
    },
    "para": {
        "freq": 1800, "chirps": 1, "dur": 0.50, "gap": 0.00, "long": True,
        "label": "Para", "emoji": "✋",
    },
}
SIGNALS["bloqueado"]["long"] = True

#: Silencio al final de cada emision (ayuda al decodificador a cerrar el grupo)
TAIL_SILENCE = 0.10


# ---------------------------------------------------------------- sintesis
def synth(signal: str) -> np.ndarray:
    """Genera la onda (float32, [-1,1]) de una senal del vocabulario."""
    if signal not in SIGNALS:
        raise ValueError(f"senal desconocida: {signal!r}")
    spec = SIGNALS[signal]
    freq = float(spec["freq"])
    dur = float(spec["dur"])
    gap = float(spec["gap"])
    n_chirps = int(spec["chirps"])

    # envolvente (rampa) para evitar clics en los bordes
    n_tone = max(1, int(dur * SR))
    ramp = max(1, int(0.006 * SR))
    env = np.ones(n_tone, np.float32)
    env[:ramp] = np.linspace(0.0, 1.0, ramp, dtype=np.float32)
    env[-ramp:] = np.linspace(1.0, 0.0, ramp, dtype=np.float32)

    t = np.arange(n_tone, dtype=np.float32) / SR
    tone = (np.sin(2.0 * math.pi * freq * t) * env * AMP).astype(np.float32)

    parts: List[np.ndarray] = []
    n_gap = int(gap * SR)
    for i in range(n_chirps):
        parts.append(tone)
        if i != n_chirps - 1 and n_gap > 0:
            parts.append(np.zeros(n_gap, np.float32))
    parts.append(np.zeros(int(TAIL_SILENCE * SR), np.float32))
    return np.concatenate(parts).astype(np.float32)


def to_wav(signal: str, path: str | Path) -> Path:
    """Escribe la senal como WAV 16-bit mono (formato que acepta Vector)."""
    pcm = np.clip(synth(signal), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return path


# ---------------------------------------------------------------- decodificacion
def _segments(samples: np.ndarray, frame_ms: float = 10.0,
              thresh_ratio: float = 0.22) -> List[Dict[str, float]]:
    """Trocea el audio en segmentos de tono activo (energia + frecuencia)."""
    x = np.asarray(samples, np.float32).ravel()
    if x.size == 0:
        return []
    peak = float(np.max(np.abs(x)))
    if peak < 1e-4:
        return []
    x = x / peak

    hop = max(1, int(SR * frame_ms / 1000.0))
    win = np.hanning(hop).astype(np.float32)
    n_frames = x.size // hop
    if n_frames < 2:
        return []

    active: List[Tuple[bool, float]] = []
    for i in range(n_frames):
        chunk = x[i * hop:(i + 1) * hop] * win
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        if rms < thresh_ratio:
            active.append((False, 0.0))
            continue
        spec = np.abs(np.fft.rfft(chunk))
        k = int(np.argmax(spec[1:])) + 1          # ignora la continua
        freq = k * SR / hop
        active.append((True, freq))

    # agrupa frames activos contiguos en segmentos
    segs: List[Dict[str, float]] = []
    cur: List[Tuple[float, float]] = []
    for is_on, freq in active + [(False, 0.0)]:
        if is_on:
            cur.append((freq, 1.0))
        elif cur:
            freqs = [f for f, _ in cur]
            segs.append({
                "dur": len(cur) * frame_ms / 1000.0,
                "freq": float(np.median(freqs)),
                "n": float(len(cur)),
            })
            cur = []
    return segs


def classify(samples: np.ndarray) -> Dict[str, Any]:
    """Decodifica audio y devuelve la senal del vocabulario mas probable."""
    segs = _segments(samples)
    out: Dict[str, Any] = {
        "signal": None, "label": "", "emoji": "", "confidence": 0.0,
        "chirps": len(segs), "freq": 0.0, "dur": 0.0, "segments": segs,
    }
    if not segs:
        return out

    chirps = len(segs)
    freqs = [s["freq"] for s in segs]
    durs = [s["dur"] for s in segs]
    med_freq = float(np.median(freqs))
    med_dur = float(np.median(durs))
    out["freq"] = round(med_freq, 1)
    out["dur"] = round(med_dur, 3)

    best: Optional[str] = None
    best_score = -1.0
    for name, spec in SIGNALS.items():
        want_chirps = int(spec["chirps"])
        want_freq = float(spec["freq"])
        want_dur = float(spec["dur"])

        # numero de chirps: lo mas discriminante
        if want_chirps >= 4:
            ok_chirps = chirps >= 4
        else:
            ok_chirps = chirps == want_chirps
        if not ok_chirps:
            continue

        # tono largo vs corto
        long_tone = med_dur >= 0.30
        if bool(spec.get("long", False)) != long_tone:
            continue

        # frecuencia: debe caer cerca de la del vocabulario. Sin esto, el
        # ruido ambiente (voz, golpes) se clasifica como senales falsas.
        tol_freq = max(180.0, 0.18 * want_freq)
        if abs(med_freq - want_freq) > tol_freq:
            continue

        # duracion: tolerancia generosa pero acotada
        if abs(med_dur - want_dur) > max(0.12, 0.6 * want_dur):
            continue

        freq_pen = abs(med_freq - want_freq) / tol_freq
        dur_pen = abs(med_dur - want_dur) / max(0.12, 0.6 * want_dur)
        score = 1.0 - min(1.0, 0.6 * freq_pen + 0.4 * dur_pen)
        if score > best_score:
            best_score, best = score, name

    if best is None:
        return out
    out.update({
        "signal": best,
        "label": SIGNALS[best]["label"],
        "emoji": SIGNALS[best]["emoji"],
        "confidence": round(max(0.0, best_score), 2),
    })
    return out


# ---------------------------------------------------------------- protocolo
#: Respuesta automatica a cada senal ajena. None = no responder (evita ping-pong:
#: ven -> aqui -> ack -> fin).
REPLY: Dict[str, Optional[str]] = {
    "objeto": "ack", "acercate": "ack", "bloqueado": "ack", "ayuda": "ack",
    "aqui": "ack", "te_veo": "ack", "para": "ack",
    "ven": "aqui",
    "ack": None,
}
ECHO_MARGIN_S = 0.45      # latencia altavoz->mic + cola de reverberacion
MIN_CONFIDENCE = 0.35


class ChirpProtocol:
    """Logica de conversacion pura (sin audio ni hilos), con reloj inyectable.

    - `note_emitted()` registra lo que Vector acaba de decir: durante su duracion
      (+ margen) la misma senal oida se considera eco propio y se ignora.
    - `on_heard()` devuelve la senal con la que responder, o None.
    - Un ack oido resuelve la ultima emision pendiente de acuse.
    """

    def __init__(self, clock=None) -> None:
        import time as _t
        self._clock = clock or _t.monotonic
        self._emitted: List[Tuple[str, float, float]] = []   # (signal, t0, t_end)
        self.pending_ack: Optional[Tuple[str, float]] = None  # (signal, t0)
        self.acked: List[str] = []

    def note_emitted(self, signal: str) -> None:
        if signal not in SIGNALS:
            raise ValueError(f"senal desconocida: {signal!r}")
        now = self._clock()
        dur = synth(signal).size / SR
        self._emitted = [e for e in self._emitted if now - e[2] < 5.0]
        self._emitted.append((signal, now, now + dur + ECHO_MARGIN_S))
        if signal != "ack":
            self.pending_ack = (signal, now)

    def is_own_echo(self, signal: str) -> bool:
        now = self._clock()
        return any(sig == signal and t0 <= now <= t_end
                   for sig, t0, t_end in self._emitted)

    def on_heard(self, result: Dict[str, Any]) -> Optional[str]:
        sig = result.get("signal")
        if not sig or float(result.get("confidence", 0.0)) < MIN_CONFIDENCE:
            return None
        if self.is_own_echo(sig):
            return None
        if sig == "ack":
            if self.pending_ack is not None:
                self.acked.append(self.pending_ack[0])
                self.pending_ack = None
            return None
        return REPLY.get(sig)

    def ack_timed_out(self, timeout_s: float = 4.0) -> Optional[str]:
        """Senal emitida sin acuse pasado `timeout_s` (para reintentar). Se consume."""
        if self.pending_ack and self._clock() - self.pending_ack[1] > timeout_s:
            sig = self.pending_ack[0]
            self.pending_ack = None
            return sig
        return None


# ---------------------------------------------------------------- utilidades
def vocabulary() -> List[Dict[str, Any]]:
    """Vocabulario listo para serializar en la web."""
    return [
        {
            "id": k,
            "label": v["label"],
            "emoji": v["emoji"],
            "freq": v["freq"],
            "chirps": v["chirps"],
            "dur": v["dur"],
        }
        for k, v in SIGNALS.items()
    ]


if __name__ == "__main__":   # auto-test: sintetiza y decodifica cada senal
    print("=== AUTO-TEST DEL PROTOCOLO ===")
    ok = 0
    for name in SIGNALS:
        wav = synth(name)
        res = classify(wav)
        good = res["signal"] == name
        ok += good
        print(f"  {'OK ' if good else 'FALLA'} {name:10s} -> "
              f"detectado={res['signal']!s:10s} chirps={res['chirps']} "
              f"freq={res['freq']}Hz dur={res['dur']}s conf={res['confidence']}")
    print(f"=== {ok}/{len(SIGNALS)} correctas ===")

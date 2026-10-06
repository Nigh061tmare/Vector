"""
VECTOR ULTRA 13.5 :: SERVICIO DE COMUNICACION POR TONOS (Flyctor-style)

Cierra el bucle sensorial:
    cerebro-mosca  ->  ALTAVOZ de Vector      (emitir senal)
    microfono PC   ->  decodificador de tonos -> cerebro-mosca  (oir senal)

Es deliberadamente defensivo: ninguna excepcion debe tumbar el nucleo.
Si no hay microfono o el robot no esta disponible, el servicio queda
en modo degradado pero el resto del sistema sigue funcionando.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

import vector_talk as vt

WAV_DIR = Path(__file__).parent / "talk_wav"

#: nombre (parcial) del microfono a usar
MIC_NAME = os.getenv("TALK_MIC", "K38")
MIC_INDEX = int(os.getenv("TALK_MIC_INDEX", "0") or 0)
TALK_ON = os.getenv("TALK_ON", "1").lower() not in ("0", "false", "no")

MAX_HIST = 40
COOLDOWN = 1.2          # s: evita detectar la misma emision dos veces
ACT_RATIO = 0.18        # umbral de energia relativo al pico reciente
#: confianza minima para aceptar una decodificacion (evita falsos positivos)
MIN_CONF = float(os.getenv("TALK_MIN_CONF", "0.55"))


def _find_mic() -> Optional[int]:
    """Devuelve el indice del microfono configurado (o el de por defecto)."""
    if sd is None:
        return None
    if MIC_INDEX:
        return MIC_INDEX
    try:
        devs = sd.query_devices()
    except Exception:
        return None
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0 and MIC_NAME.lower() in d["name"].lower():
            return i
    try:
        return int(sd.default.device[0])
    except Exception:
        return None


class TalkService:
    """Emisor + receptor del vocabulario de chirps."""

    def __init__(self, robot=None) -> None:
        self.robot = robot
        self.enabled = TALK_ON
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mic: Optional[int] = None
        self._mic_err = ""
        self._last_emit_t = 0.0
        self._last_heard_t = 0.0
        self._muted_until = 0.0          # ignora el eco de nuestra propia emision
        self.historial: List[Dict[str, Any]] = []
        self.ultimo_emitido: Optional[Dict[str, Any]] = None
        self.ultimo_oido: Optional[Dict[str, Any]] = None
        self.eventos_oidos = 0
        self.eventos_emitidos = 0
        self.activo = False
        # --- datos para visualizar el "patron de frecuencia" en la web ---
        self.ultimo_audio: List[float] = []      # envolvente (waveform)
        self.ultimo_espectro: List[float] = []   # espectro FFT normalizado
        self.ultimo_segmentos: List[Dict[str, Any]] = []   # chirps detectados
        self.ultimo_audio_t = 0.0
        self.nivel = 0.0                          # nivel RMS actual

    # ------------------------------------------------------------ emision
    def emitir(self, nombre: str, volumen: int = 70) -> bool:
        """Reproduce una senal del vocabulario por el altavoz de Vector."""
        if nombre not in vt.SIGNALS:
            return False
        if self.robot is None:
            self._log_evento("emitido", nombre, ok=False, nota="robot no disponible")
            return False
        try:
            WAV_DIR.mkdir(parents=True, exist_ok=True)
            path = WAV_DIR / f"{nombre}.wav"
            if not path.exists():
                vt.to_wav(nombre, path)
        except Exception as e:
            self._log_evento("emitido", nombre, ok=False, nota=f"wav: {e}")
            return False

        with self._lock:
            try:
                # no pisar otro audio en curso
                self.robot.audio.stream_wav_file(str(path), volume=volumen)
                ok = True
                nota = ""
            except Exception as e:
                ok = False
                nota = str(e)[:120]
            self._last_emit_t = time.time()
            # silencia el microfono un momento: no queremos oirnos a nosotros
            self._muted_until = time.time() + 0.35 + 0.35
            self.ultimo_emitido = {
                "signal": nombre,
                "label": vt.SIGNALS[nombre]["label"],
                "emoji": vt.SIGNALS[nombre]["emoji"],
                "hora": time.strftime("%H:%M:%S"),
                "ok": ok,
            }
            self.eventos_emitidos += 1
        self._log_evento("emitido", nombre, ok=ok, nota=nota)
        return ok

    # ------------------------------------------------------------ recepcion
    def start(self) -> None:
        """Arranca el hilo de escucha (idempotente y a prueba de fallos)."""
        if not self.enabled or self._thread is not None:
            return
        if sd is None:
            self._mic_err = "sounddevice no instalado"
            return
        self._mic = _find_mic()
        if self._mic is None:
            self._mic_err = "sin microfono"
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="TalkListen")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        """Escucha continua: detecta actividad, captura y decodifica."""
        block = 0.10                      # s por bloque
        n_block = int(vt.SR * block)
        buf: List[np.ndarray] = []
        capturing = False
        silence = 0
        max_blocks = int(2.0 / block)     # tope de captura
        try:
            stream = sd.InputStream(device=self._mic, channels=1,
                                    samplerate=vt.SR, dtype="float32",
                                    blocksize=n_block)
            stream.start()
        except Exception as e:
            self._mic_err = f"no se pudo abrir el microfono: {e}"[:120]
            return

        self.activo = True
        # buffer rodante para la visualizacion en vivo (aunque no haya senal)
        from collections import deque
        viz_buf: "deque[np.ndarray]" = deque(maxlen=max(1, int(1.0 / block)))
        last_viz = 0.0
        try:
            while not self._stop.is_set():
                try:
                    data, _ = stream.read(n_block)
                except Exception:
                    time.sleep(0.05)
                    continue
                x = np.asarray(data, np.float32).ravel()
                if x.size == 0:
                    continue
                rms = float(np.sqrt(np.mean(x ** 2)))
                self.nivel = round(rms, 4)
                muted = time.time() < self._muted_until

                # visualizacion en vivo (~2.5 Hz) del patron de frecuencia
                viz_buf.append(x)
                if time.time() - last_viz >= 0.4 and not muted:
                    last_viz = time.time()
                    try:
                        self._guardar_analisis(np.concatenate(viz_buf))
                    except Exception:
                        pass

                if not muted and rms > ACT_RATIO * 0.15:
                    capturing = True
                    silence = 0
                    buf.append(x)
                    if len(buf) > max_blocks:
                        self._decode(np.concatenate(buf))
                        buf, capturing = [], False
                elif capturing:
                    buf.append(x)
                    silence += 1
                    if silence * block >= 0.35:
                        self._decode(np.concatenate(buf))
                        buf, capturing, silence = [], False, 0
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self.activo = False

    def _guardar_analisis(self, samples: np.ndarray) -> None:
        """Guarda envolvente + espectro + chirps del ultimo audio (para la web).

        Esto es la parte del tweet "converts it into a frequency pattern":
        el sonido se convierte en un patron que se puede ver y clasificar.
        """
        x = np.asarray(samples, np.float32).ravel()
        if x.size < 32:
            return
        # envolvente (waveform) en 160 puntos
        N = 160
        paso = max(1, x.size // N)
        env = [float(np.max(np.abs(x[i:i + paso])))
               for i in range(0, max(1, x.size - paso + 1), paso)][:N]
        mx = max(env) if env else 1.0
        self.ultimo_audio = [round(v / (mx or 1.0), 3) for v in env]
        # espectro en 96 bins
        win = np.hanning(x.size).astype(np.float32)
        sp = np.abs(np.fft.rfft(x * win))
        bins = 96
        idx = np.linspace(0, max(0, len(sp) - 1), bins + 1).astype(int)
        esp = [float(sp[idx[i]:max(idx[i] + 1, idx[i + 1])].mean())
               for i in range(bins)]
        m = max(esp) if esp else 1.0
        self.ultimo_espectro = [round(v / (m or 1.0), 3) for v in esp]
        try:
            self.ultimo_segmentos = vt._segments(x)
        except Exception:
            self.ultimo_segmentos = []
        self.ultimo_audio_t = time.time()

    def _decode(self, samples: np.ndarray) -> None:
        try:
            self._guardar_analisis(samples)
        except Exception:
            pass
        try:
            res = vt.classify(samples)
        except Exception:
            return
        if not res.get("signal"):
            return
        # umbral de confianza: el ruido ambiente no debe parecer una senal
        if float(res.get("confidence", 0.0)) < MIN_CONF:
            return
        now = time.time()
        if now - self._last_heard_t < COOLDOWN:
            return
        self._last_heard_t = now
        self.ultimo_oido = {
            "signal": res["signal"],
            "label": res["label"],
            "emoji": res["emoji"],
            "confidence": res["confidence"],
            "chirps": res["chirps"],
            "freq": res["freq"],
            "hora": time.strftime("%H:%M:%S"),
        }
        self.eventos_oidos += 1
        self._log_evento("oido", res["signal"], ok=True,
                         nota=f"conf={res['confidence']}")

    # ------------------------------------------------------------ estado
    def _log_evento(self, direccion: str, nombre: str, ok: bool = True,
                    nota: str = "") -> None:
        try:
            self.historial.append({
                "dir": direccion,
                "signal": nombre,
                "label": vt.SIGNALS.get(nombre, {}).get("label", nombre),
                "emoji": vt.SIGNALS.get(nombre, {}).get("emoji", ""),
                "ok": ok,
                "nota": nota,
                "hora": time.strftime("%H:%M:%S"),
                "t": round(time.time(), 3),
            })
            del self.historial[:-MAX_HIST]
        except Exception:
            pass

    def estado(self) -> Dict[str, Any]:
        """Estado serializable para la web."""
        return {
            "enabled": bool(self.enabled),
            "activo": bool(self.activo),
            "mic": MIC_NAME if not self._mic_err else "",
            "mic_err": self._mic_err,
            "emitidos": self.eventos_emitidos,
            "oidos": self.eventos_oidos,
            "ultimo_emitido": self.ultimo_emitido,
            "ultimo_oido": self.ultimo_oido,
            "historial": self.historial[-16:],
            "vocabulario": vt.vocabulary(),
            # analisis de audio en vivo (patron de frecuencia)
            "audio": self.ultimo_audio,
            "espectro": self.ultimo_espectro,
            "segmentos": self.ultimo_segmentos,
            "nivel": self.nivel,
            "audio_edad": (round(time.time() - self.ultimo_audio_t, 1)
                           if self.ultimo_audio_t else None),
        }


#: instancia global (la web y el nucleo comparten estado)
_SERVICE: Optional[TalkService] = None


def get_service() -> Optional[TalkService]:
    return _SERVICE


def iniciar(robot=None) -> TalkService:
    """Crea (o reutiliza) el servicio y arranca la escucha."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = TalkService(robot)
    else:
        _SERVICE.robot = robot
    _SERVICE.start()
    return _SERVICE


if __name__ == "__main__":   # auto-test del receptor (sin robot)
    import sys
    print("=== AUTO-TEST DEL SERVICIO ===")
    svc = TalkService(robot=None)
    svc.start()
    time.sleep(0.6)
    print("microfono:", svc._mic, "| err:", svc._mic_err or "(ninguno)")
    print("vocabulario:", [v["id"] for v in vt.vocabulary()])
    if svc._mic is None:
        print("Sin microfono: modo degradado (la emision sigue disponible).")
        sys.exit(0)
    print("Reproduce una senal (p.ej. el auto-test de vector_talk) para probar.")
    for _ in range(10):
        time.sleep(1)
        if svc.ultimo_oido:
            print("OIDO:", svc.ultimo_oido)
            break
    print("estado:", svc.estado()["activo"], "| oidos:", svc.eventos_oidos)

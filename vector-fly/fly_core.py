#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR-FLY :: NUCLEO
# Envuelve el connectome MaleCNS v1.0 (166.700 neuronas) y lo
# traduce a ordenes motrices para un robot tipo Vector.
#
#   camara / sensores  ->  FeatureDetectors  ->  FlyBrain (LIF)
#        ->  Trace (grupos motores)  ->  ordenes de rueda
#
# Toda la dinamica neuronal es REAL del connectome; solo el
# front-end visual y el decoder motor son modelos nuestros.
# ============================================================
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from flybrain import FlyBrain, FeatureDetectors, Trace, blob_for
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Falta el paquete flybrain. Instala con:\n"
        '  pip install "flybrain[gpu]"\n'
        f"Detalle: {exc}"
    )

# ------------------------------------------------------------
# Grupos motores etiquetados en el connectome (brain.groups)
# ------------------------------------------------------------
MOTOR_GROUPS = (
    "forward_L", "forward_R",
    "backward_L", "backward_R",
    "steer_L", "steer_R",
    "escape_L", "escape_R",
    "punch_L", "punch_R",
    "kick_L", "kick_R",
)

# Neuronas visuales / descendentes que merece la pena vigilar
WATCH_TYPES = ("LPLC2", "LC4", "LPLC1", "LC10a", "DNp01", "DNp02", "DNa02", "DNg01")


def _concat(*arrays: np.ndarray) -> np.ndarray:
    arrays = [np.asarray(a, dtype=np.int64) for a in arrays if a is not None and len(a)]
    return np.concatenate(arrays) if arrays else np.empty(0, dtype=np.int64)


class FlyCore:
    """Cerebro de mosca en tiempo real.

    Uso minimo:
        core = FlyCore()
        while True:
            ordenes = core.step(opp=(dx, size), threat=0.0)
            # ordenes: {"escape": 0.4, "forward": 0.6, ...}
    """

    #: dt biologico del modelo (20 ms -> 50 Hz)
    DT = 0.020

    def __init__(
        self,
        data: Optional[str] = None,
        device: str = "cpu",
        seed: int = 64,
        dt: float = DT,
        sensory_input: bool = True,
        encoder: Optional[Dict[str, float]] = None,
        verbose: bool = True,
    ) -> None:
        self.data = Path(data) if data else None
        self.dt = float(dt)
        self.t0 = time.time()

        self.brain = FlyBrain(
            data=self.data,
            seed=seed,
            device=device,
            dt=self.dt,
            sensory_input=sensory_input,
        )
        self.eyes = FeatureDetectors(self.brain, **(encoder or {}))

        # --- trazas de poblaciones que nos interesan -----------------
        g = self.brain.groups
        self.traces: Dict[str, Trace] = {
            "LPLC2":  Trace(self.brain, types=["LPLC2"]),
            "LC4":    Trace(self.brain, types=["LC4"]),
            "LPLC1":  Trace(self.brain, types=["LPLC1"]),
            "LC10a":  Trace(self.brain, types=["LC10a"]),
            "escape": Trace(self.brain, idx=_concat(g.get("escape_L"), g.get("escape_R"))),
            "forward": Trace(self.brain, idx=_concat(g.get("forward_L"), g.get("forward_R"))),
            "backward": Trace(self.brain, idx=_concat(g.get("backward_L"), g.get("backward_R"))),
            "steer":  Trace(self.brain, idx=_concat(g.get("steer_L"), g.get("steer_R"))),
            "punch":  Trace(self.brain, idx=_concat(g.get("punch_L"), g.get("punch_R"))),
            "kick":   Trace(self.brain, idx=_concat(g.get("kick_L"), g.get("kick_R"))),
        }

        # descendentes clave (escape / orientacion)
        self.dn: Dict[str, np.ndarray] = {}
        for name in ("DNp01", "DNp02", "DNg01"):
            idx = self.brain.cells([name])
            if len(idx):
                self.dn[name] = idx
        # DNa02 suele venir como superclase "DNa"
        dna = self.brain.cells(["DNa"])
        if len(dna):
            self.dn["DNa"] = dna

        # votos de grupo por lado (para decidir el sentido del giro)
        self.side_idx = {
            "escape_L": np.asarray(g.get("escape_L", [])),
            "escape_R": np.asarray(g.get("escape_R", [])),
            "forward_L": np.asarray(g.get("forward_L", [])),
            "forward_R": np.asarray(g.get("forward_R", [])),
            "steer_L": np.asarray(g.get("steer_L", [])),
            "steer_R": np.asarray(g.get("steer_R", [])),
            "backward_L": np.asarray(g.get("backward_L", [])),
            "backward_R": np.asarray(g.get("backward_R", [])),
        }
        self._trace_side_slot: Dict[str, np.ndarray] = {}
        for key, idx in self.side_idx.items():
            slot = np.full(self.brain.n, -1, np.int64)
            if len(idx):
                slot[idx] = np.arange(len(idx))
            self._trace_side_slot[key] = slot

        self.steps = 0
        self._last_motor: Dict[str, float] = {k: 0.0 for k in MOTOR_GROUPS}
        self._last_big: Dict[str, float] = {k: 0.0 for k in self.traces}
        self._last_side: Dict[str, float] = {k: 0.0 for k in self.side_idx}
        self._last_dn: Dict[str, float] = {k: 0.0 for k in self.dn}
        self._last_fired = 0

        # readout entrenado (opcional)
        self._read_trace = None
        self._read_loom = None
        self._read_side = None
        self._learned: Dict[str, float] = {}
        self._az = np.asarray(self.brain.azimuth, np.float32)
        self._pano_x = np.linspace(-1.0, 1.0, 128, dtype=np.float32)
        self._prev_pano = None
        self._vis_feats = np.zeros(35, np.float32)
        # decision de navegacion (readout entrenado, opt-in)
        self._read_dec = None
        self._dec_trace = None
        self._dec_sel = None
        self._dec_npop = 0
        self._dec_prev = 0.0
        self._dec_steer = 0.0
        # adaptacion retiniana (P2)
        self._retina_base = None
        # P1: escape como EVENTO (flanco de subida + cooldown)
        self._esc_prev = 0.0
        self._esc_until = 0.0
        self._esc_cooldown = 3.0
        # P5: escena semantica ('descubrir el gato') -> acercarse y mirar
        self._scene_until = 0.0
        self._scene_side = 0.0
        self._scene_near = 0.0
        self._scene_name = ""
        self._scene_novel = False
        try:
            self.load_readouts()
        except Exception:
            pass

        # --- mapa cerebral 2D (proyeccion anatomica de los somas) ---
        self._map_cell = None
        self._map_shape = (0, 0)
        self._density_norm = None
        self._map_u = None
        self._map_w = None
        self._hubs: list = []
        try:
            self._build_brain_map()
        except Exception:
            self._map_cell = None

        if verbose:
            n_photo = len(self.brain.visual) if hasattr(self.brain, "visual") else 0
            print("[FlyCore] Male CNS v1.0 listo")
            print(f"  neuronas        : {self.brain.n:,}")
            print(f"  fotorreceptores : {n_photo:,}")
            print(f"  dt              : {self.dt*1000:.1f} ms  ({1/self.dt:.0f} Hz)")
            print(f"  dispositivo     : {self.brain.device}")
            print(f"  grupos motores  : {len(MOTOR_GROUPS)}")
            print(f"  descendentes    : {', '.join(self.dn) or '-'}")

    # ---------------------------------------------------------
    # Vision espacial (panorama -> fotorreceptores + proyeccion)
    # ---------------------------------------------------------
    def vision_drive(self, pano, prev=None):
        pano = np.asarray(pano, np.float32)
        if pano.ndim > 1:
            pano = pano.mean(axis=0)
        if pano.size == 0:
            return np.zeros(0, np.float32), []
        if pano.max() > 1.0:
            pano = pano / 255.0
        x = np.linspace(-1.0, 1.0, len(pano), dtype=np.float32)
        eye = np.interp(self._az, x, pano).astype(np.float32)
        half = len(pano) // 2
        # --- adaptacion retiniana: responde al CAMBIO, no al brillo constante ---
        if self._retina_base is None or self._retina_base.shape != pano.shape:
            self._retina_base = pano.copy()
        base = self._retina_base
        self._retina_base = base + 0.05 * (pano - base)
        dev = pano - base
        dark = np.clip(1.0 - pano, 0.0, 1.0)
        pv = np.asarray(prev, np.float32) if prev is not None else np.ones_like(pano)
        growth = np.clip(dark - np.clip(1.0 - pv, 0, 1), 0.0, 1.0)
        inj = []
        c = self.eyes.cells
        for side, sl in (("L", slice(0, half)), ("R", slice(half, len(pano)))):
            loom = float(np.clip(growth[sl].mean() * 6.0, 0, 0.8))
            near = float(np.clip((-dev[sl]).mean() * 0.8, 0, 0.6))
            attn = float(np.clip((dev[sl]).mean() * 0.6, 0, 0.6))
            if loom > 0.01:
                inj.append((c["loom"][side], loom))
            if near > 0.01:
                inj.append((c["threat"][side], near))
            if attn > 0.01:
                inj.append((c["chase"][side], attn))
        self._prev_pano = pano
        self._vis_feats_from_pano(pano, prev=pv)
        return eye, inj

    def _vis_feats_from_pano(self, pano, prev=None):
        """Rasgos visuales directos para el readout (mapa de azimut fino)."""
        pano = np.asarray(pano, np.float32)
        half = len(pano) // 2
        nz = 32
        zones = np.array_split(pano, nz)
        zone_b = np.array([float(z.mean()) for z in zones], np.float32)
        k = int(np.argmax(zone_b))
        peak = float(k / (nz - 1) * 2.0 - 1.0)          # -1 izq .. +1 der
        lsum = float(pano[:half].sum())
        rsum = float(pano[half:].sum())
        lr = float((rsum - lsum) / (lsum + rsum + 1e-6))  # >0 objetivo a la derecha
        pv = np.asarray(prev, np.float32) if prev is not None else None
        if pv is None or pv.shape != pano.shape:
            # Cambio de resolucion del pano (vision semantica=128 vs camara=160):
            # se trata como primer frame en vez de romper el broadcast.
            pv = np.ones_like(pano)
        grew = float(np.clip((pano - np.clip(pv, 0, 1)).mean() * 4.0, -1, 1))
        self._vis_feats = np.concatenate([
            zone_b, np.array([peak, lr, grew], np.float32)
        ]).astype(np.float32)
        return self._vis_feats

    # ---------------------------------------------------------
    # Readout entrenado (reservoir computing)
    # ---------------------------------------------------------
    def load_readouts(self, models_dir: Optional[Path] = None) -> bool:
        """Carga loom/side + decision si existen. Devuelve True si algo cargo."""
        from flybrain.reservoir import Readout, Trace

        d = Path(models_dir) if models_dir else (Path(__file__).resolve().parent / "models")
        ok = False
        meta_p, loom_p, side_p = d / "meta.npz", d / "loom.npz", d / "side.npz"
        if meta_p.exists() and loom_p.exists() and side_p.exists():
            meta = np.load(meta_p, allow_pickle=False)
            idx = meta["idx"].astype(np.int64)
            self._read_loom = Readout.load(loom_p)
            self._read_side = Readout.load(side_p)
            self._read_trace = Trace(self.brain, idx=idx, tau=0.1)
            self._learned = {"loom": 0.0, "side": 0.0}
            ok = True
        # --- decision de navegacion (readout entrenado) ---
        dec_p, dmeta_p = d / "decision.npz", d / "decision_meta.npz"
        if dec_p.exists() and dmeta_p.exists():
            dm = np.load(dmeta_p, allow_pickle=False)
            pidx = dm["idx"].astype(np.int64)
            self._dec_npop = int(dm["npop"]) if "npop" in dm.files else len(pidx)
            sel = dm["sel"].astype(np.int64) if "sel" in dm.files else None
            self._dec_sel = sel if (sel is not None and sel.size) else None
            self._read_dec = Readout.load(dec_p)
            self._dec_trace = Trace(self.brain, idx=pidx, tau=0.1)
            self._dec_prev = 0.0
            self._dec_steer = 0.0
            ok = True
        return ok

    # ---------------------------------------------------------
    # Percepcion
    # ---------------------------------------------------------
    def visual_inject(
        self,
        opp: Optional[Tuple[float, float]] = None,
        threat: float = 0.0,
        shots: Sequence[Tuple[str, float, float]] = (),
    ) -> list:
        """Encode un objeto que se acerca. `opp=(dx, size)`; dx<0 izquierda.

        Ademas de disparar los detectores visuales, sintetiza el panorama que
        la mosca "veria" para que la decision entrenada reciba rasgos visuales
        coherentes aunque no haya camara conectada.
        """
        inject = self.eyes.inject(opp=opp, shots=shots, threat=threat)
        pano = self._pano_from_senses(opp=opp, threat=threat, shots=shots)
        if pano is not None:
            self._vis_feats_from_pano(pano, prev=self._prev_pano)
            self._prev_pano = pano
        return inject

    def scene_inject(self, objects, ttl: float = 8.0):
        """Convierte una escena semantica (vision tipo 'Astra') en estimulo.

        `objects`: secuencia de dicts con claves
            name  : str          etiqueta ('cat','chair','cable','person'...)
            side  : float -1..1  <0 izquierda, >0 derecha
            near  : float  0..1  0 lejos, 1 encima
            novel : bool         primera vez que se ve (novedad)

        Los objetos novedosos/cercanos generan atencion (chase) y activan el
        modo 'approach': la mosca gira hacia el objeto y se acerca despacio.
        """
        inj: list = []
        c = self.eyes.cells
        best = None
        pano = None
        for o in objects or ():
            if not isinstance(o, dict):
                continue
            try:
                side = max(-1.0, min(1.0, float(o.get("side", 0.0))))
                near = max(0.0, min(1.0, float(o.get("near", 0.25))))
            except Exception:
                continue
            dx = float(side * 100.0)
            size = 12.0 + near * 80.0
            inj.extend(self.eyes.inject(opp=(dx, size), threat=0.0))
            key = "L" if side < 0 else "R"
            attn = float(np.clip(0.18 + near * 0.55, 0.0, 0.75))
            inj.append((c["chase"][key], attn))
            if near >= 0.85:
                inj.append((c["threat"][key], 0.15))
            score = (2.0 if o.get("novel") else 0.4) * (0.3 + near)
            if best is None or score > best[0]:
                best = (score, o, side, near)
            p = self._pano_from_senses(opp=(dx, size), threat=0.0)
            if p is not None:
                pano = p if pano is None else np.minimum(pano, p)
        if pano is not None:
            self._vis_feats_from_pano(pano, prev=self._prev_pano)
            self._prev_pano = pano
        if best is not None:
            _, o, side, near = best
            self._scene_side = side
            self._scene_near = near
            self._scene_name = str(o.get("name", ""))
            self._scene_novel = bool(o.get("novel"))
            # habituacion: lo ya conocido solo se mira un momento.
            # La ventana debe cubrir el periodo REAL de la vision semantica
            # (la inferencia VL tarda varios segundos), o el comportamiento
            # parpadea entre 'watch' y 'chase'.
            self._scene_until = time.monotonic() + (float(ttl) if self._scene_novel else 7.0)
        return inj

    def scene_info(self) -> Dict[str, Any]:
        """Estado del objeto de interes actual (para telemetria/UI)."""
        on = time.monotonic() < self._scene_until
        return {"on": bool(on), "name": self._scene_name if on else "",
                "side": round(float(self._scene_side), 3) if on else 0.0,
                "near": round(float(self._scene_near), 3) if on else 0.0,
                "novel": bool(on and self._scene_novel)}

    def _pano_from_senses(self, opp=None, threat: float = 0.0, shots=(), n: int = 128):
        """Panorama 1-D (0..1, fondo 0.9) reconstruido del estimulo sintetico."""
        if opp is None and not shots and threat <= 0.0:
            return None
        x = np.linspace(-1.0, 1.0, n, dtype=np.float32)
        lum = np.full(n, 0.9, np.float32)
        blobs = []
        if opp is not None:
            blobs.append((float(opp[0]), float(opp[1]), 0.85))
        for _key, dx, size in (shots or ()):
            blobs.append((float(dx), float(size), 0.70))
        for dx, size, dark in blobs:
            c = float(np.clip(dx / 110.0, -1.0, 1.0))
            hw = float(np.clip(size / max(abs(dx), 8.0) * 0.5, 0.03, 0.7))
            inside = np.abs(x - c) <= hw
            lum[inside] = np.minimum(lum[inside], np.float32(0.9 * (1.0 - dark)))
        if threat > 0.0:
            lum = np.clip(lum * (1.0 - 0.3 * float(np.clip(threat, 0.0, 1.0))), 0.0, 1.0)
        return lum

    def camera_inject(
        self,
        gray: np.ndarray,
        prev_gray: Optional[np.ndarray] = None,
        darkness_threshold: float = 0.45,
        row_band: Tuple[float, float] = (0.35, 0.75),
    ) -> list:
        """Convierte un fotograma gris (0..1) en estimulo visual para la mosca.

        Estrategia sin OpenCV:
          * recorta una banda horizontal central,
          * detecta la columna de mayor "oscuridad x movimiento",
          * de ahi salen (dx, size) y una amenaza proporcional al crecimiento.
        Devuelve la lista `inject` y guarda el ultimo estimulo en self.last_visual.
        """
        gray = np.asarray(gray, dtype=np.float32)
        if gray.size == 0:
            return []
        if gray.max() > 1.0:
            gray = gray / 255.0

        if gray.ndim == 1:
            # panoramica 1-D ya calculada (fotograma aplanado en columnas)
            col = gray
            motion = (np.abs(col - np.asarray(prev_gray, dtype=np.float32))
                      if prev_gray is not None and np.asarray(prev_gray).shape == col.shape
                      else np.zeros_like(col))
        else:
            h, w = gray.shape[:2]
            y0, y1 = int(h * row_band[0]), int(h * row_band[1])
            band = gray[y0:y1, :]
            if band.size == 0:
                return []
            col = band.mean(axis=0)                      # 1-D panoramica
            if prev_gray is not None:
                p = np.asarray(prev_gray, dtype=np.float32)
                if p.max() > 1.0:
                    p = p / 255.0
                pband = p[y0:y1, :].mean(axis=0)
                motion = np.abs(col - pband)
            else:
                motion = np.zeros_like(col)

        w = len(col)
        dark = np.clip(1.0 - col / max(float(col.mean()), 1e-3), 0, 2)
        sal = dark + 2.0 * motion
        j = int(np.argmax(sal))
        strength = float(np.clip((sal[j] - 0.35) / 0.8, 0.0, 1.0))

        # ancho del blob (objeto) alrededor del pico
        thr = sal[j] * 0.6
        left = j
        while left > 0 and sal[left - 1] > thr:
            left -= 1
        right = j
        while right < len(sal) - 1 and sal[right + 1] > thr:
            right += 1
        width = max(1, right - left + 1)
        size = float(np.clip(width / w * 4.0, 0.05, 1.0))     # tamano angular aprox.

        # dx en unidades "de juego" (-1 izq ... +1 der) escalado como eyes
        dx_unit = (j / max(w - 1, 1)) * 2.0 - 1.0
        dx = float(np.clip(dx_unit * 110.0, -110.0, 110.0))

        movement = float(np.clip((motion.max() - 0.02) / 0.25, 0.0, 1.0))
        threat = float(np.clip(strength * (0.4 + 0.6 * movement), 0.0, 1.0))

        self.last_visual = {"dx": dx, "size": size, "threat": threat, "x_norm": dx_unit}
        if strength <= 0.02:
            return []
        return self.eyes.inject(opp=(dx, size), threat=threat)

    # ---------------------------------------------------------
    # Un paso de simulacion
    # ---------------------------------------------------------
    def step(self, inject: Optional[list] = None, eye_drive=None) -> Dict[str, float]:
        """Avanza 20 ms de neurodinamica y devuelve el estado motor.

        Devuelve un dict con:
          * trazas normalizadas por poblacion (escape, forward, backward, steer, punch, kick)
          * 'LPLC2','LC4','LC10a' -> deteccion visual
          * 'escape_L','escape_R','steer_L','steer_R' -> voto lateral
          * 'fire_count' -> spikes globales del paso
        """
        fired = self.brain.step(eye_drive=eye_drive, inject=inject or ())

        # readout entrenado (reservoir): detecta looming y su lado
        if self._read_trace is not None:
            try:
                feat = self._read_trace.observe(fired)
                self._learned["loom"] = float(self._read_loom.predict(feat))
                self._learned["side"] = float(self._read_side.predict(feat))
            except Exception:
                pass

        # decision de navegacion: reservorio + rasgos visuales + memoria de giro
        if self._read_dec is not None and self._dec_trace is not None:
            try:
                df = np.asarray(self._dec_trace.observe(fired), np.float32)
                res = df[:self._dec_npop]
                if self._dec_sel is not None:
                    res = res[self._dec_sel]
                x = np.concatenate([res, np.asarray(self._vis_feats, np.float32),
                                    np.array([self._dec_prev], np.float32)])
                self._dec_steer = float(np.clip(self._read_dec.predict(x), -1.0, 1.0))
                self._dec_prev = self._dec_steer
            except Exception:
                pass

        # trazas de poblaciones completas
        for name, tr in self.traces.items():
            tr.observe(fired)
            self._last_big[name] = float(tr.features().mean())

        # votos laterales crudos (tasa instantanea por lado)
        arr = fired if isinstance(fired, np.ndarray) else np.asarray(fired)
        for key, slot in self._trace_side_slot.items():
            if len(slot) and arr.size:
                s = slot[arr]
                self._last_side[key] = float((s >= 0).sum())

        self._last_fired = int(arr.size) if arr.ndim == 1 else sum(len(f) for f in fired)

        # descendentes (tasa por paso)
        dn_out: Dict[str, float] = {}
        for name, idx in self.dn.items():
            if arr.size:
                hit = np.isin(arr, idx)
                dn_out[name] = float(hit.sum())
            else:
                dn_out[name] = 0.0
        self._last_dn = dn_out

        self.steps += 1
        return self.snapshot()

    def snapshot(self) -> Dict[str, float]:
        """Estado actual sin avanzar (para decoders y telemetria)."""
        snap: Dict[str, float] = {}
        for k, v in self._last_big.items():
            snap[k] = round(v, 5)
        for k, v in self._last_side.items():
            snap[k] = round(v, 3)
        snap["LPLC2"] = round(self._last_big.get("LPLC2", 0.0), 5)
        snap["LC4"] = round(self._last_big.get("LC4", 0.0), 5)
        snap["LC10a"] = round(self._last_big.get("LC10a", 0.0), 5)
        for k, v in self._last_dn.items():
            snap["DN_" + k] = round(float(v), 3)
        snap["fire_count"] = self._last_fired
        snap["steps"] = self.steps
        snap["t"] = round(self.steps * self.dt, 3)
        for k, v in self._learned.items():
            snap["LRN_" + k] = round(float(v), 3)
        if self._read_dec is not None:
            snap["DEC_steer"] = round(float(self._dec_steer), 3)
        return snap

    def spike_histogram(self, bins: int = 128) -> list:
        """Densidad de disparo a lo largo de las 166.700 neuronas (para ver en vivo)."""
        f = np.asarray(getattr(self.brain, "fired", []), dtype=np.int64).ravel()
        if f.size == 0:
            return [0] * bins
        h, _ = np.histogram(np.clip(f, 0, self.brain.n - 1), bins=bins,
                            range=(0, self.brain.n))
        return h.astype(int).tolist()

    # ---------------------------------------------------------
    # Mapa cerebral (proyeccion anatomica para el visor)
    # ---------------------------------------------------------
    def _build_brain_map(self, gx: int = 96, gy: int = 72) -> None:
        """Proyecta los somas (posiciones EM) a una rejilla 2D para dibujar el cerebro."""
        pos = getattr(self.brain, "positions", None)
        if pos is None:
            return
        p = np.asarray(pos, np.float32)
        if p.ndim != 2 or p.shape[1] < 2:
            return
        ok = ~np.isnan(p).any(axis=1)
        if not np.any(ok):
            return
        v = p[ok]
        lo, hi = v.min(axis=0), v.max(axis=0)
        spread = hi - lo
        axes = np.argsort(spread)[::-1][:2]
        a, b = int(axes[0]), int(axes[1])
        u = np.clip((p[:, a] - lo[a]) / (spread[a] + 1e-6), 0.0, 1.0)
        w = np.clip((p[:, b] - lo[b]) / (spread[b] + 1e-6), 0.0, 1.0)
        # las filas sin posicion (NaN) quedan a 0 antes de convertir a indices
        u = np.where(ok, u, 0.0)
        w = np.where(ok, w, 0.0)
        ci = np.clip((u * (gx - 1)).astype(np.int64), 0, gx - 1)
        cj = np.clip((w * (gy - 1)).astype(np.int64), 0, gy - 1)
        cell = (cj * gx + ci).astype(np.int64)
        cell[~ok] = -1
        self._map_cell = cell
        self._map_shape = (gy, gx)
        self._map_u, self._map_w = u, w
        dens = np.bincount(cell[ok], minlength=gx * gy).astype(np.float32)
        self._density_norm = dens / (dens.max() + 1e-6)

        hubs = []
        want = [("LPLC2", self.brain.cells(["LPLC2"])),
                ("LC4", self.brain.cells(["LC4"])),
                ("LC10a", self.brain.cells(["LC10a"]))]
        for name in ("DNp01", "DNg01", "DNa"):
            want.append((name, self.brain.cells([name])))
        for name in ("escape", "forward", "steer"):
            idx = _concat(self.brain.groups.get(name + "_L"),
                          self.brain.groups.get(name + "_R"))
            want.append((name, idx))
        for name, idx in want:
            if idx is None or len(idx) == 0:
                continue
            idx = np.asarray(idx, np.int64)
            good = ok[idx]
            if not np.any(good):
                continue
            hubs.append({"name": name,
                         "x": round(float(u[idx][good].mean()), 4),
                         "y": round(float(w[idx][good].mean()), 4)})
        self._hubs = hubs

    def spike_map(self):
        """Actividad por celda del mapa cerebral (lista plana gy*gx)."""
        if self._map_cell is None:
            return None
        gy, gx = self._map_shape
        f = np.asarray(getattr(self.brain, "fired", []), dtype=np.int64).ravel()
        if f.size == 0:
            return [0] * (gx * gy)
        cells = self._map_cell[f]
        cells = cells[cells >= 0]
        if cells.size == 0:
            return [0] * (gx * gy)
        m = np.bincount(cells, minlength=gx * gy)
        return m.astype(int).tolist()

    def spike_sample(self, limit: int = 256) -> list:
        """Muestra de indices de neuronas que dispararon en el ultimo paso.

        Para el raster en vivo: no manda los ~miles de disparos, sino una
        submuestra representativa (limit) ordenada por indice anatomico.
        """
        f = np.asarray(getattr(self.brain, "fired", []), dtype=np.int64).ravel()
        if f.size == 0:
            return []
        if f.size > limit:
            f = np.sort(f)
            f = f[::max(1, f.size // limit)][:limit]
        return f.tolist()

    def brain_map_meta(self):
        """Geometria + densidad estatica + hubs del mapa cerebral (una sola vez)."""
        if self._map_cell is None:
            return None
        gy, gx = self._map_shape
        dens = (self._density_norm * 255.0).astype(int).tolist() \
            if self._density_norm is not None else [0] * (gx * gy)
        return {"gx": gx, "gy": gy, "density": dens, "hubs": list(self._hubs)}

    def reset(self, seed: Optional[int] = None) -> None:
        self.brain.reset(seed)
        for tr in self.traces.values():
            tr.reset()
        self.steps = 0
        self.eyes.previous = {}
        self._last_fired = 0
        self._last_motor = {k: 0.0 for k in MOTOR_GROUPS}
        self._last_big = {k: 0.0 for k in self.traces}
        self._last_side = {k: 0.0 for k in self.side_idx}
        if self._dec_trace is not None:
            self._dec_trace.reset()
        self._dec_prev = 0.0
        self._dec_steer = 0.0
        self._vis_feats = np.zeros(35, np.float32)
        self._prev_pano = None
        self._retina_base = None
        self._esc_prev = 0.0
        self._esc_until = 0.0

    # ---------------------------------------------------------
    # Decoder: neuronas -> ruedas
    # ---------------------------------------------------------
    def motor_command(self, snapshot: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Traduce la actividad motriz a ordenes tipo robot diferencial.

        Salida:
            {
              "left": v, "right": v,      # -1..1 por rueda
              "linear": v, "turn": v,     # velocidad / giro
              "escape": bool,             # reflejo de escape activo
              "mode": "escape|chase|wander|idle",
              "reflex_side": "L"|"R"|""
            }
        """
        s = snapshot or self.snapshot()
        esc = s.get("escape", 0.0)
        fwd = s.get("forward", 0.0)
        bwd = s.get("backward", 0.0)
        chs = s.get("LC10a", 0.0)

        esc_L, esc_R = s.get("escape_L", 0.0), s.get("escape_R", 0.0)
        st_L, st_R = s.get("steer_L", 0.0), s.get("steer_R", 0.0)

        # P1: escape como EVENTO = flanco de subida de la poblacion de escape
        import time as _time
        _now = _time.monotonic()
        _rise = float(esc) - float(self._esc_prev)
        self._esc_prev = float(esc)
        strong = max(esc_L, esc_R) >= 1.0
        if (_rise >= 0.12 or strong) and _now >= self._esc_until:
            escape_on = True
            self._esc_until = _now + self._esc_cooldown
        else:
            escape_on = False
        diff = (esc_R - esc_L)
        steer = float(np.tanh(diff / 2.0))
        if st_L != st_R:
            steer += float(np.tanh((st_R - st_L) / 2.0))
        steer = float(np.clip(steer, -1.0, 1.0))

        linear = float(np.clip(fwd * 2.5 - bwd * 2.5, -1.0, 1.0))
        if not escape_on:
            # P3: exploracion suave guiada por la decision de la mosca
            if linear < 0.2:
                linear = 0.2
            steer = float(np.clip(steer + 0.9 * self._dec_steer, -1.0, 1.0))
        if escape_on:
            linear = max(linear, 0.75)          # huida: acelera
            mode = "escape"
            reflex_side = "R" if diff > 0 else ("L" if diff < 0 else "")
        elif _now < self._scene_until and self._scene_near >= 0.2:
            # P5 'descubrir el gato': girar HACIA el objeto. Si es NUEVO,
            # ademas se acerca; si ya lo conoce, solo lo mira un momento.
            mode = "approach" if self._scene_novel else "watch"
            reflex_side = ("L" if self._scene_side < -0.15 else
                           ("R" if self._scene_side > 0.15 else ""))
            steer = float(np.clip(-0.95 * self._scene_side, -1.0, 1.0))
            if (not self._scene_novel) or self._scene_near >= 0.85:
                linear = 0.0                    # mirar / ya encima: quieto
            else:
                linear = float(np.clip(0.30 + 0.35 * (1.0 - self._scene_near),
                                       0.0, 0.65))
        elif chs > 0.0:
            mode = "chase"
            reflex_side = ""
        elif linear > 0.05:
            mode = "wander"
            reflex_side = ""
        else:
            mode = "idle"
            reflex_side = ""

        left = linear - steer * 0.8
        right = linear + steer * 0.8
        # Normalizar por el pico (no recortar cada rueda): conserva el radio del arco.
        _peak = max(abs(left), abs(right), 1.0)
        left, right = float(left / _peak), float(right / _peak)
        out = {
            "left": round(left, 4),
            "right": round(right, 4),
            "linear": round(linear, 4),
            "turn": round(steer, 4),
            "escape": bool(escape_on),
            "mode": mode,
            "reflex_side": reflex_side,
            "scene": self.scene_info(),
        }
        if self._read_dec is not None:
            out["decision"] = round(float(self._dec_steer), 3)
            out["decision_on"] = True
        return out

    # ---------------------------------------------------------
    # Utilidades
    # ---------------------------------------------------------
    def brain_summary(self) -> Dict[str, Any]:
        return {
            "neurons": int(self.brain.n),
            "photoreceptors": int(len(self.brain.visual)) if hasattr(self.brain, "visual") else 0,
            "dt_ms": round(self.dt * 1000, 2),
            "device": str(self.brain.device),
            "groups": {k: int(len(v)) for k, v in self.brain.groups.items()},
            "watch": list(self.dn),
        }


def cargar_core_desde_env() -> FlyCore:
    """Crea un FlyCore usando variables de entorno (FLY_*)."""
    return FlyCore(
        data=os.getenv("FLY_DATA") or None,
        device=os.getenv("FLY_DEVICE", "cpu"),
        seed=int(os.getenv("FLY_SEED", "64")),
        dt=float(os.getenv("FLY_DT", str(FlyCore.DT))),
        sensory_input=os.getenv("FLY_SENSORY", "1").lower() not in ("0", "false", "no"),
    )


if __name__ == "__main__":
    core = cargar_core_desde_env()
    print(json.dumps(core.brain_summary(), indent=2, ensure_ascii=False))
    # mini demo: 25 pasos con un objeto que se acerca por la izquierda
    for t in range(25):
        dx = -60 + t * 2.0     # se aproxima al centro
        size = 10 + t * 1.5
        core.step(inject=core.visual_inject(opp=(dx, size), threat=min(1.0, t / 20)))
        cmd = core.motor_command()
        if t % 5 == 0 or cmd["escape"]:
            print(f"t={t*20:4d}ms dx={dx:6.1f} LC4={core.snapshot()['LC4']:.4f} "
                  f"escape={core.snapshot()['escape']:.4f} -> {cmd['mode']:6s} "
                  f"L={cmd['left']:+.2f} R={cmd['right']:+.2f}")

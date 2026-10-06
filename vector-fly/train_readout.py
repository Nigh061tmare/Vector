#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VECTOR-FLY :: ENTRENAR READOUT (reservoir computing)
# Aprende a leer la actividad de la mosca: detecta looming y su lado.
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fly_core import FlyCore  # noqa: E402
from flybrain.reservoir import Readout, Trace, auc  # noqa: E402
from scipy.special import expit  # noqa: E402

MODELS = HERE / "models"
N_FEATURES = 2000
STEPS = 12
EPISODES_PER_CLASS = 60


def make_trace(brain, idx):
    return Trace(brain, idx=idx, tau=0.1)


def episodes(core, idx, rng):
    """Genera episodios: 0=nada, 1=looming izq, 2=looming der."""
    trace = make_trace(core.brain, idx)
    X, y, groups = [], [], []
    g = 0
    plan = [0, 1, 2] * EPISODES_PER_CLASS
    rng.shuffle(plan)
    for cls in plan:
        trace.reset()
        for t in range(STEPS):
            if cls == 0:
                inject = []
            else:
                size = 18.0 + t * 6.0            # crece = looming
                dx = -30.0 if cls == 1 else 30.0
                inject = core.visual_inject(opp=(dx, size))
            core.brain.step(inject=inject)
            feat = trace.observe(core.brain.last_fired
                                 if hasattr(core.brain, "last_fired") else [])
        X.append(feat.copy())
        y.append(cls)
        groups.append(g)
        g += 1
    return np.asarray(X, np.float32), np.asarray(y), np.asarray(groups)


def main():
    MODELS.mkdir(parents=True, exist_ok=True)
    print("=" * 64)
    print(" VECTOR-FLY :: ENTRENAMIENTO DEL READOUT")
    print("=" * 64)
    rng = np.random.default_rng(7)
    core = FlyCore(device="cpu")
    n = core.brain.n
    idx = np.sort(rng.choice(n, size=min(N_FEATURES, n), replace=False))
    print(f"neuronas: {n} | features (muestra): {len(idx)} | pasos/episodio: {STEPS}")

    # El brain no expone last_fired: usamos el retorno de step
    trace = make_trace(core.brain, idx)
    X, y, groups = [], [], []
    g = 0
    plan = [0, 1, 2] * EPISODES_PER_CLASS
    rng.shuffle(plan)
    print("recogiendo actividad...")
    for cls in plan:
        trace.reset()
        feat = None
        for t in range(STEPS):
            inject = []
            if cls != 0:
                size = 18.0 + t * 6.0
                dx = -30.0 if cls == 1 else 30.0
                inject = core.visual_inject(opp=(dx, size))
            fired = core.brain.step(inject=inject)
            feat = trace.observe(fired)
        X.append(feat.copy()); y.append(cls); groups.append(g); g += 1
    X = np.asarray(X, np.float32)
    y = np.asarray(y)
    groups = np.asarray(groups)
    print(f"dataset: {X.shape}  clases: {np.bincount(y, minlength=3).tolist()}")

    # Tarea 1: looming (clase 0) vs no-looming (clases 1,2)
    y_loom = (y > 0).astype(float)
    r1 = Readout.fit(X, y_loom, kind="logistic", groups=None, verbose=True)
    r1.save(MODELS / "loom.npz")
    print(f"  -> LOOMING vs NADA   AUC={r1.cv_score:.3f}  (modelo: loom.npz)")

    # Tarea 2: lado del looming (izq=1 vs der=2)
    m = y > 0
    y_side = (y[m] == 2).astype(float)
    r2 = Readout.fit(X[m], y_side, kind="logistic", groups=None, verbose=True)
    r2.save(MODELS / "side.npz")
    print(f"  -> IZQUIERDA vs DERECHA  AUC={r2.cv_score:.3f}  (modelo: side.npz)")

    np.savez(MODELS / "meta.npz", idx=idx, n_features=len(idx), n_neurons=n,
             steps=STEPS, loom_auc=r1.cv_score, side_auc=r2.cv_score)
    print(f"\nOK. Modelos y metadatos en {MODELS}")


if __name__ == "__main__":
    main()

# ============================================================
# VECTOR-FLY — SMOKE TEST DEL CONNECTOME (MaleCNS v1.0)
# ============================================================
import time
import numpy as np
from flybrain import FlyBrain

print("=" * 64)
print(" VECTOR-FLY :: SMOKE TEST")
print("=" * 64)

t0 = time.time()
brain = FlyBrain(device="cpu")
t_load = time.time() - t0
print(f"Brain cargado en {t_load:.1f}s")
print(f"  neuronas      : {brain.n:,}")
print(f"  batch         : {brain.batch}")
print(f"  photoreceptores: {len(brain.visual):,}")
print(f"  dt            : {brain.dt*1000:.0f} ms")
print(f"  grupos        : {list(brain.groups.keys())}")

# --- Identificar neuronas clave -------------------------------------------
lc4 = brain.cells(["LC4"])
lplc2 = brain.cells(["LPLC2"])
dnp01 = brain.cells(["DNp01"])
dn_all = brain.cells(["descending_neuron"])
print(f"\nNeuronas LC4     : {len(lc4)}")
print(f"Neuronas LPLC2   : {len(lplc2)}")
print(f"Neuronas DNp01   : {len(dnp01)}")
print(f"Neuronas descend.: {len(dn_all)}")

# --- Linea base (reposo) ---------------------------------------------------
brain.reset(seed=64)
base_dn = 0
N = 200
for _ in range(N):
    fired = brain.step()
    base_dn += np.isin(fired, dn_all).sum()
print(f"\nReposo: {base_dn} disparos descendentes en {N} pasos ({N*brain.dt:.1f}s sim)")

# --- Estimulo visual: looming lateral izquierdo -----------------------------
brain.reset(seed=64)
stim_dn = 0
stim_dnp01 = 0
lat = []
for i in range(N):
    if i < 100:
        brain.stimulate(brain.cells(["LC4", "LPLC2"], side="L"), 0.8)
    fired = brain.step()
    n_dn = np.isin(fired, dn_all).sum()
    n_p = np.isin(fired, dnp01).sum()
    stim_dn += n_dn
    stim_dnp01 += n_p
    if n_p > 0 and len(lat) < 5:
        lat.append(i)

print(f"Estimulo LC4/LPLC2 izq: {stim_dn} disparos descendentes | DNp01: {stim_dnp01}")
print(f"  latencia DNp01 (primeros pasos): {lat if lat else 'sin respuesta'}")

# --- Benchmark -------------------------------------------------------------
brain.reset(seed=1)
# calentar (compilacion numba)
for _ in range(5):
    brain.step()
t0 = time.time()
B = 100
for _ in range(B):
    brain.step()
dt = (time.time() - t0) / B
print(f"\nBENCHMARK CPU (1 mosca): {dt*1000:.2f} ms/paso  |  "
      f"factor tiempo real {brain.dt/dt:.2f}x  |  {1/dt:.0f} Hz")
print("=" * 64)

# ============================================================
# VECTOR-FLY — BENCHMARK CPU vs GPU (batch de moscas)
# ============================================================
import time
import sys

import numpy as np
from flybrain import FlyBrain


def bench(device: str, batch: int, steps: int = 300):
    try:
        b = FlyBrain(device=device, batch=batch, sensory_input=True)
    except Exception as e:
        return None, str(e)
    # deja asentar
    for _ in range(20):
        b.step()
    t0 = time.perf_counter()
    for _ in range(steps):
        b.step()
    dt = (time.perf_counter() - t0) / steps
    return dt, None


def main():
    print("=" * 70)
    print(" VECTOR-FLY :: BENCHMARK EMERGENTE (MaleCNS 166,700 neuronas)")
    print("=" * 70)
    print(f"{'device':<8}{'batch':>6}{'ms/paso':>12}{'Hz':>10}{'x tiempo real':>16}")
    print("-" * 70)
    for device, batches in (("cpu", [1, 8]), ("cuda", [1, 8, 64, 512])):
        for batch in batches:
            dt, err = bench(device, batch)
            if err:
                print(f"{device:<8}{batch:>6}   ERROR: {err[:40]}")
                continue
            hz = 1.0 / dt
            print(f"{device:<8}{batch:>6}{dt*1000:>11.2f}{hz:>10.0f}{hz*0.020:>15.1f}x")
    print("-" * 70)
    print("Nota: 1 paso = 20 ms biologicos. 'x tiempo real' = pasos/s * 0.020 s")


if __name__ == "__main__":
    main()

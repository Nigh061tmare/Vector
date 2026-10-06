#!/usr/bin/env python3
"""Smoke test: Vector arranca, curva y frena con rampas (requiere robot real).

Ejecutar con el venv del nucleo:  VectorSDK\\venv\\Scripts\\python.exe smoke_movimiento.py
Con --dry no toca el robot y solo imprime el perfil de velocidad.
"""
import sys
import time

from vector_motion import MotionController, TICK_HZ


class _Dry:
    class motors:
        @staticmethod
        def set_wheel_motors(l, r, left_wheel_accel=0.0, right_wheel_accel=0.0):
            print(f"L={l:7.1f}  R={r:7.1f} mm/s")


def run(robot) -> None:
    mc = MotionController(robot)
    dt = 1.0 / TICK_HZ
    plan = [(0.7, 0.0, 1.5), (0.6, 0.5, 1.5), (0.6, -0.5, 1.5), (0.0, 0.0, 1.0)]
    try:
        for lin, trn, dur in plan:
            fin = time.monotonic() + dur
            while time.monotonic() < fin:
                mc.set_target(lin, trn, ttl=0.3); mc.tick(dt); time.sleep(dt)
    finally:
        mc.emergency_stop()


if __name__ == "__main__":
    if "--dry" in sys.argv:
        run(_Dry())
    else:
        import anki_vector
        with anki_vector.Robot() as robot:
            robot.behavior.set_head_angle(anki_vector.util.degrees(10))
            run(robot)

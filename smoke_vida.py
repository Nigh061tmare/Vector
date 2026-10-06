#!/usr/bin/env python3
"""Smoke test de la VIDA animal.

  python smoke_vida.py --sim        sin robot: simulacion 2-D, imprime mapa y linea de tiempo
  python smoke_vida.py [segundos]   CON robot real (venv del nucleo; nucleo PARADO: solo una
                                    conexion SDK con control a la vez). Vector debe estar en
                                    suelo libre; Ctrl+C lo detiene.
"""
import sys

if "--sim" in sys.argv:
    import vector_life
    vector_life.simulate(seconds=120)
    sys.exit(0)

import anki_vector  # noqa: E402
import vector_life  # noqa: E402

secs = float(next((a for a in sys.argv[1:] if a.replace(".", "").isdigit()), 60))
with anki_vector.Robot() as robot:
    try:
        print("Viviendo", secs, "s ... (Ctrl+C para parar)")
        vector_life.vivir(robot, secs)
    finally:
        robot.motors.stop_all_motors()
        v = vector_life.snapshot()
        print(v.get("mapa", ""))
        for e in v.get("timeline", [])[-10:]:
            print(f"[{e['estado']}] {e['razon']}")

import os, sys, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_life as vl


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_sim_explores_without_hitting_walls(seed):
    out = vl.simulate(seconds=90, seed=seed, verbose=False)
    assert out["collisions"] <= 2          # pared: solo roces puntuales
    assert out["celdas_vistas"] > 300      # construye mapa
    assert out["estados"].get("EXPLORAR", 0) > 700


def test_sense_survives_a_broken_robot():
    class Broken:
        def get_battery_state(self): raise RuntimeError("grpc")
    s = vl.sense(Broken(), {})
    assert s["tof_mm"] is None and s["battery_v"] is None and s["cliff"] is False


def test_sense_battery_zero_uses_last_good_value():
    class R:
        def __init__(self, v): self.v = v
        def get_battery_state(self): return type("B", (), {"battery_volts": self.v})()
    cache = {}
    assert vl.sense(R(4.0), cache)["battery_v"] == 4.0
    assert vl.sense(R(0.0), cache)["battery_v"] == 4.0


def test_wheel_commands_are_ramped_not_stepped():
    rb = vl.SimRobot(); sent = []
    orig = rb.set_wheel_motors
    rb.set_wheel_motors = lambda l, r, **k: (sent.append((l, r)), orig(l, r))
    vida = vl.Vida(rb, seed=1, clock=lambda: 0.0, draw_eyes=False,
                   sense_fn=lambda r, b: {"tof_mm": None, "pos_mm": (0, 0), "heading_deg": 0,
                                          "on_charger": False, "cliff": False,
                                          "picked_up": False, "battery_v": 4.0})
    vida.hour_fn = lambda: 12
    t = [0.0]; vida._clock = lambda: t[0]; vida.motion._clock = lambda: t[0]
    vida.behavior._clock = lambda: t[0]
    for _ in range(100):
        vida.step(0.05); t[0] += 0.05
    deltas = [abs(b[0] - a[0]) for a, b in zip(sent, sent[1:])]
    assert deltas and max(deltas) < 30        # sin saltos bruscos entre ordenes

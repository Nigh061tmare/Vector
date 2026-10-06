"""Verifica la logica de escena/habituacion de fly_core SIN el connectome.

`flybrain` (paquete pesado con datos) no esta disponible en CI: se stubbea y se
construye FlyCore sin __init__, fijando solo el estado que usan scene_inject y
motor_command.  No valida la simulacion neuronal, solo la capa de decision.
"""
import os, sys, types
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vector-fly"))
_stub = types.ModuleType("flybrain")
for n in ("FlyBrain", "FeatureDetectors", "Trace", "blob_for"):
    setattr(_stub, n, object)
sys.modules.setdefault("flybrain", _stub)
import fly_core  # noqa: E402


class _Eyes:
    cells = {"chase": {"L": 1, "R": 2}, "threat": {"L": 3, "R": 4}}
    def inject(self, **k): return []


@pytest.fixture
def core(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(fly_core.time, "monotonic", lambda: t[0])
    c = object.__new__(fly_core.FlyCore)
    c.eyes = _Eyes(); c._prev_pano = None
    c._pano_from_senses = lambda **k: None
    c._vis_feats_from_pano = lambda *a, **k: None
    c._scene_until = 0.0; c._scene_near = 0.0; c._scene_side = 0.0
    c._scene_novel = False; c._scene_name = ""
    c._read_dec = None
    c._esc_prev = 0.0; c._esc_until = 0.0; c._esc_cooldown = 0.5; c._dec_steer = 0.0
    c.t = t
    return c


SNAP = {"forward": 0.0, "backward": 0.0}


def obj(novel, near=0.5, side=0.3):
    return [{"name": "gato", "side": side, "near": near, "novel": novel}]


def mode(c):
    return c.motor_command(dict(SNAP))["mode"]


def test_novel_object_is_approached_then_watched(core):
    core.scene_inject(obj(True))
    assert mode(core) == "approach"
    core.scene_inject(obj(False))
    m = core.motor_command(dict(SNAP))
    assert m["mode"] == "watch" and m["linear"] == 0.0


def test_no_flicker_when_vl_period_under_ttl(core):
    """Con ciclo VL de 5.5 s (< 7 s de habituacion) 'watch' es continuo."""
    core.scene_inject(obj(False))
    modes = []
    for step in range(60):                    # 60 s a 1 Hz
        if step % 5 == 0 and step:            # redeteccion cada ~5 s
            core.scene_inject(obj(False))
        modes.append(mode(core)); core.t[0] += 1.0
    assert set(modes) == {"watch"}


def test_gap_longer_than_ttl_drops_to_wander(core):
    """Documenta el limite: si la VL tarda > 7 s, 'watch' se cae."""
    core.scene_inject(obj(False))
    core.t[0] += 7.5
    assert mode(core) != "watch"


def test_turn_sign_towards_object(core):
    core.scene_inject(obj(True, side=0.8))
    m = core.motor_command(dict(SNAP))
    assert m["turn"] < 0 and m["reflex_side"] == "R"   # convencion del nucleo

import ast, os, types
SRC = open(os.path.join(os.path.dirname(__file__), "..", "vector_autonomo.py"), encoding="utf-8").read()

def _load():
    tree = ast.parse(SRC); keep = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in ("bat_volts", "bat_low"):
            keep.append(n)
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            t = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if getattr(t, "id", "") in ("_BAT_VALIDA_V", "_bat_cache", "V_BAT_BAJA"):
                keep.append(n)
    import time
    from typing import Any, Dict
    ns = {"time": time, "Any": Any, "Dict": Dict}
    exec(compile(ast.Module(keep, []), "bat", "exec"), ns); return types.SimpleNamespace(**ns)

class R:
    def __init__(self, v, boom=False): self.v, self.boom = v, boom
    def get_battery_state(self):
        if self.boom: raise RuntimeError("x")
        return types.SimpleNamespace(battery_volts=self.v)

def test_zero_volts_is_not_dead_battery():
    m = _load()
    assert m.bat_volts(R(4.0)) == 4.0
    assert m.bat_volts(R(0.0)) == 4.0          # cache
    assert m.bat_volts(R(0.0, boom=True)) == 4.0
    assert m.bat_low(R(0.0)) is False

def test_no_reading_ever_gives_zero_and_not_low():
    m = _load()
    assert m.bat_volts(R(0.0)) == 0.0 and m.bat_low(R(0.0)) is False

def test_real_low_battery_detected():
    assert _load().bat_low(R(3.5)) is True

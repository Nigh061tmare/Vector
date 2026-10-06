import os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_behavior as vb
from vector_behavior import Behavior, Inputs, Personality, State


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def adv(self, s): self.t += s


def make(seed=1, **pk):
    c = Clock()
    p = Personality(**{**dict(audacia=0.5, curiosidad=1.0, sociabilidad=1.0, energia=0.5,
                              sesgo_giro=0.0, hora_dormir=23, hora_despertar=7), **pk})
    b = Behavior(p, seed=seed, clock=c)
    return b, c


def run(b, c, x, secs, dt=0.1):
    d = None
    for _ in range(int(secs / dt)):
        d = b.update(x); c.adv(dt)
    return d


def to_explore(b, c, **kw):
    run(b, c, Inputs(hour=12, battery_v=4.0, **kw), 4)
    assert b.state == State.EXPLORAR


def test_wakes_up_then_explores():
    b, c = make()
    assert b.state == State.DESPERTAR
    to_explore(b, c)


def test_low_battery_goes_home_then_sleeps_at_night_on_charger():
    b, c = make(); to_explore(b, c)
    run(b, c, Inputs(hour=12, battery_v=3.5), 6)
    assert b.state == State.VUELTA_A_BASE
    run(b, c, Inputs(hour=12, battery_v=3.5, on_charger=True), 1)
    assert b.state in (State.DESPERTAR, State.DORMIR)


def test_battery_reading_failure_does_not_trigger_return():
    """0 V / None = lectura fallida: no debe mandarlo a cargar (bug real)."""
    for bad in (0.0, None, 1.0):
        b, c = make(); to_explore(b, c)
        run(b, c, Inputs(hour=12, battery_v=bad), 15)
        assert b.state == State.EXPLORAR, bad


def test_threat_makes_it_flee_immediately_and_calm_later():
    b, c = make(audacia=0.3); to_explore(b, c)
    d = b.update(Inputs(hour=12, battery_v=4.0, threat=0.95, object_side=0.5))
    assert b.state == State.HUIR and d.linear > 0.5
    run(b, c, Inputs(hour=12, battery_v=4.0, threat=0.0), 4)
    assert b.state != State.HUIR


def test_bold_personality_flees_less_than_timid():
    thr = 0.7
    res = {}
    for name, aud in (("timido", 0.1), ("audaz", 0.95)):
        b, c = make(audacia=aud); to_explore(b, c)
        b.update(Inputs(hour=12, battery_v=4.0, threat=thr))
        res[name] = b.state
    assert res["timido"] == State.HUIR and res["audaz"] != State.HUIR


def test_night_on_charger_sleeps_and_wakes_in_morning():
    b, c = make(); to_explore(b, c)
    run(b, c, Inputs(hour=2, battery_v=4.1, on_charger=True), 8)
    assert b.state == State.DORMIR
    d = run(b, c, Inputs(hour=3, battery_v=4.1, on_charger=True), 30)
    assert b.state == State.DORMIR and d.linear == 0
    run(b, c, Inputs(hour=9, battery_v=4.1, on_charger=True), 30)
    assert b.state != State.DORMIR


def test_novel_object_approached_and_played_with():
    b, c = make(); to_explore(b, c)
    run(b, c, Inputs(hour=12, battery_v=4.0, object_near=0.4, object_novel=True, object_side=0.5), 5)
    assert b.state == State.ACERCARSE
    d = b.update(Inputs(hour=12, battery_v=4.0, object_near=0.4, object_novel=True, object_side=0.5, tof_mm=900))
    assert d.linear > 0 and d.turn < 0        # gira hacia el objeto (convencion del nucleo)
    run(b, c, Inputs(hour=12, battery_v=4.0, object_near=0.9, object_novel=True), 4)
    assert b.state == State.JUGAR


def test_no_state_flicker_with_noisy_inputs():
    b, c = make(seed=3); to_explore(b, c)
    rng = random.Random(5); trans = 0; prev = b.state
    for _ in range(600):
        x = Inputs(hour=12, battery_v=4.0, tof_mm=rng.choice([None, 600, 800]),
                   object_near=rng.choice([None, 0.3]), object_novel=False,
                   threat=rng.random() * 0.2, noise=rng.random() * 0.5)
        b.update(x); c.adv(0.1)
        trans += b.state != prev; prev = b.state
    assert trans <= 8                        # 60 s: ningun parpadeo rapido


def test_safety_cliff_and_pickup_stop_motion():
    b, c = make(); to_explore(b, c)
    for kw in ({"cliff": True}, {"picked_up": True}):
        d = b.update(Inputs(hour=12, battery_v=4.0, **kw))
        assert d.linear == 0 and d.turn == 0


def test_obstacle_slows_turns_and_backs_off():
    b, c = make(); to_explore(b, c)
    far = b.update(Inputs(hour=12, battery_v=4.0, tof_mm=1000)).linear
    near = b.update(Inputs(hour=12, battery_v=4.0, tof_mm=250))
    close = b.update(Inputs(hour=12, battery_v=4.0, tof_mm=100))
    assert near.linear < far and abs(near.turn) >= 0.8
    assert close.linear < 0


def test_stuck_triggers_escape_maneuver_and_marks_heading_bad():
    b, c = make(); to_explore(b, c)
    x = Inputs(hour=12, battery_v=4.0, tof_mm=2000, pos_mm=(0.0, 0.0), heading_deg=90.0)
    seen_back = False
    for _ in range(80):                      # 8 s sin desplazarse
        d = b.update(x); c.adv(0.1)
        seen_back |= d.linear < 0
    assert seen_back
    assert b.stig.penalty(90.0) == 1.0
    assert any("atascado" in e["razon"] for e in b.timeline())


def test_moving_robot_is_not_stuck():
    b, c = make(); to_explore(b, c)
    for i in range(80):
        b.update(Inputs(hour=12, battery_v=4.0, tof_mm=2000, pos_mm=(i * 6.0, 0.0)))
        c.adv(0.1)
    assert not any("atascado" in e["razon"] for e in b.timeline())


def test_stigmergy_expiry_and_best_heading():
    c = Clock(); s = vb.Stigmergy(c)
    s.mark_bad(0.0, ttl=10); s.mark_safe(180.0)
    assert s.penalty(0) == 1.0 and s.penalty(180) == -0.5 and s.penalty(90) == 0.0
    assert s.best_heading(0.0) == 180          # prefiere la direccion segura
    c.adv(11)
    assert s.penalty(0) == 0.0


def test_personality_deterministic_and_varied():
    assert Personality.from_seed(7) == Personality.from_seed(7)
    assert len({Personality.from_seed(i) for i in range(20)}) == 20
    assert Personality(hora_dormir=23, hora_despertar=7).duerme_a(2)
    assert not Personality(hora_dormir=23, hora_despertar=7).duerme_a(12)
    assert Personality(hora_dormir=0, hora_despertar=7).duerme_a(3)


def test_timeline_in_natural_language():
    b, c = make(); to_explore(b, c)
    tl = b.timeline()
    assert tl and all(isinstance(e["razon"], str) and e["razon"] for e in tl)

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_supervisor as vs


def make(alive):
    t = [0.0]; launched = []; logs = []
    svc = vs.Service("fly", 4711, ["x"], startup_grace_s=20)
    sup = vs.Supervisor([svc], probe=lambda s: alive["v"], launcher=lambda s: launched.append(t[0]),
                        clock=lambda: t[0], log=logs.append)
    return sup, t, launched, logs


def adv(sup, t, n, step=30):
    r = None
    for _ in range(n):
        t[0] += step; r = sup.tick()
    return r


def test_healthy_service_is_left_alone():
    sup, t, launched, _ = make({"v": True})
    assert adv(sup, t, 10)["fly"] == "ok" and not launched


def test_single_blip_does_not_restart():
    alive = {"v": False}; sup, t, launched, _ = make(alive)
    sup.tick(); alive["v"] = True; sup.tick()
    assert not launched


def test_dead_service_restarted_after_two_failures_with_grace():
    sup, t, launched, _ = make({"v": False})
    assert sup.tick()["fly"] == "caido"
    t[0] += 30; assert sup.tick()["fly"] == "reiniciado" and len(launched) == 1
    t[0] += 10; assert sup.tick()["fly"] == "arrancando"       # periodo de gracia


def test_exponential_backoff_and_give_up():
    sup, t, launched, logs = make({"v": False})
    adv(sup, t, 400)                                            # 3.3 h de fallo continuo
    gaps = [b - a for a, b in zip(launched, launched[1:])]
    assert gaps[0] >= vs.BACKOFF_MIN_S and gaps[1] >= 2 * vs.BACKOFF_MIN_S - 30
    assert max(gaps) <= vs.BACKOFF_MAX_S + 60
    assert len(launched) <= vs.MAX_RESTARTS_PER_HOUR            # se rinde
    assert any("me rindo" in l for l in logs)
    assert sup.tick()["fly"] == "rendido"


def test_launcher_failure_is_survived():
    t = [0.0]
    def boom(s): raise OSError("no exe")
    sup = vs.Supervisor([vs.Service("a", 1, ["x"])], probe=lambda s: False, launcher=boom,
                        clock=lambda: t[0], log=lambda m: None)
    sup.tick(); t[0] += 30
    assert sup.tick()["a"] == "caido"


def test_probe_exception_counts_as_down():
    def bad(s): raise RuntimeError
    sup = vs.Supervisor([vs.Service("a", 1, ["x"])], probe=bad, launcher=lambda s: None,
                        clock=lambda: 0.0, log=lambda m: None)
    assert sup.tick()["a"] == "caido"

import math, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_gaze as vg
import vector_map as vm


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def adv(self, s): self.t += s


# ---------------- mirada ----------------
def test_gaze_has_fixations_and_saccades_within_limits():
    c = Clock(); g = vg.GazeController(c, random.Random(1))
    sacc_frames = fix_frames = 0; poss = []
    for _ in range(600):                      # 60 s a 10 Hz... pero sacadas duran 0.16
        t = g.update(); c.adv(0.05)
        poss.append(t.head_deg)
        sacc_frames += t.saccade; fix_frames += not t.saccade
        assert vg.HEAD_MIN_DEG <= t.head_deg <= vg.HEAD_MAX_DEG
    assert fix_frames > 4 * sacc_frames       # sobre todo fijaciones
    assert 15 < g.n_saccades < 120            # ni rigido ni frenetico
    assert max(poss) - min(poss) > 8          # realmente explora


def test_gaze_biased_to_interest():
    c = Clock(); g = vg.GazeController(c, random.Random(2), base_deg=10)
    xs = []
    for _ in range(1200):
        xs.append(g.update(interest=(35.0, 0.95)).head_deg); c.adv(0.05)
    assert sum(xs) / len(xs) > 24             # pasa la mayor parte mirando al objeto


def test_arousal_shortens_fixations():
    def n(ar):
        c = Clock(); g = vg.GazeController(c, random.Random(3))
        for _ in range(1200): g.update(arousal=ar); c.adv(0.05)
        return g.n_saccades
    assert n(1.0) > n(0.0)


def test_head_motor_p_controller_sign_and_clamp():
    assert vg.head_motor_speed(0.0, 30) > 0
    assert vg.head_motor_speed(math.radians(40), 0) < 0
    assert abs(vg.head_motor_speed(0.0, 999)) <= vg.HEAD_MAX_RADPS
    assert abs(vg.head_motor_speed(0.0, 0.0)) < 1e-9


# ---------------- ojos ----------------
def settle(name, secs=2.0):
    c = Clock(); e = vg.EyeState(c, random.Random(0)); e.set(name)
    out = None
    for _ in range(int(secs / 0.02)):
        c.adv(0.02); out = e.update()
    return out


def test_pupil_orders_fear_lt_calm_lt_surprise():
    assert settle("asustado")["pupil"] < settle("tranquilo")["pupil"] < settle("sorpresa")["pupil"]


def test_surprise_dilates_faster_than_calm_change():
    def after(name, secs):
        c = Clock(); e = vg.EyeState(c, random.Random(0)); e.set(name)
        for _ in range(int(secs / 0.01)): c.adv(0.01); r = e.update()
        return r["pupil"]
    start = vg.EYE_STYLES["tranquilo"].pupil
    prog_s = (after("sorpresa", 0.15) - start) / (vg.EYE_STYLES["sorpresa"].pupil - start)
    prog_c = (after("curioso", 0.15) - start) / (vg.EYE_STYLES["curioso"].pupil - start)
    assert prog_s > prog_c


def test_blinks_happen_but_not_while_asleep():
    def blinks(name):
        c = Clock(); e = vg.EyeState(c, random.Random(4)); e.set(name); n = 0; prev = False
        for _ in range(int(60 / 0.02)):
            c.adv(0.02); cur = e.update()["lid"] >= 1.0
            n += cur and not prev; prev = cur
        return n
    assert blinks("tranquilo") >= 8 and blinks("sueno") == 0


def test_render_size_and_pupil_changes_pixels():
    a = vg.render_eyes(0.16, 0.0, 0.5, 0.9); b = vg.render_eyes(0.6, 0.0, 0.5, 0.9)
    assert a.size == (vg.SCREEN_W, vg.SCREEN_H)
    import numpy as np
    dark = lambda im: int((np.asarray(im).sum(axis=2) == 0).sum())
    assert dark(b) > dark(a) and a.tobytes() != b.tobytes()
    closed = vg.render_eyes(0.4, 1.0, 0.5, 0.9)
    assert dark(closed) > dark(a)


def test_show_eyes_tolerates_missing_sdk():
    class R: pass
    assert vg.show_eyes(R(), {"pupil": .3, "lid": 0, "hue": .5, "sat": 1}) is False


# ---------------- mapa ----------------
def test_ray_marks_free_then_occupied():
    g = vm.OccupancyGrid(); pose = (0.0, 0.0, 0.0)
    for _ in range(4): g.update_tof(pose, 500.0)
    assert g.state(250, 0) == "free" and g.state(500, 0) == "occupied"
    assert g.state(0, 600) == "unknown"


def test_no_hit_beyond_range_only_frees():
    g = vm.OccupancyGrid()
    for _ in range(4): g.update_tof((0, 0, 0), None)
    assert g.state(600, 0) == "free" and g.state(1190, 0) != "occupied"


def test_heading_rotation_and_bearing():
    g = vm.OccupancyGrid()
    for _ in range(4): g.update_tof((0, 0, 90.0), 400.0)       # mira hacia +y
    assert g.state(0, 400) == "occupied" and g.state(400, 0) == "unknown"
    assert abs(g.bearing_to((0, 0, 0), (0, 100)) - 90) < 1e-6   # a la izquierda
    assert g.bearing_to((0, 0, 0), (0, -100)) < 0


def test_safe_zone_excludes_obstacle_neighbours():
    g = vm.OccupancyGrid()
    for _ in range(4): g.update_tof((0, 0, 0), 500.0)
    assert g.is_safe(150, 0) and not g.is_safe(500, 0) and not g.is_safe(450, 0)
    assert not g.is_safe(1e6, 0)


def test_frontier_found_next_to_unknown():
    g = vm.OccupancyGrid()
    for _ in range(4): g.update_tof((0, 0, 0), 800.0)
    f = g.unexplored_frontier((0, 0, 0))
    assert f is not None and g.state(*f) == "free"


def test_occupancy_is_bounded_and_forgets_dynamic_obstacle():
    g = vm.OccupancyGrid()
    for _ in range(50): g.update_tof((0, 0, 0), 500.0)
    assert g.l[g.to_cell(500, 0)[0]][g.to_cell(500, 0)[1]] <= vm.L_MAX
    for _ in range(12): g.update_tof((0, 0, 0), None)           # el obstaculo se fue
    assert g.state(500, 0) != "occupied"


def test_ascii_has_robot_and_home():
    g = vm.OccupancyGrid(); g.set_home(0, 0)
    s = g.to_ascii((300.0, 0.0, 0.0))
    assert "R" in s and "H" in s


def test_object_memory_merge_motion_and_forgetting():
    c = Clock(); m = vm.ObjectMemory(c)
    o = m.observe("gato", 1000, 0); c.adv(5)
    m.observe("gato", 1020, 10)
    assert len(m.objects) == 1 and not m.objects[0].moved
    c.adv(5); m.observe("gato", 1250, 0)
    assert m.objects[0].moved
    m.observe("lampara", -500, 500)
    assert m.where("gato") is not None and len(m.summary()) == 2
    c.adv(vm.OBJ_TTL_S + 1)
    assert m.where("gato") is None


def test_projection_uses_tof_when_centered_and_size_otherwise():
    x, y = vm.ObjectMemory.project((0, 0, 0), 0.0, 0.5, tof_mm=600)
    assert abs(x - 600) < 1 and abs(y) < 1
    x2, _ = vm.ObjectMemory.project((0, 0, 0), 0.0, 1.0)
    x3, _ = vm.ObjectMemory.project((0, 0, 0), 0.0, 0.1)
    assert x2 < x3                                  # mas grande = mas cerca
    _, yr = vm.ObjectMemory.project((0, 0, 0), 0.8, 0.5)
    assert yr < 0                                   # derecha => y negativo

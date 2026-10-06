import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vector_motion import (ACCEL_MMPS2, DECEL_MMPS2, MAX_WHEEL_MMPS, MotionController,
                           WheelSmoother, diff_drive, slew)


class FakeMotors:
    def __init__(self): self.calls = []
    def set_wheel_motors(self, l, r, left_wheel_accel=0.0, right_wheel_accel=0.0):
        self.calls.append((l, r, left_wheel_accel))

class FakeRobot:
    def __init__(self): self.motors = FakeMotors()


def test_diff_drive_straight_and_convention():
    assert diff_drive(1, 0) == (MAX_WHEEL_MMPS, MAX_WHEEL_MMPS)
    l, r = diff_drive(0.5, 0.5)
    assert r > l  # turn>0 => derecha mas rapida (convencion del nucleo)


def test_diff_drive_saturation_keeps_arc_ratio():
    l, r = diff_drive(1.0, 0.5)          # 0.6 y 1.4 sin normalizar
    assert max(abs(l), abs(r)) <= MAX_WHEEL_MMPS + 1e-9
    assert abs(l / r - 0.6 / 1.4) < 1e-9


def test_slew_limits_accel_and_brakes_harder():
    assert slew(0, 100, 0.1) == ACCEL_MMPS2 * 0.1
    assert slew(100, 0, 0.1) == 100 - DECEL_MMPS2 * 0.1
    assert slew(10, 12, 1.0) == 12


def test_smoother_never_jumps_and_converges():
    s = WheelSmoother(); prev = 0.0
    for _ in range(100):
        l, _ = s.step(100, 100, 0.05)
        assert l - prev <= ACCEL_MMPS2 * 0.05 + 1e-9
        prev = l
    assert prev == 100


def test_deadman_decelerates_to_zero():
    t = [0.0]; rb = FakeRobot()
    mc = MotionController(rb, clock=lambda: t[0])
    mc.set_target(1.0, 0.0, ttl=0.3)
    for _ in range(10):
        t[0] += 0.05; mc.tick(0.05)
    assert mc.speeds[0] > 0
    for _ in range(60):
        t[0] += 0.05; mc.tick(0.05)
    assert mc.speeds == (0.0, 0.0)
    assert rb.motors.calls[-1][:2] == (0.0, 0.0)


def test_guard_forces_immediate_stop():
    rb = FakeRobot(); ok = [True]
    mc = MotionController(rb, guard=lambda: ok[0])
    mc.set_target(1.0, 0.0, ttl=5)
    for _ in range(10): mc.tick(0.05)
    ok[0] = False
    assert mc.tick(0.05) == (0.0, 0.0) and mc.speeds == (0.0, 0.0)


def test_sdk_errors_are_swallowed():
    class Boom:
        class motors:
            @staticmethod
            def set_wheel_motors(*a, **k): raise RuntimeError("grpc")
    mc = MotionController(Boom()); mc.set_target(1, 0)
    mc.tick(0.05); assert mc.errors == 1


# ---------------- avance continuo ----------------
from vector_motion import drive_distance


class SimClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


def _drive(tof_fn, total=300.0, speed=36.0, unsafe=lambda: False):
    c = SimClock(); rb = FakeRobot()
    mc = MotionController(rb, clock=c)
    res = drive_distance(mc, total, speed, tof_fn, unsafe, sleep=c.sleep, clock=c)
    return res, rb, c


def test_drive_distance_clear_path_covers_distance_with_smooth_ramp():
    (mm, why), rb, c = _drive(lambda: 1000.0)
    assert why == "ok" and 250 <= mm <= 360
    speeds = [abs(x[0]) for x in rb.motors.calls]
    assert max(speeds) <= 36.0 + 1e-6
    steps = [abs(b - a) for a, b in zip(speeds, speeds[1:])]
    assert max(steps) < 40                      # sin saltos bruscos
    assert rb.motors.calls[-1][:2] == (0.0, 0.0)


def test_drive_distance_stops_for_obstacle_with_soft_braking():
    seq = iter([1000.0] * 5 + [250.0] * 100)
    (mm, why), rb, c = _drive(lambda: next(seq))
    assert why == "obstaculo" and mm < 100
    assert rb.motors.calls[-1][:2] == (0.0, 0.0)


def test_drive_distance_danger_is_immediate_stop():
    (mm, why), rb, _ = _drive(lambda: 100.0)
    assert why == "peligro" and mm == 0.0 and rb.motors.calls[-1][:2] == (0.0, 0.0)


def test_drive_distance_no_reading_and_unsafe():
    assert _drive(lambda: None)[0][1] == "sin_lectura"
    assert _drive(lambda: 1000.0, unsafe=lambda: True)[0][1] == "inseguro"


def test_drive_distance_slows_in_comfort_zone():
    (mm, why), rb, _ = _drive(lambda: 400.0, total=100.0, speed=48.0)
    assert why == "ok" and max(abs(x[0]) for x in rb.motors.calls) <= 26.0 + 1e-6

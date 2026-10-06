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

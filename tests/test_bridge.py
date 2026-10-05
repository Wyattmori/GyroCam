"""
Offline tests for pc-bridge/gyrocam_bridge.py. No phone, game or ViGEmBus needed:
a fake vgamepad module stands in for the virtual controller, a simulated "game" integrates
its right stick (radial deadzone, linear response), and the clock is simulated.

    python tests/test_bridge.py
"""
import json
import math
import os
import struct
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pc-bridge"))


# --------------------------------------------------------------------------- fakes
class FakePad:
    def __init__(self):
        self.rx = self.ry = 0.0
        self.btn = set()
        self.log = []  # buttons held at each update()

    def reset(self): self.btn = set()
    def press_button(self, button): self.btn.add(button)
    def right_trigger_float(self, value_float): pass
    def left_trigger_float(self, value_float): pass
    def left_joystick_float(self, x_value_float, y_value_float): pass
    def right_joystick_float(self, x_value_float, y_value_float): self.rx, self.ry = x_value_float, y_value_float
    def update(self): self.log.append(frozenset(self.btn))


_BUTTONS = ["A", "B", "X", "Y", "DPAD_UP", "DPAD_DOWN", "LEFT_THUMB", "RIGHT_SHOULDER", "LEFT_SHOULDER"]
sys.modules["vgamepad"] = types.SimpleNamespace(
    VX360Gamepad=FakePad,
    XUSB_BUTTON=types.SimpleNamespace(**{"XUSB_GAMEPAD_" + b: b for b in _BUTTONS}))

import gyrocam_bridge as gb  # noqa: E402

CLOCK = [0.0]
gb.time.perf_counter = lambda: CLOCK[0]
gb.reskate_console = lambda cmd, vk: None
DT = 0.008  # the app sends ~120 packets/s


# --------------------------------------------------------------------------- phone poses
def mat_to_quat(m):
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1) * 2
        return ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    i = max(range(3), key=lambda k: m[k][k])
    if i == 0:
        s = math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2
        return (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    if i == 1:
        s = math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2
        return ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    s = math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2
    return ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def axis_angle(ax, deg):
    h = math.radians(deg) / 2
    return (ax[0] * math.sin(h), ax[1] * math.sin(h), ax[2] * math.sin(h), math.cos(h))


def basis(x, y, z):  # device axes expressed in world coordinates
    return mat_to_quat([[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]])


# Back camera facing north, level. World: X east, Y north, Z up.
LANDSCAPE = basis((0, 0, 1), (-1, 0, 0), (0, -1, 0))  # Surface.ROTATION_90
PORTRAIT = basis((1, 0, 0), (0, 0, 1), (0, -1, 0))    # Surface.ROTATION_0


def pose(base, yaw, pitch, roll=0.0):
    q = qmul(axis_angle((0, -1, 0), roll), base)
    q = qmul(axis_angle((1, 0, 0), pitch), q)
    return qmul(axis_angle((0, 0, -1), yaw), q)


# --------------------------------------------------------------------------- harness
class Rig:
    """A bridge, a simulated phone and (in gamepad mode) a simulated game camera."""

    def __init__(self, mode="gamepad", game_deadzone=0.27, **overrides):
        cfg = json.loads(json.dumps(gb.DEFAULT_CONFIG))
        cfg.update(mode=mode, port=0, target_processes=[])
        for section, values in overrides.items():
            cfg[section].update(values)
        self.cfg = cfg
        self.mouse = []
        if mode == "mouse":
            gb.mouse_move = lambda dx, dy: self.mouse.append((dx, dy))
            gb.mouse_right = lambda down: None
            gb.key = lambda scan, down: None
        self.bridge = gb.Bridge(cfg)
        self.bridge.sock.close()
        self.bridge.sock = types.SimpleNamespace(sendto=lambda *a: None)
        self.pad = getattr(self.bridge.out, "pad", None)
        self.dz = game_deadzone
        self.cam = [0.0, 0.0]
        self.seq = 0

    def step(self, yaw, pitch=0.0, base=LANDSCAPE, rotation=1, tracking=True, recenter=0, home=0,
             sens=1.0, look=(0.0, 0.0), play=False, fov=0.0, fov_sync=0, n=1):
        for _ in range(n):
            if self.pad:  # the game turns at the stick held since the last packet
                m = math.hypot(self.pad.rx, self.pad.ry)
                if m > self.dz:
                    full = self.cfg["gamepad"]["full_stick_degrees_per_second"]
                    speed = (min(m, 1) - self.dz) / (1 - self.dz) * full
                    self.cam[0] += speed * self.pad.rx / m * DT
                    self.cam[1] += speed * self.pad.ry / m * DT
            flags = (1 if tracking else 0) | (4 if play else 0)
            pkt = bytearray(struct.pack(gb.PHONE_FMT, b"GYC1", self.seq & 0xFFFF, flags, recenter, rotation, home,
                                        *pose(base, yaw, pitch), 0.0, 0.0, 0.0, 0)
                            + struct.pack("<5f", 0.0, sens, look[0], look[1], fov))
            pkt[10] = fov_sync
            self.seq += 1
            CLOCK[0] += DT
            self.bridge.handle(bytes(pkt), ("127.0.0.1", 1))

    def mouse_degrees(self):
        m = self.cfg["mouse"]
        return (sum(d[0] for d in self.mouse) / m["counts_per_degree_x"],
                -sum(d[1] for d in self.mouse) / m["counts_per_degree_y"])

    def taps(self, button):
        log = [frozenset()] + self.pad.log
        return sum(1 for a, b in zip(log, log[1:]) if button in b and button not in a)


def ramp(rig, y0, y1, seconds, **kw):
    n = int(seconds / DT)
    for i in range(n):
        rig.step(y0 + (y1 - y0) * i / (n - 1), **kw)


def close(a, b, tol, what):
    assert abs(a - b) <= tol, f"{what}: got {a:.2f}, expected {b:.2f} +- {tol}"


# --------------------------------------------------------------------------- tests
def test_orientation_math():
    for base, rot in ((LANDSCAPE, 1), (PORTRAIT, 0)):
        for yaw, pitch in ((0, 0), (30, 0), (-45, 10), (170, -20)):
            y, p, _ = gb.camera_angles(pose(base, yaw, pitch), rot)
            close(gb.wrap180(y - yaw), 0, 1e-6, "yaw")
            close(p, pitch, 1e-6, "pitch")
    _, _, r = gb.camera_angles(pose(LANDSCAPE, 0, 0, 15), 1)
    close(abs(r), 15, 1e-6, "roll")


def test_mouse_absolute_tracking_clutch_and_wrap():
    rig = Rig(mode="mouse")
    rig.step(77, n=50)
    ramp(rig, 77, 167, 1.6)
    rig.step(167, n=200)
    close(rig.mouse_degrees()[0], 90, 0.5, "90 degree pan")
    # Pause, move the phone back, resume: the camera must not jump.
    before = rig.mouse_degrees()[0]
    ramp(rig, 167, 107, 0.2, tracking=False)
    rig.step(107, n=100)
    close(rig.mouse_degrees()[0], before, 0.2, "clutch")
    # Crossing +-180 on the phone.
    before = rig.mouse_degrees()[0]
    ramp(rig, 107, 237, 1.6)
    rig.step(237, n=150)
    close(rig.mouse_degrees()[0] - before, 130, 0.5, "yaw wrap")


def test_slow_pan_never_stalls():
    """A 6 deg/s handheld pan must move the camera continuously (the old 0.20 dead spot)."""
    rig = Rig()
    rig.step(0, n=50)
    history = []
    for i in range(int(3 / DT)):
        rig.step(6 * i * DT)
        history.append(rig.cam[0])
    stall = longest = 0
    for a, b in zip(history[10:], history[11:]):
        stall = stall + 1 if b == a else 0
        longest = max(longest, stall)
    assert longest * DT < 0.1, f"camera stalled for {longest * DT:.2f} s"
    rig.step(18, n=100)
    close(rig.cam[0], 18, 0.5, "slow pan end")


def test_camera_weight_eases_in():
    rig = Rig()
    rig.step(0, n=50)
    rig.step(30, n=6)
    assert rig.cam[0] < 8, "camera should ease into a sudden move, not jump"
    rig.step(30, n=200)
    close(rig.cam[0], 30, 0.6, "settles on target")


def test_sensitivity_and_look_stick():
    rig = Rig()
    rig.step(0, n=30)
    ramp(rig, 0, 40, 1.2)
    rig.step(40, n=150)
    close(rig.cam[0], 40, 1.5, "1x")
    ramp(rig, 40, 60, 1.2, sens=2.0)
    rig.step(60, n=150, sens=2.0)
    close(rig.cam[0], 80, 1.5, "2x sensitivity")
    rig.step(60, n=int(1 / DT), sens=2.0, look=(1.0, 0.0))
    rig.step(60, n=150, sens=2.0)
    close(rig.cam[0], 80 + rig.cfg["look_stick_degrees_per_second"], 3, "LOOK stick")
    rig.step(60, n=int(0.5 / DT), sens=2.0, look=(0.0, -1.0), tracking=False)
    rig.step(60, n=150, sens=2.0, tracking=False)
    close(rig.cam[1], -60, 3, "LOOK stick while paused")


def test_recenter_and_home():
    rig = Rig()
    rig.step(0, n=30)
    ramp(rig, 0, 120, 1.5, pitch=0)
    rig.step(120, pitch=30, n=200)
    rig.step(-100, pitch=-20, n=300, recenter=1, tracking=False)  # recenter while paused
    close(rig.cam[0], 0, 1.5, "recenter yaw")
    close(rig.cam[1], 0, 1.5, "recenter pitch")
    rig.step(-100, pitch=-20, n=10, recenter=1)                   # resume: this pose = forward
    ramp(rig, -100, -80, 1.0, pitch=-20, recenter=1)
    rig.step(-80, pitch=-20, n=150, recenter=1)
    close(rig.cam[0], 20, 1.5, "phone pose became forward")
    rig.step(-80, pitch=-20, n=5, recenter=1, home=1)              # set home at 20
    ramp(rig, -80, -50, 1.0, pitch=-20, recenter=1, home=1)
    rig.step(-50, pitch=-20, n=150, recenter=1, home=1)
    close(rig.cam[0], 50, 1.5, "pan after set home")
    rig.step(90, pitch=0, n=300, recenter=2, home=1)
    close(rig.cam[0], 20, 1.5, "recenter returns to new home")


def test_play_button_holds_a():
    rig = Rig()
    rig.step(0, n=5, play=True)
    assert all("A" in b for b in rig.pad.log[-3:])
    rig.step(0, n=3)
    assert "A" not in rig.pad.log[-1]


def test_fov_slider():
    rig = Rig()
    rig.step(0, n=100, fov=70)
    assert rig.taps("DPAD_UP") + rig.taps("DPAD_DOWN") == 0, "pressed before the slider moved"
    rig.pad.log.clear()
    rig.step(0, n=int(3 / DT), fov=85)  # unknown FOV: 15 downs to 50, then up to 85
    assert (rig.taps("DPAD_DOWN"), rig.taps("DPAD_UP")) == (15, 7)
    assert rig.bridge.fov_known == 85
    rig.pad.log.clear()
    rig.step(0, n=int(1 / DT), fov=60)
    assert (rig.taps("DPAD_DOWN"), rig.taps("DPAD_UP")) == (5, 0)
    rig.pad.log.clear()
    rig.step(0, n=int(3 / DT), fov=60, fov_sync=1)
    assert (rig.taps("DPAD_DOWN"), rig.taps("DPAD_UP")) == (15, 2)


def test_old_packets_still_work():
    rig = Rig()
    pkt = struct.pack(gb.PHONE_FMT, b"GYC1", 1, 1, 0, 1, 0, *pose(LANDSCAPE, 0, 0), 0.0, 0.0, 0.0, 0)
    assert len(pkt) == 44
    rig.bridge.handle(pkt, ("127.0.0.1", 1))


def test_config_migration_from_v1():
    old_path = gb.CONFIG_PATH
    gb.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
    try:
        with open(gb.CONFIG_PATH, "w") as f:
            json.dump({"mode": "mouse",
                       "gamepad": {"lift_up_button": "RIGHT_SHOULDER", "anti_deadzone": 0.2,
                                   "full_stick_degrees_per_second": 150},
                       "zoom": {"method": "freecam_fov", "gamepad_input": "LEFT_Y", "fov_min": 40},
                       "smoothing": {"min_cutoff_hz": 1.2, "beta": 0.02}}, f)
        c = gb.load_config()
        assert c["mode"] == "gamepad"
        assert c["gamepad"]["lift_up_button"] == "RT"
        assert c["gamepad"]["anti_deadzone"] == 0.27
        assert c["gamepad"]["full_stick_degrees_per_second"] == 150  # user calibration kept
        assert c["zoom"]["method"] == "gamepad" and c["zoom"]["gamepad_input"] == "DPAD"
        assert c["zoom"]["fov_min"] == 50
        assert c["config_version"] == gb.CONFIG_VERSION
    finally:
        gb.CONFIG_PATH = old_path


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{'all passed' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)

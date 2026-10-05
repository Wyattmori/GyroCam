"""
GyroCam bridge for skate. (ReSkate) -- turns your phone into a handheld camera.

The Android app streams its orientation over Wi-Fi (UDP). This bridge converts it
into camera input the game already understands:

  mode "gamepad" -> (default) a virtual Xbox 360 controller (needs ViGEmBus +
                    `pip install vgamepad`). Right stick = look, left stick = move,
                    RT / LT = rise / fall, L3 = boost. Matches ReSkate Freecam's controller
                    layout and works in the replay editor, where the game's mouse cursor
                    would otherwise swallow mouse input.
  mode "mouse"   -> relative mouse movement with the right mouse button held, plus
                    WASD/QE keys. Only works in ReSkate Freecam with the ReSkate menu closed.

Both modes are calibrated with `c` and `+`/`-` so that turning the phone 30 degrees
turns the camera about 30 degrees.

Console keys (while this window is focused):
  r  recenter (camera glides back to home)   h  set home to the current view
  d  find the game's stick deadzone (press F8 when the camera starts moving)
  c  calibration turn (360 degrees after 3 s)
  +  / -  turn more / less per phone degree       m  switch gamepad/mouse    q  quit
"""

import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import socket
import struct
import sys
import threading
import time

try:
    import msvcrt
except ImportError:  # not Windows
    msvcrt = None

VERSION = "1.3.1"
FROZEN = getattr(sys, "frozen", False)  # running as GyroCam-bridge.exe (PyInstaller)
# Keep config.json next to the .exe, not in PyInstaller's temporary unpack folder.
HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
VIGEMBUS_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"

CONFIG_VERSION = 4

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "port": 47823,
    "discovery_port": 47824,
    "mode": "gamepad",
    # Only send input while one of these executables is the foreground window.
    # Empty list = always send (careful: it will move your desktop mouse too).
    "target_processes": ["Skate.exe"],
    "mouse": {
        # Mouse counts per degree of camera rotation. Calibrate with the `c` key.
        "counts_per_degree_x": 12.0,
        "counts_per_degree_y": 12.0,
        "invert_y": False,
        # ReSkate Freecam only looks while RMB is held. The bridge holds it while tracking.
        "hold_right_mouse": True,
        # Stops the emitted pitch from running past the game's own clamp and desyncing.
        "pitch_limit_degrees": 85.0,
    },
    "gamepad": {
        # How fast (deg/s) the game turns the camera at full right stick. Calibrate with `c`.
        "full_stick_degrees_per_second": 180.0,
        # The game ignores stick values below its deadzone (Xbox standard for the right stick
        # is ~0.27). This is added to every non-zero value so slow pans still register.
        # Measure yours with the `d` key.
        "anti_deadzone": 0.27,
        # "radial" (most games: deadzone on the stick's total tilt) or "axial" (per axis).
        "deadzone_shape": "radial",
        # If the game turns at stick^k, set k here and the bridge pre-compensates. 1 = linear.
        "stick_curve_exponent": 1.0,
        # How quickly the stick closes the gap between phone and camera. Lower = snappier,
        # higher = smoother.
        "response_seconds": 0.04,
        "invert_y": False,
        # Any XUSB button name (A, B, X, Y, LEFT_SHOULDER, ...) or RT / LT for the triggers.
        "lift_up_button": "RT",
        "lift_down_button": "LT",
        "boost_button": "LEFT_THUMB",
        # The phone's PLAY button (resume the replay).
        "play_button": "A",
    },
    "zoom": {
        # The phone's FOV slider sets an absolute target FOV.
        # "gamepad":     (default) taps gamepad_input until the game's FOV reaches the slider.
        #                DPAD = the replay editor's FOV setting (D-pad up / down, fov_step per
        #                tap). The first time, it taps all the way down to fov_min to learn where
        #                the game is; the phone's SYNC button repeats that.
        # "freecam_fov": ReSkate Freecam. On release the bridge types `freecamfov <deg>` into
        #                ReSkate's console (brief flash).
        # "mouse_wheel": scroll the mouse wheel.   "off": ignore the buttons.
        "method": "gamepad",
        # DPAD (FOV+ = up), SHOULDERS (FOV+ = RB), LEFT_Y or RIGHT_Y (FOV+ = stick up).
        "gamepad_input": "DPAD",
        "repeat_delay_seconds": 0.45,  # old FOV -/+ buttons: first repeat after this
        "repeat_seconds": 0.2,         # ...then one step this often
        # Slider taps: how long each D-pad press is held, and the gap between presses.
        "tap_press_seconds": 0.04,
        "tap_gap_seconds": 0.04,
        "fov_step": 5,
        "fov_start": 70, "fov_min": 50, "fov_max": 120,
        "degrees_per_second": 30.0,
        "console_key_vk": 192,  # ReSkate's console key (` / ~), see ReSkateLauncher.settings.json
        "wheel_clicks_per_second": 10.0,
        "invert": False,
    },
    # The phone's LOOK stick: camera turn speed (deg/s) at full deflection.
    "look_stick_degrees_per_second": 120.0,
    "keys": {
        # DirectInput scan codes (set 1). W A S D Q E LShift.
        "forward": 0x11, "left": 0x1E, "back": 0x1F, "right": 0x20,
        "down": 0x10, "up": 0x12, "boost": 0x2A,
        "threshold": 0.35,
    },
    "smoothing": {
        # One Euro filter. Lower min_cutoff = smoother/laggier at rest,
        # higher beta = less lag on fast swings.
        "min_cutoff_hz": 2.0,
        "beta": 0.05,
        # Gives the camera a little weight so moves ease in and out like a real handheld
        # camera instead of starting and stopping dead. 0 = off, 0.1-0.3 feels natural.
        "camera_weight_seconds": 0.15,
    },
    # Release everything if the phone goes quiet for this long.
    "timeout_seconds": 0.5,
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if user.get("config_version", 1) < 2:
            # v1 defaulted to mouse mode and bumpers for lift; ReSkate Freecam uses triggers.
            user.pop("mode", None)
            g = user.get("gamepad", {})
            if g.get("lift_up_button") == "RIGHT_SHOULDER":
                g.pop("lift_up_button")
            if g.get("lift_down_button") == "LEFT_SHOULDER":
                g.pop("lift_down_button")
        if user.get("config_version", 1) < 3:
            # v3: zoom drives the replay editor's FOV via the D-pad; less smoothing lag.
            z = user.get("zoom", {})
            if z.get("method") == "freecam_fov":
                z.pop("method")
            if z.get("gamepad_input") == "LEFT_Y":
                z.pop("gamepad_input")
            sm = user.get("smoothing", {})
            if sm.get("min_cutoff_hz") == 1.2 and sm.get("beta") == 0.02:
                user.pop("smoothing")
            if user.get("gamepad", {}).get("response_seconds") == 0.06:
                user["gamepad"].pop("response_seconds")
        if user.get("config_version", 1) < 4:
            # v4: anti-deadzone matched to the Xbox right-stick deadzone; replay editor FOV range.
            g = user.get("gamepad", {})
            if g.get("anti_deadzone") == 0.2:
                g.pop("anti_deadzone")
            z = user.get("zoom", {})
            if z.get("fov_min") == 40:
                z.pop("fov_min")
        user["config_version"] = CONFIG_VERSION
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Protocol (little endian). See README.md.
# ---------------------------------------------------------------------------
PHONE_FMT = "<4sHBBBB2x4f3fI"  # magic seq flags recenter rotation home pad quat(x,y,z,w) moveX moveY lift time_ms
PHONE_SIZE = struct.calcsize(PHONE_FMT)  # 44; app 1.1+ appends f32 zoom (48 bytes)
PHONE_MAGIC = b"GYC1"
FLAG_TRACKING = 1
FLAG_BOOST = 2
FLAG_PLAY = 4
ACK_MAGIC = b"GYCA"
BEACON_MAGIC = b"GYCB"


# ---------------------------------------------------------------------------
# Win32 input injection
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def mouse_move(dx, dy):
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)
    _send(i)


def mouse_right(down):
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP, 0, 0)
    _send(i)


def key(scan, down):
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP), 0, 0)
    _send(i)


KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
VK_CONTROL, VK_RETURN, VK_A = 0x11, 0x0D, 0x41


def key_vk(vk, down):
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), 0 if down else KEYEVENTF_KEYUP, 0, 0)
    _send(i)


def tap_vk(vk):
    key_vk(vk, True)
    key_vk(vk, False)


def type_text(text):
    for ch in text:
        down = INPUT(type=INPUT_KEYBOARD)
        down.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, 0)
        up = INPUT(type=INPUT_KEYBOARD)
        up.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
        _send(down, up)


def mouse_wheel(clicks):
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(0, 0, ctypes.c_uint32(clicks * 120).value, MOUSEEVENTF_WHEEL, 0, 0)
    _send(i)


def reskate_console(command, console_vk):
    """Open ReSkate's console, run one command, close it again (~0.3 s)."""
    tap_vk(console_vk)
    time.sleep(0.15)
    key_vk(VK_CONTROL, True)  # select-all so a stray ` from the toggle gets replaced
    tap_vk(VK_A)
    key_vk(VK_CONTROL, False)
    type_text(command)
    tap_vk(VK_RETURN)
    time.sleep(0.1)
    tap_vk(console_vk)


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


def cursor_visible():
    """True when Windows is drawing the cursor, i.e. the game's UI owns the mouse."""
    ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    return bool(user32.GetCursorInfo(ctypes.byref(ci))) and bool(ci.flags & 1)


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def foreground_exe():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


# ---------------------------------------------------------------------------
# Orientation math
# ---------------------------------------------------------------------------
def quat_rotate(q, v):
    """Rotate vector v by unit quaternion q=(x,y,z,w)."""
    x, y, z, w = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


# Device-frame "screen up" for each Android display rotation (Surface.ROTATION_*).
SCREEN_UP = {0: (0, 1, 0), 1: (1, 0, 0), 2: (0, -1, 0), 3: (-1, 0, 0)}


def camera_angles(q, rotation):
    """Yaw/pitch/roll in degrees of the phone's back camera, world Z up.

    Android rotation vectors map device coordinates to world coordinates. The back
    camera looks along device -Z; which device axis is "up" depends on how the phone
    is held, so the app sends its display rotation.
    """
    fwd = quat_rotate(q, (0, 0, -1))
    up = quat_rotate(q, SCREEN_UP.get(rotation, (1, 0, 0)))
    yaw = math.degrees(math.atan2(fwd[0], fwd[1]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd[2]))))
    right = (fwd[1] * up[2] - fwd[2] * up[1],
             fwd[2] * up[0] - fwd[0] * up[2],
             fwd[0] * up[1] - fwd[1] * up[0])
    roll = math.degrees(math.atan2(-right[2], up[2]))
    return yaw, pitch, roll


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


class OneEuro:
    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None
        self.dx = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self, value):
        self.x, self.dx = value, 0.0

    def __call__(self, value, dt):
        if self.x is None or dt <= 0:
            self.reset(value)
            return value
        dx = (value - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx += a_d * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, dt)
        self.x += a * (value - self.x)
        return self.x


# ---------------------------------------------------------------------------
# Output backends
# ---------------------------------------------------------------------------
class MouseOutput:
    name = "mouse"

    def __init__(self, cfg):
        self.cfg = cfg
        self.rmb = False
        self.keys_down = set()
        self.extra_buttons = set()  # gamepad-only (PLAY, D-pad zoom); ignored here

    def look(self, d_yaw, d_pitch, dt):
        m = self.cfg["mouse"]
        if m["hold_right_mouse"] and not self.rmb:
            mouse_right(True)
            self.rmb = True
        sign_y = -1 if m["invert_y"] else 1
        # Only whole counts are sent; the caller tracks what was sent, so the
        # leftover fraction is naturally part of the next frame's delta.
        ix = int(d_yaw * m["counts_per_degree_x"])
        iy = int(-d_pitch * m["counts_per_degree_y"] * sign_y)
        if ix or iy:
            mouse_move(ix, iy)
        return ix / m["counts_per_degree_x"], -iy * sign_y / m["counts_per_degree_y"]

    def move(self, mx, my, lift, boost):
        k = self.cfg["keys"]
        t = k["threshold"]
        want = set()
        if my > t: want.add(k["forward"])
        if my < -t: want.add(k["back"])
        if mx > t: want.add(k["right"])
        if mx < -t: want.add(k["left"])
        if lift > t: want.add(k["up"])
        if lift < -t: want.add(k["down"])
        if boost: want.add(k["boost"])
        for s in self.keys_down - want:
            key(s, False)
        for s in want - self.keys_down:
            key(s, True)
        self.keys_down = want

    def stop_look(self):
        if self.rmb:
            mouse_right(False)
            self.rmb = False

    def release(self):
        self.stop_look()
        self.move(0, 0, 0, False)

    def close(self):
        self.release()


class GamepadOutput:
    name = "gamepad"

    def __init__(self, cfg):
        import vgamepad  # noqa: imported lazily so mouse mode has no dependencies
        self.vg = vgamepad
        self.cfg = cfg
        self.pad = vgamepad.VX360Gamepad()
        self.rx = self.ry = 0.0
        self.lx = self.ly = 0.0
        self.buttons = set()
        # Turn rates (deg/s) currently commanded through the right stick.
        self.rate_yaw = self.rate_pitch = 0.0
        self.zoom = 0.0  # -1..1 for stick zoom inputs (LEFT_Y / RIGHT_Y)
        self.extra_buttons = set()  # play button + D-pad / shoulder zoom taps

    def _shape_mag(self, m):
        """Wanted fraction of full turn speed -> stick tilt the game needs for it."""
        g = self.cfg["gamepad"]
        dz = g["anti_deadzone"]
        k = max(g["stick_curve_exponent"], 0.1)
        return dz + (1 - dz) * min(m, 1.0) ** (1.0 / k)

    def _shape(self, x, y):
        if self.cfg["gamepad"]["deadzone_shape"] == "axial":
            return (math.copysign(self._shape_mag(abs(x)), x) if abs(x) > 1e-4 else 0.0,
                    math.copysign(self._shape_mag(abs(y)), y) if abs(y) > 1e-4 else 0.0)
        m = math.hypot(x, y)
        if m < 1e-4:
            return 0.0, 0.0
        scale = min(self._shape_mag(m), 1.0) / m
        return x * scale, y * scale

    def look(self, err_yaw, err_pitch, dt):
        """err_* = phone angle minus where we think the camera is.

        The game turns at roughly stick * full_stick_degrees_per_second, so credit the
        rate held since the last packet, then steer the stick to close what's left.
        """
        g = self.cfg["gamepad"]
        full = g["full_stick_degrees_per_second"]
        dt = min(dt, 0.1)
        moved_yaw, moved_pitch = self.rate_yaw * dt, self.rate_pitch * dt
        tau = max(g["response_seconds"], 0.01)
        self.rate_yaw = (err_yaw - moved_yaw) / tau
        self.rate_pitch = (err_pitch - moved_pitch) / tau
        # One stick: the combined turn speed can't exceed a full tilt, so limit the vector
        # (radial) or each axis (axial) - otherwise the model credits motion that never happened.
        if g["deadzone_shape"] == "axial":
            self.rate_yaw = max(-full, min(full, self.rate_yaw))
            self.rate_pitch = max(-full, min(full, self.rate_pitch))
        else:
            mag = math.hypot(self.rate_yaw, self.rate_pitch)
            if mag > full:
                self.rate_yaw *= full / mag
                self.rate_pitch *= full / mag
        self.rx, self.ry = self._shape(self.rate_yaw / full, self.rate_pitch / full)
        if g["invert_y"]:
            self.ry = -self.ry
        self._push()
        return moved_yaw, moved_pitch

    def move(self, mx, my, lift, boost):
        g = self.cfg["gamepad"]
        self.lx, self.ly = mx, my
        want = set()
        t = self.cfg["keys"]["threshold"]
        if lift > t: want.add(g["lift_up_button"])
        if lift < -t: want.add(g["lift_down_button"])
        if boost: want.add(g["boost_button"])
        self.buttons = {b for b in want if b}
        self._push()

    def _push(self):
        B = self.vg.XUSB_BUTTON
        self.pad.reset()
        lx, ly, ry = self.lx, self.ly, self.ry
        buttons = self.buttons | self.extra_buttons
        if abs(self.zoom) > 0.05:
            if self.cfg["zoom"]["gamepad_input"].upper() == "LEFT_Y":
                lx, ly = 0.0, self.zoom
            else:
                ry = self.zoom
        for b in buttons:
            if b == "RT":
                self.pad.right_trigger_float(value_float=1.0)
            elif b == "LT":
                self.pad.left_trigger_float(value_float=1.0)
            else:
                self.pad.press_button(button=getattr(B, "XUSB_GAMEPAD_" + b))
        self.pad.left_joystick_float(x_value_float=lx, y_value_float=ly)
        self.pad.right_joystick_float(x_value_float=self.rx, y_value_float=ry)
        self.pad.update()

    def stop_look(self):
        self.rx = self.ry = 0.0
        self.rate_yaw = self.rate_pitch = 0.0
        self._push()

    def release(self):
        self.rx = self.ry = self.lx = self.ly = 0.0
        self.rate_yaw = self.rate_pitch = 0.0
        self.zoom = 0.0
        self.buttons = set()
        self.extra_buttons = set()
        self._push()

    def close(self):
        self.release()


def make_output(cfg):
    if cfg["mode"] == "gamepad":
        try:
            return GamepadOutput(cfg)
        except ImportError:
            print("\n[!] Gamepad mode needs the vgamepad package: pip install -r requirements.txt"
                  "\n    (or use GyroCam-bridge.exe, which includes it). Falling back to mouse mode.")
        except Exception as e:  # vgamepad present but it can't reach the ViGEmBus driver
            print("\n[!] Gamepad mode needs the ViGEmBus driver, which doesn't seem to be installed"
                  f" ({e}).\n    Download and run ViGEmBus_..._x64_x86_arm64.exe from:\n    {VIGEMBUS_URL}"
                  "\n    then restart the bridge. Falling back to mouse mode for now.")
            if msvcrt and sys.stdin and sys.stdin.isatty():
                print("    Open that page now? [Y/n] ", end="", flush=True)
                if msvcrt.getwch().lower() != "n":
                    import webbrowser
                    webbrowser.open(VIGEMBUS_URL)
                print()
        cfg["mode"] = "mouse"
    return MouseOutput(cfg)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------
class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.out = make_output(cfg)
        self.lock = threading.Lock()
        self.running = True

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", cfg["port"]))
        self.sock.settimeout(0.05)

        self.phone_addr = None
        self.last_packet = 0.0
        self.last_recenter = None
        self.last_home = None
        self.tracking = False
        self.focused = False
        self.looking = False
        self.packets = 0

        # Camera angles are built from two parts:
        #   phone_* : what the gyro asks for (sens x phone rotation since the anchor)
        #   manual_*: what the LOOK stick added
        # emitted_* is where we've told the game to put the camera.
        self.prev_raw_yaw = None
        self.unwrapped_yaw = 0.0
        self.offset_yaw = self.offset_pitch = 0.0
        self.phone_yaw = self.phone_pitch = 0.0
        self.manual_yaw = self.manual_pitch = 0.0
        self.emitted_yaw = self.emitted_pitch = 0.0
        self.sens = 1.0
        self.last_t = None
        sm = cfg["smoothing"]
        self.f_yaw = OneEuro(sm["min_cutoff_hz"], sm["beta"])
        self.f_pitch = OneEuro(sm["min_cutoff_hz"], sm["beta"])
        self.cur_yaw = self.cur_pitch = self.cur_roll = 0.0
        self.calibrating_until = 0.0
        self.need_resync = True
        # Critically damped spring after the jitter filter: the "weight" of the camera.
        self.sp_yaw = self.sp_pitch = None
        self.sv_yaw = self.sv_pitch = 0.0

        z = cfg["zoom"]
        self.fov = float(z["fov_start"])
        self.fov_applied = None  # last value sent to the game
        self.zoom_busy = False
        self.zoom_touched = False
        self.wheel_acc = 0.0
        self.zoom_dir = 0
        self.zoom_next_tap = 0.0
        self.zoom_tap_until = 0.0
        # FOV slider -> D-pad taps.
        self.last_fov_sync = None
        self.fov_target = None       # from the phone slider
        self.fov_known = None        # where we believe the game's FOV is (None = unknown)
        self.fov_homing_taps = 0     # taps down still needed to reach fov_min
        self.fov_tap_phase = "idle"  # idle / press / gap
        self.fov_tap_until = 0.0
        self.fov_tap_button = None
        self.fov_target_changed_at = 0.0
        self.fov_armed = False

    def _spring(self, x, v, target, dt):
        """Critically damped spring step. Returns new (x, v)."""
        weight = self.cfg["smoothing"].get("camera_weight_seconds", 0.0)
        if x is None or weight <= 0 or dt <= 0:
            return target, 0.0
        w = 2.0 / weight
        steps = max(1, int(dt / 0.004))
        h = dt / steps
        for _ in range(steps):
            v += (w * w * (target - x) - 2 * w * v) * h
            x += v * h
        return x, v

    def resync(self):
        """Re-anchor the phone so its current pose produces the current phone_* angles. No jump."""
        self.offset_yaw = self.unwrapped_yaw - self.phone_yaw / self.sens
        self.offset_pitch = self.cur_pitch - self.phone_pitch / self.sens
        self.need_resync = False

    def recenter(self):
        """Glide the camera back to its home view; the phone's current pose becomes 'forward'."""
        self.offset_yaw = self.unwrapped_yaw
        self.offset_pitch = self.cur_pitch
        self.phone_yaw = self.phone_pitch = 0.0
        self.manual_yaw = self.manual_pitch = 0.0
        self.need_resync = False
        if self.f_yaw.x is None:
            self.f_yaw.reset(self.emitted_yaw)
            self.f_pitch.reset(self.emitted_pitch)

    def set_home(self):
        """The camera's current view becomes home. Nothing moves."""
        self.emitted_yaw = self.emitted_pitch = 0.0
        self.phone_yaw = self.phone_pitch = 0.0
        self.manual_yaw = self.manual_pitch = 0.0
        self.f_yaw.reset(0.0)
        self.f_pitch.reset(0.0)
        self.sp_yaw = self.sp_pitch = 0.0
        self.sv_yaw = self.sv_pitch = 0.0
        self.need_resync = True

    def set_sens(self, sens):
        sens = max(0.1, min(10.0, sens))
        if abs(sens - self.sens) > 1e-4:
            self.sens = sens
            self.need_resync = True  # keep phone_* continuous under the new multiplier

    def update_zoom(self, value, dt, now):
        z = self.cfg["zoom"]
        method = z["method"]
        if z["invert"]:
            value = -value
        if method == "gamepad":
            if not isinstance(self.out, GamepadOutput):
                return
            target = z["gamepad_input"].upper()
            if target in ("LEFT_Y", "RIGHT_Y"):
                self.out.zoom = value
                return
            # Button inputs: one tap per press, auto-repeat while held.
            direction = 1 if value > 0.35 else (-1 if value < -0.35 else 0)
            if direction != self.zoom_dir:
                self.zoom_dir = direction
                if direction:  # fresh press: tap now, repeat after a pause
                    self.zoom_tap_until = now + 0.08
                    self.zoom_next_tap = now + z["repeat_delay_seconds"]
            elif direction and now >= self.zoom_next_tap:
                self.zoom_tap_until = now + 0.08
                self.zoom_next_tap = now + z["repeat_seconds"]
            buttons = {"DPAD": ("DPAD_UP", "DPAD_DOWN"),
                       "SHOULDERS": ("RIGHT_SHOULDER", "LEFT_SHOULDER")}.get(target, ("DPAD_UP", "DPAD_DOWN"))
            if direction and now < self.zoom_tap_until:
                self.out.extra_buttons.add(buttons[0] if direction > 0 else buttons[1])
            return
        if method == "mouse_wheel":
            if abs(value) > 0.05:
                self.wheel_acc += value * z["wheel_clicks_per_second"] * dt
                clicks = int(self.wheel_acc)
                if clicks:
                    self.wheel_acc -= clicks
                    mouse_wheel(-clicks)  # wheel up usually zooms in = lower FOV
            else:
                self.wheel_acc = 0.0
            return
        if method != "freecam_fov":
            return
        if abs(value) > 0.05:
            self.fov = max(z["fov_min"], min(z["fov_max"], self.fov + value * z["degrees_per_second"] * dt))
            self.zoom_touched = True
            return
        target = int(round(self.fov))
        # Only touch the console once the buttons have been used and released.
        if self.zoom_touched and target != self.fov_applied and not self.zoom_busy:
            self.zoom_busy = True
            self.fov_applied = target
            threading.Thread(target=self._send_fov, args=(target,), daemon=True).start()

    def update_fov_slider(self, target, now):
        """Absolute FOV from the phone slider."""
        z = self.cfg["zoom"]
        step = max(1, z["fov_step"])
        lo, hi = z["fov_min"], z["fov_max"]
        target = max(lo, min(hi, lo + round((target - lo) / step) * step))
        if self.fov_target is None:
            self.fov_target = target  # the slider's position at connect; not a request yet
            return
        if target != self.fov_target:
            self.fov_target = target
            self.fov_target_changed_at = now
            self.fov_armed = True
        if not self.fov_armed:
            return  # never press anything until the slider is moved (or SYNC is pressed)

        if z["method"] == "freecam_fov":
            # Console command once the slider has rested briefly.
            self.fov = target
            if target != self.fov_applied and not self.zoom_busy and now - self.fov_target_changed_at > 0.25:
                self.zoom_busy = True
                self.fov_applied = target
                threading.Thread(target=self._send_fov, args=(int(target),), daemon=True).start()
            return
        if z["method"] != "gamepad" or not isinstance(self.out, GamepadOutput):
            return
        inp = z["gamepad_input"].upper()
        up, down = ("RIGHT_SHOULDER", "LEFT_SHOULDER") if inp == "SHOULDERS" else ("DPAD_UP", "DPAD_DOWN")
        if z["invert"]:
            up, down = down, up

        # Tap state machine: press for tap_press_seconds, release for tap_gap_seconds.
        if self.fov_tap_phase == "press" and now >= self.fov_tap_until:
            self.fov_tap_phase, self.fov_tap_until = "gap", now + z["tap_gap_seconds"]
        elif self.fov_tap_phase == "gap" and now >= self.fov_tap_until:
            self.fov_tap_phase = "idle"
        if self.fov_tap_phase == "idle":
            button = None
            if self.fov_known is None and self.fov_homing_taps == 0:
                # Unknown position: step down past the whole range, then count up from fov_min.
                self.fov_homing_taps = int((hi - lo) / step) + 1
            if self.fov_homing_taps > 0:
                button = down
                self.fov_homing_taps -= 1
                if self.fov_homing_taps == 0:
                    self.fov_known = lo
            elif self.fov_known is not None and self.fov_known != self.fov_target:
                direction = 1 if self.fov_target > self.fov_known else -1
                button = up if direction > 0 else down
                self.fov_known += direction * step
            if button:
                self.fov_tap_phase, self.fov_tap_until = "press", now + z["tap_press_seconds"]
                self.fov_tap_button = button
        if self.fov_tap_phase == "press":
            self.out.extra_buttons.add(self.fov_tap_button)

    def _send_fov(self, fov):
        try:
            reskate_console(f"freecamfov {fov}", self.cfg["zoom"]["console_key_vk"])
            print(f"\n[zoom] freecamfov {fov}")
        finally:
            self.zoom_busy = False

    def handle(self, data, addr):
        if len(data) < PHONE_SIZE or data[:4] != PHONE_MAGIC:
            return
        (_, seq, flags, recenter, rotation, home, qx, qy, qz, qw,
         mx, my, lift, _t) = struct.unpack_from(PHONE_FMT, data)
        # Fields appended by newer apps.
        zoom = struct.unpack_from("<f", data, 44)[0] if len(data) >= 48 else 0.0
        sens = struct.unpack_from("<f", data, 48)[0] if len(data) >= 52 else 1.0
        view_x, view_y = struct.unpack_from("<2f", data, 52) if len(data) >= 60 else (0.0, 0.0)
        fov_target = struct.unpack_from("<f", data, 60)[0] if len(data) >= 64 else 0.0
        fov_sync = data[10]
        now = time.perf_counter()
        self.phone_addr = addr
        self.last_packet = now
        self.packets += 1

        yaw, pitch, roll = camera_angles((qx, qy, qz, qw), rotation)
        if self.prev_raw_yaw is None:
            self.prev_raw_yaw = yaw
        self.unwrapped_yaw += wrap180(yaw - self.prev_raw_yaw)
        self.prev_raw_yaw = yaw
        self.cur_yaw, self.cur_pitch, self.cur_roll = yaw, pitch, roll
        if math.isfinite(sens) and sens > 0:
            self.set_sens(sens)

        if self.last_recenter is None:
            self.last_recenter = recenter
        elif recenter != self.last_recenter:
            self.last_recenter = recenter
            self.recenter()
        if self.last_home is None:
            self.last_home = home
        elif home != self.last_home:
            self.last_home = home
            self.set_home()
            print("\n[home] camera's current view saved as home")
        if self.last_fov_sync is None:
            self.last_fov_sync = fov_sync
        elif fov_sync != self.last_fov_sync:
            self.last_fov_sync = fov_sync
            self.fov_known = None  # re-learn: tap down to fov_min again
            self.fov_homing_taps = 0
            self.fov_armed = True
            print("\n[fov] syncing: stepping down to the minimum first")

        tracking = bool(flags & FLAG_TRACKING)
        dt = min(now - self.last_t, 0.1) if self.last_t else 0.0
        self.last_t = now
        self.focused = self._target_focused()

        if not self.focused or now < self.calibrating_until:
            if self.looking or self.tracking:
                self.out.release()
            self.tracking = self.looking = False
            self.need_resync = True
        else:
            if tracking and not self.tracking:
                self.need_resync = True
            self.tracking = tracking
            if tracking:
                if self.need_resync:
                    self.resync()
                self.phone_yaw = self.sens * (self.unwrapped_yaw - self.offset_yaw)
                self.phone_pitch = self.sens * (pitch - self.offset_pitch)

            look_speed = self.cfg["look_stick_degrees_per_second"]
            if abs(view_x) > 0.05:
                self.manual_yaw += view_x * abs(view_x) * look_speed * dt  # squared for fine aim
            if abs(view_y) > 0.05:
                self.manual_pitch += view_y * abs(view_y) * look_speed * dt
            # Keep pitch inside the game's clamp; trim the LOOK part first so it can't wind up.
            lim = self.cfg["mouse"]["pitch_limit_degrees"]
            total_pitch = self.phone_pitch + self.manual_pitch
            if abs(total_pitch) > lim:
                self.manual_pitch -= total_pitch - math.copysign(lim, total_pitch)

            goal_yaw = self.phone_yaw + self.manual_yaw
            goal_pitch = max(-lim, min(lim, self.phone_pitch + self.manual_pitch))
            target_yaw = self.f_yaw(goal_yaw, dt)
            target_pitch = self.f_pitch(goal_pitch, dt)
            if self.sp_yaw is None:
                self.sp_yaw, self.sp_pitch = self.emitted_yaw, self.emitted_pitch
            self.sp_yaw, self.sv_yaw = self._spring(self.sp_yaw, self.sv_yaw, target_yaw, dt)
            self.sp_pitch, self.sv_pitch = self._spring(self.sp_pitch, self.sv_pitch, target_pitch, dt)
            target_yaw, target_pitch = self.sp_yaw, self.sp_pitch
            err_yaw, err_pitch = target_yaw - self.emitted_yaw, target_pitch - self.emitted_pitch
            # Drive the camera while tracking, while LOOK is used, or while gliding home.
            # Compare against the final goal, not the smoothed position, so a glide finishes.
            active = tracking or abs(view_x) > 0.05 or abs(view_y) > 0.05 \
                or abs(goal_yaw - self.emitted_yaw) > 0.3 or abs(goal_pitch - self.emitted_pitch) > 0.3
            if active:
                sy, sp = self.out.look(err_yaw, err_pitch, dt)
                self.emitted_yaw += sy
                self.emitted_pitch += sp
            elif self.looking:
                self.out.stop_look()
            self.looking = active

            self.out.extra_buttons = {self.cfg["gamepad"]["play_button"]} if flags & FLAG_PLAY else set()
            if fov_target > 0 and math.isfinite(fov_target):
                self.update_fov_slider(fov_target, now)
            else:
                self.update_zoom(zoom, dt, now)
            self.out.move(mx, my, lift, bool(flags & FLAG_BOOST))

        if self.cfg["zoom"]["method"] == "freecam_fov":
            fov = int(round(self.fov))
        else:
            fov = int(round(self.fov_known)) if self.fov_known is not None else 0
        ack = ACK_MAGIC + struct.pack("<BBHB", int(self.focused) | (int(self.tracking) << 1),
                                      0 if self.out.name == "mouse" else 1, seq, fov)
        try:
            self.sock.sendto(ack, addr)
        except OSError:
            pass

    def _target_focused(self):
        procs = self.cfg.get("target_processes") or []
        if not procs:
            return True
        exe = foreground_exe().lower()
        return any(exe == p.lower() for p in procs)

    def receive_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(256)
            except socket.timeout:
                data = None
            except OSError:
                continue
            with self.lock:
                if data:
                    self.handle(data, addr)
                if (self.tracking or self.looking) and \
                        time.perf_counter() - self.last_packet > self.cfg["timeout_seconds"]:
                    self.out.release()
                    self.tracking = self.looking = False
                    self.need_resync = True

    def beacon_loop(self):
        b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        b.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = BEACON_MAGIC + struct.pack("<H", self.cfg["port"]) + socket.gethostname().encode()[:32]
        while self.running:
            for addr in broadcast_addresses():
                try:
                    b.sendto(msg, (addr, self.cfg["discovery_port"]))
                except OSError:
                    pass
            time.sleep(1.0)

    def calibration_turn(self):
        """Send exactly 360 degrees of yaw (per current calibration) for checking."""
        print("\n[calibrate] Switch to the game with the free camera on (menus closed). "
              "Turning 360 degrees in 3 s...")
        if self.cfg["mode"] == "gamepad":
            full = self.cfg["gamepad"]["full_stick_degrees_per_second"]
            self.calibrating_until = time.perf_counter() + 3.5 + 360.0 / full
            time.sleep(3.0)
            with self.lock:
                out = self.out
                out.rx, out.ry = 1.0, 0.0
                out._push()
            time.sleep(360.0 / full)
            with self.lock:
                out.release()
            print("[calibrate] Done. If the camera turned too far press '-', not far enough press '+'"
                  f" (now {full:.1f} deg/s at full stick).")
            return
        self.calibrating_until = time.perf_counter() + 6.0
        time.sleep(3.0)
        m = self.cfg["mouse"]
        total = int(round(360 * m["counts_per_degree_x"]))
        steps = 120
        if m["hold_right_mouse"]:
            mouse_right(True)
        sent = 0
        for i in range(1, steps + 1):
            target = total * i // steps
            mouse_move(target - sent, 0)
            sent = target
            time.sleep(1.5 / steps)
        if m["hold_right_mouse"]:
            mouse_right(False)
        print("[calibrate] Done. If the camera turned too far press '-', not far enough press '+'"
              f" (now {m['counts_per_degree_x']:.2f} counts/deg).")

    def find_deadzone(self):
        """Slowly tilt the right stick; the user presses F8 when the camera starts to move."""
        if self.cfg["mode"] != "gamepad":
            print("\n[deadzone] Only used in gamepad mode.")
            return
        ramp_seconds, top = 12.0, 0.6
        print("\n[deadzone] Switch to the game (free camera, menus closed). In 3 s the right stick "
              "starts tilting slowly. Press F8 the moment the camera starts to turn.")
        self.calibrating_until = time.perf_counter() + 3.5 + ramp_seconds
        time.sleep(3.0)
        user32.GetAsyncKeyState(0x77)  # clear the "pressed since last call" bit
        start = time.perf_counter()
        found = None
        while time.perf_counter() - start < ramp_seconds:
            v = top * (time.perf_counter() - start) / ramp_seconds
            with self.lock:
                self.out.rx, self.out.ry = v, 0.0
                self.out._push()
            if user32.GetAsyncKeyState(0x77) & 0x8001:  # F8
                found = max(0.0, v - 0.25 * top / ramp_seconds)  # minus ~0.25 s reaction time
                break
            time.sleep(0.01)
        with self.lock:
            self.out.release()
        self.calibrating_until = 0.0
        if found is None:
            print("[deadzone] No F8 press; nothing changed.")
            return
        self.cfg["gamepad"]["anti_deadzone"] = round(found, 3)
        save_config(self.cfg)
        print(f"[deadzone] Saved anti_deadzone = {found:.3f}. Recalibrate with `c` afterwards.")

    def adjust_sensitivity(self, factor):
        """factor > 1 = the camera turns further per phone degree."""
        if self.cfg["mode"] == "gamepad":
            g = self.cfg["gamepad"]
            g["full_stick_degrees_per_second"] = round(g["full_stick_degrees_per_second"] / factor, 3)
            save_config(self.cfg)
            print(f"\n[sensitivity] {g['full_stick_degrees_per_second']:.1f} deg/s at full stick (saved)")
            return
        m = self.cfg["mouse"]
        m["counts_per_degree_x"] = round(m["counts_per_degree_x"] * factor, 3)
        m["counts_per_degree_y"] = round(m["counts_per_degree_y"] * factor, 3)
        save_config(self.cfg)
        print(f"\n[sensitivity] {m['counts_per_degree_x']:.3f} counts/deg (saved)")

    def switch_mode(self):
        with self.lock:
            self.out.close()
            self.cfg["mode"] = "gamepad" if self.cfg["mode"] == "mouse" else "mouse"
            self.out = make_output(self.cfg)
            save_config(self.cfg)
            self.need_resync = True
        print(f"\n[mode] {self.cfg['mode']}")

    def status_line(self):
        connected = time.perf_counter() - self.last_packet < 1.0
        phone = f"{self.phone_addr[0]}" if (self.phone_addr and connected) else "waiting for phone"
        if self.cfg["mode"] == "mouse" and self.focused and cursor_visible():
            return ("\r[mouse] GAME CURSOR IS SHOWING - mouse input goes to the UI, not the camera. "
                    "Close the ReSkate menu (End) or press m for gamepad mode.   ")
        return (f"\r[{self.cfg['mode']}] {phone:<18} game {'FOCUSED' if self.focused else 'not focused'}"
                f"  {'TRACKING' if self.tracking else 'idle    '}"
                f"  sens {self.sens:4.2f}x  cam yaw {wrap180(self.emitted_yaw):7.1f}"
                f" pitch {self.emitted_pitch:6.1f}   ")

    def run(self):
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.beacon_loop, daemon=True).start()
        try:
            while self.running:
                sys.stdout.write(self.status_line())
                sys.stdout.flush()
                if msvcrt and msvcrt.kbhit():
                    ch = msvcrt.getwch().lower()
                    if ch == "q":
                        break
                    elif ch == "r":
                        with self.lock:
                            self.recenter()
                        print("\n[recenter] camera returns to home on the next tracking frame")
                    elif ch == "h":
                        with self.lock:
                            self.set_home()
                        print("\n[home] camera's current view saved as home")
                    elif ch == "d":
                        threading.Thread(target=self.find_deadzone, daemon=True).start()
                    elif ch == "c":
                        threading.Thread(target=self.calibration_turn, daemon=True).start()
                    elif ch in "+=":
                        self.adjust_sensitivity(1.02)
                    elif ch in "-_":
                        self.adjust_sensitivity(1 / 1.02)
                    elif ch == "m":
                        self.switch_mode()
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            with self.lock:
                self.out.close()
            print("\nBye.")


def local_ipv4s():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def broadcast_addresses():
    # /24 directed broadcast covers typical home Wi-Fi; plus the limited broadcast.
    addrs = {"255.255.255.255"}
    for ip in local_ipv4s():
        addrs.add(".".join(ip.split(".")[:3] + ["255"]))
    return addrs


def main():
    cfg = load_config()
    print(__doc__.strip().split("\n\n")[0] + f"  [v{VERSION}]")
    print(f"\nListening on UDP {cfg['port']}. Type one of these IPs into the phone app "
          f"(or leave it on auto): {', '.join(local_ipv4s()) or 'unknown'}")
    print("Keys: r recenter | h set home | d find deadzone | c calibrate | +/- sensitivity | "
          "m gamepad/mouse | q quit\n")
    Bridge(cfg).run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        if not FROZEN:
            raise
        # A double-clicked .exe would otherwise vanish before the error can be read.
        import traceback
        traceback.print_exc()
        if isinstance(e, OSError) and getattr(e, "winerror", None) == 10048:
            print("\nPort 47823 is already in use - is another GyroCam bridge already running?")
        input("\nPress Enter to close.")

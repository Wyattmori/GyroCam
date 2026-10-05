# GyroCam for skate. (ReSkate)

Use your Android phone as a handheld camera, in the style of the Assetto Corsa
[ARCamera](https://www.overtake.gg/downloads/arcamera.86246/) mod. Point the phone and the
in-game camera turns with it. The on-screen sticks fly the camera around. It's built for
filming replays.

```
 phone (GyroCam.apk) --Wi-Fi UDP 47823--> PC bridge (gyrocam_bridge.py) --> mouse / keys or virtual pad --> skate.
                     <--beacon UDP 47824--
```

**Download:** get `GyroCam.apk` and `GyroCam-bridge.exe` from the
[Releases](../../releases) page. **Want to improve it?** See [CONTRIBUTING.md](CONTRIBUTING.md).

> Fan-made and unofficial. Not affiliated with EA, Full Circle or the ReSkate project.

## Why it isn't a normal `Mods/` mod

ReSkate's `Mods/` folder only loads Frostbite data mods (built with ReSkate Studio) and park
mods. Its `scripts/Custom` Lua runs once at startup and has no camera or network access.
GyroCam therefore drives the cameras that already exist through their own inputs:

| Mode | Camera it drives | How |
|------|------------------|-----|
| `gamepad` (default) | ReSkate **Freecam** and the native **replay editor free camera** | Virtual Xbox 360 pad using Freecam's controller layout: right stick looks, left stick moves, RT / LT rise / fall, L3 boosts. The bridge steers the stick so the camera follows the phone's angle |
| `mouse` | ReSkate **Freecam** only, with the ReSkate menu (End) closed | Absolute mouse counts with the right mouse button held, plus WASD / Q E / Shift. Fails wherever the game shows a cursor: the cursor just moves. The bridge warns when that happens |

Roll can't be sent: there's no live roll input. Use the replay editor's Roll setting.

## Setup

### PC
1. Install the [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) driver
   (`ViGEmBus_..._x64_x86_arm64.exe`). This is what lets the bridge create a virtual Xbox
   controller. Without it the bridge falls back to mouse mode; it tells you and offers to open
   the download page.
2. Download `GyroCam-bridge.exe` from [Releases](../../releases), put it in its own folder
   (its `config.json` is saved next to it) and run it. No Python needed.
   - Windows SmartScreen may warn about an unrecognised app: click **More info → Run anyway**.
   - When Windows Firewall asks, allow it on **Private** networks so the phone can reach it.

Running from source instead: install Python 3.10+, run
`pip install -r pc-bridge/requirements.txt`, then `pc-bridge/run_bridge.bat`. To build the exe
yourself: `pip install pyinstaller`, then `python pc-bridge/build_exe.py`.

### Phone
1. Download `GyroCam.apk` from [Releases](../../releases) (or build it, see below) and install
   it. You'll need to allow "install unknown apps".
2. Connect to the same Wi-Fi as the PC and open GyroCam. It finds the bridge automatically. If it
   doesn't, type the IP shown in the bridge window.

### Filming
1. In skate., open your replay and switch to a free camera: the replay editor's free camera,
   or ReSkate Freecam. Close the ReSkate menu (End) before tracking.
2. Hold the phone like a camera, pointed at the screen. Tap **RECENTER**, then
   **START TRACKING**. Tap **PLAY (A)** to resume the replay.
3. Tracking only sends input while `Skate.exe` is the focused window.

The app works upright (portrait) or sideways (landscape). If your phone's auto-rotate is
locked, it stays in whatever orientation the lock is set to.

Phone controls:
- **LOOK stick**: aims the camera quickly. It adds to the gyro and also works while tracking
  is paused. Small pushes turn slowly for fine aiming; full push turns at
  `look_stick_degrees_per_second`.
- **MOVE stick / UP/DN**: fly the camera (left stick / RT and LT).
- **SENS − / +**: how far the camera turns per phone degree, 0.25×–4× (2× = turn the phone 10°,
  the camera turns 20°). Remembered between sessions.
- **PLAY (A)**: presses A on the virtual controller (resume replay). Held for as long as you
  hold it.
- **FOV slider (50–120°)**: drag to the FOV you want. The bridge rapid-taps D-pad up or down
  (the replay editor's FOV setting) until the game matches, about 12 steps a second. The game
  only moves in 5° steps, so that's as fine as it gets.
  - **First use:** the bridge doesn't know the game's current FOV. The first time you move the
    slider, it steps all the way down to 50° and then counts up to your value (about 2 s).
  - **SYNC:** press it if the game and slider ever disagree (e.g. you changed FOV with a real
    controller). It repeats that step-down.
  - Nothing is pressed until you move the slider or press SYNC.
- **Recenter** (or **Vol+**): the camera glides back to its home view, and the way you're
  holding the phone right now becomes "forward". This also clears anything the LOOK stick added.
- **Long-press Recenter**: makes the camera's current view the new home. Nothing moves.
- **Vol−**: toggle tracking.
- **Hold to track**: tracking runs only while you hold the button. Release it, reposition your
  arm, then hold again. The camera doesn't jump (a "clutch").

FOV slider with `zoom.method` set to `freecam_fov` (for ReSkate Freecam): when the slider stops,
the bridge opens ReSkate's console, runs `freecamfov <deg>` and closes it. The console flashes
for about 0.3 s.

Bridge keys: `r` recenter, `h` set home, `d` find deadzone, `c` calibrate.

### Smooth, handheld-looking camera
- **Fix the deadzone first.** If small, slow phone moves do nothing and then the camera jumps,
  the game's stick deadzone is bigger than the bridge assumes. In skate. with the free camera on
  and menus closed, focus the bridge window and press `d`, then switch to the game. After 3 s
  the right stick starts tilting very slowly. Press **F8** the moment the camera starts to
  turn. The value is saved; then recalibrate with `c`.
- **Camera weight** (`smoothing.camera_weight_seconds`, default 0.15) makes moves ease in and
  out like a real camera. Raise it (0.25–0.35) for a heavier, steadier camera, or lower it to
  0.05 for snappier response. 0 turns it off.
- Your real hand shake stays in. That's what makes it look handheld.

### Calibrate (once per mode)
In skate. with the free camera on and menus closed, focus the bridge window and press `c`, then
switch back to the game within 3 s. The bridge sends what it thinks is one full turn.
- Turned too far: press `-`.
- Not far enough: press `+`.
- Repeat until it lands back where it started.

The value is saved to `config.json`. After that, 45° on the phone gives about 45° in game.

## Config (`pc-bridge/config.json`, created on first run)

| Key | Meaning |
|-----|---------|
| `mode` | `mouse` or `gamepad` |
| `target_processes` | Input is only sent while one of these is focused. `[]` = always |
| `mouse.counts_per_degree_x/y` | Calibration |
| `mouse.hold_right_mouse` | Hold RMB while tracking (needed for ReSkate Freecam) |
| `mouse.pitch_limit_degrees` | Keeps the bridge in sync with the game's pitch clamp |
| `gamepad.full_stick_degrees_per_second` | How fast the game turns at full stick (calibration) |
| `gamepad.response_seconds` | How fast the stick catches up to the phone. Lower = snappier |
| `gamepad.lift_up_button` / `lift_down_button` | `RT` / `LT` by default, or any XUSB button name |
| `gamepad.anti_deadzone` | The game's stick deadzone (default 0.27, the Xbox standard). Measure with `d` |
| `gamepad.deadzone_shape` | `radial` (default, most games) or `axial` |
| `gamepad.stick_curve_exponent` | If the game turns at stick^k, set k (1 = linear) |
| `smoothing.camera_weight_seconds` | Camera weight / ease in-out. 0 = off |
| `keys.*` | Scan codes for move/lift/boost (default W A S D, Q down, E up, LShift) |
| `smoothing.min_cutoff_hz`, `smoothing.beta` | One Euro filter. Lower cutoff = steadier, higher beta = snappier swings |
| `look_stick_degrees_per_second` | LOOK stick turn speed at full push |
| `gamepad.play_button` | Button the PLAY key presses (default `A`) |
| `zoom.method` | `gamepad` (default), `freecam_fov`, `mouse_wheel` or `off` |
| `zoom.tap_press_seconds` / `tap_gap_seconds` | D-pad tap length and gap for the FOV slider (raise them if the game misses steps) |
| `zoom.fov_min` / `fov_max` / `fov_step` | The game's FOV range and step: 50 / 120 / 5 for the replay editor |
| `zoom.degrees_per_second` | How fast the FOV changes at full slider |
| `zoom.console_key_vk` | ReSkate's console key (192 = ` / ~). Must match `console_key` in `ReSkateLauncher.settings.json` |
| `zoom.gamepad_input` | `DPAD` (higher FOV = up, default) or `SHOULDERS` (higher FOV = RB) |
| `zoom.invert` | Swap which button raises the FOV |

## Building the APK

Needs a JDK (8+). Gradle and Android Studio aren't needed.

```bash
cd android
python build_apk.py --download-sdk
```

`--download-sdk` fetches build-tools 34 and the android-34 platform (~120 MB, checksum-verified)
from dl.google.com into `android/.sdk`. Google's Android SDK license applies. If you already have
an SDK, set `ANDROID_HOME` and omit the flag. Add `--install` to push the APK over adb.
Output: `android/build/GyroCam.apk`.

## Protocol (for anyone extending this)

Phone → PC, UDP 47823, 64 bytes (older apps sent the first 44, 48 or 60), little endian:

| Offset | Type | Field |
|--------|------|-------|
| 0 | char[4] | `GYC1` |
| 4 | u16 | sequence |
| 6 | u8 | flags: bit0 tracking, bit1 boost, bit2 play (A) |
| 7 | u8 | recenter counter (changes = recenter) |
| 8 | u8 | display rotation (`Surface.ROTATION_*`) |
| 9 | u8 | set-home counter (changes = set home) |
| 10 | u8 | FOV sync counter (changes = re-sync) |
| 11 | pad | |
| 12 | f32×4 | `TYPE_GAME_ROTATION_VECTOR` quaternion x, y, z, w |
| 28 | f32×3 | move x, move y, lift (−1..1) |
| 40 | u32 | phone uptime ms |
| 44 | f32 | FOV buttons: +1 = FOV +, −1 = FOV − |
| 48 | f32 | sensitivity multiplier |
| 52 | f32×2 | LOOK stick x, y (−1..1) |
| 60 | f32 | FOV slider target in degrees |

PC → phone ack: `GYCA` u8 flags (bit0 game focused, bit1 tracking), u8 mode, u16 seq,
u8 Freecam FOV (0 = not used).
PC broadcast beacon on UDP 47824: `GYCB` u16 port, hostname.

A future native ReSkate camera hook could consume the same packets and set the camera
transform directly, which would add roll and absolute positioning.

## License

[MIT](LICENSE). Contributions welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

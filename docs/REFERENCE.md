# GyroCam reference

## How it works

```
phone app --Wi-Fi UDP 47823--> bridge (exe / gyrocam_bridge.py) --> virtual Xbox pad or mouse --> skate.
          <--beacon UDP 47824--
```

ReSkate's `Mods/` folder only loads data and park mods, and its Lua scripts can't control the
camera or use the network. So GyroCam drives the game's existing cameras through their own
inputs:

- **`gamepad` mode (default):** a virtual Xbox controller. Works with the replay editor's free
  camera and ReSkate Freecam:
  - right stick: look
  - left stick: move
  - RT / LT: up / down
  - L3: boost
  - D-pad: FOV
  - A: play
- **`mouse` mode:** mouse + WASD/QE. Only works in ReSkate Freecam with the ReSkate menu
  closed, because anywhere the game shows a cursor, the cursor just moves.

There's no live roll input in either mode, so roll can't be sent.

## Bridge keys

| Key | Action |
|-----|--------|
| `r` / `h` | Recenter / set home |
| `d` | Find the stick deadzone (press F8 when the camera starts moving) |
| `c`, `+`, `-` | Calibrate the turn speed |
| `m` | Switch gamepad / mouse mode |
| `q` | Quit |

## Config (`config.json`, created next to the exe on first run)

| Key | Meaning |
|-----|---------|
| `mode` | `gamepad` or `mouse` |
| `target_processes` | Only send input while one of these is focused (`[]` = always) |
| `gamepad.full_stick_degrees_per_second` | Game turn speed at full stick (set by `c`) |
| `gamepad.anti_deadzone` | Game stick deadzone, default 0.27 (set by `d`) |
| `gamepad.deadzone_shape` | `radial` (default) or `axial` |
| `gamepad.stick_curve_exponent` | If the game turns at stick^k, set k (1 = linear) |
| `gamepad.response_seconds` | How fast the stick catches up. Lower = snappier |
| `gamepad.lift_up_button` / `lift_down_button` / `boost_button` / `play_button` | Button mapping (`RT`, `LT` or any XUSB name such as `A`, `LEFT_THUMB`) |
| `smoothing.camera_weight_seconds` | Ease in/out like a real camera. 0.25–0.35 = heavier, 0 = off |
| `smoothing.min_cutoff_hz`, `smoothing.beta` | Jitter filter. Lower cutoff = steadier, higher beta = less lag on fast swings |
| `look_stick_degrees_per_second` | LOOK stick speed at full push |
| `zoom.method` | `gamepad` (D-pad, default), `freecam_fov` (ReSkate console), `mouse_wheel`, `off` |
| `zoom.fov_min` / `fov_max` / `fov_step` | Game FOV range and step (50 / 120 / 5) |
| `zoom.tap_press_seconds` / `tap_gap_seconds` | D-pad tap timing. Raise these if steps get missed |
| `zoom.gamepad_input`, `zoom.invert` | `DPAD` or `SHOULDERS`; swap direction |
| `zoom.console_key_vk` | ReSkate console key for `freecam_fov` (192 = `` ` ``) |
| `mouse.counts_per_degree_x/y`, `mouse.hold_right_mouse`, `mouse.pitch_limit_degrees` | Mouse mode settings |
| `keys.*` | Mouse mode scan codes (W A S D, Q down, E up, LShift) |

## Building

- **APK** (needs a JDK; no Gradle or Android Studio):
  ```bash
  python android/build_apk.py --download-sdk
  ```
  The first time, this downloads ~120 MB of Android build tools into `android/.sdk`.
- **Exe:**
  ```bash
  pip install pyinstaller vgamepad
  python pc-bridge/build_exe.py
  ```
- **Run from source:**
  ```bash
  pip install -r pc-bridge/requirements.txt
  ```
  then run `pc-bridge/run_bridge.bat`.

## Protocol

Phone → PC, UDP 47823, little endian, 64 bytes. Older apps sent 44, 48 or 60 bytes; the bridge
reads by length.

| Offset | Type | Field |
|--------|------|-------|
| 0 | char[4] | `GYC1` |
| 4 | u16 | sequence |
| 6 | u8 | flags: bit0 tracking, bit1 boost, bit2 play |
| 7 / 9 / 10 | u8 | recenter / set-home / FOV-sync counters (a change triggers the action) |
| 8 | u8 | display rotation (`Surface.ROTATION_*`) |
| 12 | f32×4 | rotation quaternion x, y, z, w (`TYPE_GAME_ROTATION_VECTOR`) |
| 28 | f32×3 | move x, move y, lift |
| 40 | u32 | phone uptime ms |
| 44 | f32 | legacy FOV buttons (±1) |
| 48 | f32 | sensitivity |
| 52 | f32×2 | LOOK stick x, y |
| 60 | f32 | FOV slider target (degrees) |

Other messages:
- **PC → phone ack:** `GYCA`, u8 flags (bit0 game focused, bit1 tracking), u8 mode, u16 seq,
  u8 FOV.
- **Discovery beacon:** the PC broadcasts `GYCB`, u16 port and its hostname on UDP 47824.

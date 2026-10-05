# Contributing to GyroCam

Thanks for helping make GyroCam better. This page covers how the pieces fit together, how
to test changes without the game, and the open problems that would help most.

## How it works

```
Android app ──UDP 47823──> pc-bridge ──virtual Xbox pad / mouse+keys──> skate.
(gyro, sticks,             (orientation math,
 buttons, sliders)          smoothing, camera model)
```

- **`android/`**: a single-activity Java app with no AndroidX or Gradle.
  - `MainActivity.java` reads `TYPE_GAME_ROTATION_VECTOR` and sends a 64-byte packet about
    120 times a second.
  - `StickView.java` is the on-screen stick/slider.
  - The layout is built in code, for both portrait and landscape.
- **`pc-bridge/gyrocam_bridge.py`**: plain Python. Gamepad mode also needs `vgamepad` and the
  ViGEmBus driver.
  - `camera_angles()` turns the phone quaternion and display rotation into the back camera's
    yaw, pitch and roll.
  - `Bridge.handle()` builds the camera angle from two parts:
    - **phone part:** sensitivity × phone rotation since the anchor
    - **LOOK part:** what the LOOK stick has added

    It then smooths that through a One Euro filter (jitter) and a critically damped spring
    ("camera weight").
  - The game has no API to set the camera, so the bridge keeps a *model* of where the camera
    is (`emitted_yaw/pitch`) and steers inputs to close the gap:
    - **Mouse mode:** exact counts per degree.
    - **Gamepad mode:** a rate model (turn speed = stick tilt past the deadzone × full-stick
      speed). This is why calibration (`c`) and the deadzone (`d`) matter.
  - **FOV slider:** taps D-pad up/down until the model's FOV matches the slider. The first move
    (and SYNC) steps down to `fov_min` to learn where the game is.
- **Protocol:** documented in [README.md](README.md#protocol-for-anyone-extending-this). New
  fields go on the **end** of the packet; the bridge checks the packet length, so old apps keep
  working.

## Running the tests

No phone, game or driver needed. The suite fakes the virtual controller and simulates a game
camera with a radial deadzone:

```bash
python tests/test_bridge.py
```

Please run it before opening a pull request, and add a test for any behaviour you change.

## Building the APK

```bash
python android/build_apk.py --download-sdk
```

This fetches build-tools 34 and the android-34 platform (~120 MB) into `android/.sdk` the first
time, then builds `android/build/GyroCam.apk`.

Two gotchas:
- **No anonymous inner classes in Java.** d8 34.0.0 crashes on them when compiled by a newer
  JDK. Use lambdas, or have the activity implement the listener (see the FOV `SeekBar`).
- **APK signing.** Each machine generates its own `debug.keystore`, which is git-ignored, and
  Android won't install an update signed with a different key over an existing install.
  Release APKs should come from one maintainer's key.

## Things that would help most

- **Native camera hook.** Setting the camera transform directly inside Skate.exe (e.g. through
  a ReSkate SDK hook, if one becomes available) would remove the deadzone and rate-model
  guesswork entirely. It would also allow roll and exact FOV. The packets already carry the
  full quaternion.
- **Stick response curve.** Measuring whether skate.'s free camera turns linearly with stick
  tilt, then setting `gamepad.stick_curve_exponent` by default.
- **Replay editor controls.** Confirming which inputs the replay editor's free camera uses for
  height, speed and FOV, so the defaults match without config edits.
- **Packaging.** A PyInstaller build of the bridge, so people don't need Python installed.
- **iPhone app.** The protocol is simple enough to port.

## Pull requests

- Keep changes focused, and describe what you tested (tests, plus in-game if you could).
- Match the surrounding style. The bridge has no dependencies beyond the standard library
  except `vgamepad`, so please keep it that way.
- For new config keys:
  - add them to `DEFAULT_CONFIG` with a comment
  - document them in the README table
  - if an existing default changes, bump `CONFIG_VERSION` and migrate old values in
    `load_config()`

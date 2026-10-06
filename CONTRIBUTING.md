# Contributing

Fork the repo, make your change, and open a pull request, or just build your own version.

## Where things are

- **`android/src/.../MainActivity.java`**: phone app. Reads the gyro, draws the UI and sends
  packets.
- **`android/src/.../StickView.java`**: the on-screen sticks and sliders.
- **`pc-bridge/gyrocam_bridge.py`**: everything on the PC side:
  - orientation math
  - smoothing
  - the camera model that steers the virtual stick
  - FOV taps
- **`tests/test_bridge.py`**: offline tests. They simulate the phone and the game, so no game
  is needed.
- **`docs/REFERENCE.md`**: config keys, packet protocol and build commands.

## Workflow

```bash
python tests/test_bridge.py          # run before every PR
pc-bridge\run_bridge.bat             # run the bridge from source
python android/build_apk.py          # build the APK (add --download-sdk the first time)
python pc-bridge/build_exe.py        # build the exe
```

## Rules of thumb

- **Packets:** add new fields to the **end** of the packet so old apps keep working.
- **Config:**
  - New keys go in `DEFAULT_CONFIG` and the table in `docs/REFERENCE.md`.
  - If you change an existing default, bump `CONFIG_VERSION` and migrate old values in
    `load_config()`.
- **Java:** don't use anonymous inner classes (the d8 build tool crashes on them). Use lambdas
  instead.
- **Dependencies:** the bridge uses only the standard library plus `vgamepad`. Please keep it
  that way.

## Ideas that would help most

- **A native camera hook** inside Skate.exe. It would remove the deadzone guesswork and add
  roll.
- **The replay editor's controls:** measure how its free camera responds to the stick
  (response curve, inputs) so the defaults work out of the box.
- **A signed exe or installer** (no SmartScreen warning).
- **An iPhone app.**

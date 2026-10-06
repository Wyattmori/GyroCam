# GyroCam for skate. (ReSkate)

Use your Android phone as a handheld camera for skate. replays, like the Assetto Corsa
[ARCamera](https://www.overtake.gg/downloads/arcamera.86246/) mod. Point the phone and the
in-game camera follows.

> Fan-made and unofficial. Not affiliated with EA, Full Circle or ReSkate.

## Setup

1. Install the [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases/latest). It
   lets GyroCam act as an Xbox controller.
2. Download `GyroCam-bridge.exe` and the APK from [Releases](../../releases).
3. Run the exe on your PC. If SmartScreen warns, click **More info → Run anyway**. Allow it
   through the firewall on **Private** networks.
4. Install the APK on your phone and open it on the same Wi-Fi. It connects automatically.

## Filming

1. Open a replay in skate. and switch to the free camera.
2. Point your phone at the screen, tap **RECENTER**, then **START TRACKING**.
3. Tap **PLAY (A)** to resume the replay.

| Control | What it does |
|---------|--------------|
| Move the phone | Aims the camera |
| LOOK stick | Aims quickly, on top of the gyro |
| MOVE stick / UP/DN | Forward/Rewinds the replay |
| SENS − / + | Turn more or less per phone movement |
| FOV slider | Sets the game's field of view. **SYNC** fixes it if it gets out of step |
| RECENTER (Vol+) | Camera returns home. **Hold** to save the current view as home |
| Vol− | Tracking on/off |

## Tuning (once)

With skate. focused and the free camera on, use these keys in the bridge window:

- **`d` — deadzone:** run this if slow moves stick and then jump. Switch to the game and press
  **F8** the moment the camera starts turning.
- **`c` — calibrate:** sends one full turn. Press `+` or `-` until it lands back where it
  started.

Everything else (smoothness, speeds, buttons) is in `config.json` next to the exe. See
[docs/REFERENCE.md](docs/REFERENCE.md).

## Modding

All the code is here: the phone app in `android/` and the PC bridge in `pc-bridge/`. Fork it
and build on it. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

[MIT License](LICENSE)

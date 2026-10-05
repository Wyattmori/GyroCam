# Third-party software

`GyroCam-bridge.exe` bundles the following. Each keeps its own license.

| Component | License | Source |
|-----------|---------|--------|
| Python runtime and standard library | PSF License | https://www.python.org/ |
| vgamepad | MIT | https://github.com/yannbouteiller/vgamepad |
| ViGEmClient.dll (inside vgamepad) | MIT | https://github.com/nefarius/ViGEmClient |
| ViGEmBus setup files (inside vgamepad; GyroCam doesn't run them) | BSD-3-Clause | https://github.com/nefarius/ViGEmBus |
| PyInstaller bootloader | GPL-2.0 with the PyInstaller bootloader exception (allows distribution with any program) | https://pyinstaller.org/ |

The ViGEmBus driver itself is not installed by GyroCam. Users install it from its official
releases page.

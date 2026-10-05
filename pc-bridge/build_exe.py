"""
Builds dist/GyroCam-bridge.exe: the bridge with Python and vgamepad built in, so users
don't need to install Python. They still need the ViGEmBus driver (the exe tells them).

    pip install pyinstaller vgamepad
    python pc-bridge/build_exe.py
"""
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, "build")


def png_to_ico(png_path, ico_path):
    """An .ico may embed a PNG directly (Vista+), so no image library is needed."""
    data = open(png_path, "rb").read()
    width, height = struct.unpack(">II", data[16:24])
    entry = struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, 1, 32, len(data), 6 + 16)
    with open(ico_path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, 1) + entry + data)


def main():
    os.makedirs(WORK, exist_ok=True)
    ico = os.path.join(WORK, "gyrocam.ico")
    png_to_ico(os.path.join(ROOT, "icon.png"), ico)
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--console", "--name", "GyroCam-bridge", "--icon", ico,
        # ViGEmClient.dll ships inside the vgamepad package; collect it with the code.
        "--collect-all", "vgamepad",
        "--distpath", os.path.join(ROOT, "dist"), "--workpath", WORK, "--specpath", WORK,
        os.path.join(HERE, "gyrocam_bridge.py"),
    ])
    print("\nBuilt", os.path.join(ROOT, "dist", "GyroCam-bridge.exe"))


if __name__ == "__main__":
    main()

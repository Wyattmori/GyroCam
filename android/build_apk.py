"""
Builds GyroCam.apk without Gradle or Android Studio, using only the JDK plus
Android build-tools 34 and the android-34 platform.

  python build_apk.py                   # uses ANDROID_HOME / ANDROID_SDK_ROOT or ./.sdk
  python build_apk.py --download-sdk    # first fetches build-tools + platform (~120 MB)
                                        # from dl.google.com into ./.sdk (Android SDK license applies)
  python build_apk.py --install         # also installs to a USB-connected phone via adb

Output: build/GyroCam.apk (debug-signed, fine for sideloading).
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
LOCAL_SDK = os.path.join(HERE, ".sdk")
REPO = "https://dl.google.com/android/repository/"
PACKAGES = [
    # (zip, sha1, destination inside the SDK, top-level folder inside the zip)
    ("build-tools_r34-windows.zip", "62cfde1b6fcc3ad12a4d2ba1b537e752768bfd47",
     os.path.join("build-tools", "34.0.0"), "android-14"),
    ("platform-34-ext7_r03.zip", "1f2e9478d6a7601425ceaa553311dc43191f103d",
     os.path.join("platforms", "android-34"), "android-34"),
]
EXE = ".exe" if os.name == "nt" else ""
BAT = ".bat" if os.name == "nt" else ""


def run(*cmd):
    print(">", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    subprocess.check_call(list(cmd))


def jdk_tool(name):
    """javac/keytool may not be on PATH (Oracle's installer only adds java shims)."""
    found = shutil.which(name)
    if found:
        return found
    homes = [os.environ.get("JAVA_HOME", "")]
    try:
        out = subprocess.run(["java", "-XshowSettings:properties", "-version"],
                             capture_output=True, text=True).stderr
        homes += [l.split("=", 1)[1].strip() for l in out.splitlines() if "java.home =" in l]
    except OSError:
        pass
    for home in homes:
        cand = os.path.join(home, "bin", name + EXE)
        if home and os.path.exists(cand):
            return cand
    sys.exit(f"Cannot find {name}; install a JDK or set JAVA_HOME.")


def download_sdk():
    os.makedirs(LOCAL_SDK, exist_ok=True)
    for name, sha1, dest, top in PACKAGES:
        dest = os.path.join(LOCAL_SDK, dest)
        if os.path.isdir(dest):
            continue
        archive = os.path.join(LOCAL_SDK, name)
        if not os.path.exists(archive):
            print(f"Downloading {REPO + name} ...")
            urllib.request.urlretrieve(REPO + name, archive)
        h = hashlib.sha1(open(archive, "rb").read()).hexdigest()
        if h != sha1:
            os.remove(archive)
            sys.exit(f"Checksum mismatch for {name}")
        tmp = os.path.join(LOCAL_SDK, "_unzip")
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(os.path.join(tmp, top), dest)
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(archive)


def find_sdk():
    for cand in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"), LOCAL_SDK,
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")):
        if cand and glob.glob(os.path.join(cand, "build-tools", "*", "aapt2" + EXE)) \
                and glob.glob(os.path.join(cand, "platforms", "android-*", "android.jar")):
            return cand
    return None


def newest(pattern):
    def key(p):
        parts = os.path.basename(os.path.dirname(p) if os.path.isfile(p) else p).replace("android-", "").split(".")
        return [int(x) if x.isdigit() else 0 for x in parts]
    return sorted(glob.glob(pattern), key=key)[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-sdk", action="store_true")
    ap.add_argument("--install", action="store_true")
    args = ap.parse_args()

    if args.download_sdk:
        download_sdk()
    sdk = find_sdk()
    if not sdk:
        sys.exit("Android SDK not found. Re-run with --download-sdk, or set ANDROID_HOME.")
    bt = os.path.dirname(newest(os.path.join(sdk, "build-tools", "*", "aapt2" + EXE)))
    android_jar = os.path.join(os.path.dirname(newest(os.path.join(sdk, "platforms", "android-*", "android.jar"))),
                               "android.jar")
    print(f"SDK: {sdk}\nbuild-tools: {bt}\nplatform: {android_jar}")

    shutil.rmtree(BUILD, ignore_errors=True)
    gen, classes, dex = (os.path.join(BUILD, d) for d in ("gen", "classes", "dex"))
    for d in (gen, classes, dex):
        os.makedirs(d)

    res_zip = os.path.join(BUILD, "res.zip")
    run(os.path.join(bt, "aapt2" + EXE), "compile", "--dir", os.path.join(HERE, "res"), "-o", res_zip)
    unsigned = os.path.join(BUILD, "unsigned.apk")
    run(os.path.join(bt, "aapt2" + EXE), "link", "-o", unsigned, "-I", android_jar,
        "--manifest", os.path.join(HERE, "AndroidManifest.xml"), "--java", gen,
        "--min-sdk-version", "21", "--target-sdk-version", "34", res_zip)

    sources = glob.glob(os.path.join(HERE, "src", "**", "*.java"), recursive=True) + \
        glob.glob(os.path.join(gen, "**", "*.java"), recursive=True)
    # --release 8 supplies java.* (incl. the lambda bootstrap android.jar lacks); d8 desugars it.
    run(jdk_tool("javac"), "--release", "8", "-Xlint:-options", "-encoding", "UTF-8",
        "-classpath", android_jar, "-d", classes, *sources)
    classes_jar = os.path.join(BUILD, "classes.jar")
    with zipfile.ZipFile(classes_jar, "w") as z:
        for f in glob.glob(os.path.join(classes, "**", "*.class"), recursive=True):
            z.write(f, os.path.relpath(f, classes).replace(os.sep, "/"))
    run(os.path.join(bt, "d8" + BAT), "--release", "--min-api", "21", "--lib", android_jar,
        "--output", dex, classes_jar)

    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(dex, "classes.dex"), "classes.dex")

    aligned = os.path.join(BUILD, "aligned.apk")
    run(os.path.join(bt, "zipalign" + EXE), "-f", "-p", "4", unsigned, aligned)

    keystore = os.path.join(HERE, "debug.keystore")
    if not os.path.exists(keystore):
        run(jdk_tool("keytool"), "-genkeypair", "-keystore", keystore, "-storepass", "android", "-keypass", "android",
            "-alias", "gyrocam", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=GyroCam Debug")
    final = os.path.join(BUILD, "GyroCam.apk")
    run(os.path.join(bt, "apksigner" + BAT), "sign", "--ks", keystore, "--ks-pass", "pass:android",
        "--ks-key-alias", "gyrocam", "--out", final, aligned)
    print(f"\nBuilt {final}")

    if args.install:
        adb = os.path.join(sdk, "platform-tools", "adb" + EXE)
        run(adb if os.path.exists(adb) else "adb", "install", "-r", final)


if __name__ == "__main__":
    main()

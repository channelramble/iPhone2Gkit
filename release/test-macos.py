#!/usr/bin/env python3
"""Validate the actual release archive on a native Mac, without phone actions.

Downloads and image builds stay in a temporary user profile. This checks the
Monterey deployment targets; it does not substitute for running macOS 12.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import tempfile


MACHO = {bytes.fromhex(x) for x in (
    "feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca",
    "cafebabf", "bfbafeca")}


def run(args, *, env=None, timeout=90, allowed=(0,)):
    result = subprocess.run([str(a) for a in args], env=env, cwd="/",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout)
    if result.returncode not in allowed:
        raise RuntimeError(f"{args[0]} exited {result.returncode}:\n{result.stdout}")
    return result.stdout


def reply(text):
    return json.loads(next(line for line in reversed(text.splitlines())
                           if line.strip() and not line.startswith("@@")))


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(data)
    return sha.hexdigest()


def verify_macho(app):
    count = 0
    for path in sorted(app.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in MACHO:
                continue
        # Source archives/objects are not loaded. All shipped executable code,
        # including Python extension modules, must work on both native hosts.
        if "ThirdPartySources" in path.parts:
            continue
        arches = set(run(["/usr/bin/lipo", "-archs", path]).split())
        assert {"arm64", "x86_64"} <= arches, f"Not universal: {path}: {arches}"
        headers = run(["/usr/bin/otool", "-arch", "all", "-l", path])
        versions = re.findall(r"cmd LC_(?:BUILD_VERSION|VERSION_MIN_MACOSX)\b(.*?)(?=\nLoad command|\Z)",
                              headers, re.S)
        assert len(versions) >= 2, f"Missing deployment targets: {path}"
        for block in versions:
            match = re.search(r"\b(?:minos|version)\s+(\d+\.\d+(?:\.\d+)?)", block)
            assert match, f"Unreadable deployment target: {path}"
            target = tuple(int(v) for v in match[1].split("."))
            assert target <= (12, 0, 0), f"Requires newer than Monterey: {path}: {target}"
        dependencies = run(["/usr/bin/otool", "-arch", "all", "-L", path])
        for line in dependencies.splitlines():
            if not line.startswith("\t"):
                continue
            name = line.strip().split(" (", 1)[0]
            assert name.startswith(("/usr/lib/", "/System/Library/", "@")), \
                f"External dependency in {path}: {name}"
        count += 1
    assert count, "No Mach-O runtime found"
    print(f"Checked {count} universal Mach-O files; deployment targets <= macOS 12.0.", flush=True)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--arch", required=True, choices=("arm64", "x86_64"))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--downloads", action="store_true")
    args = parser.parse_args()
    assert platform.system() == "Darwin" and platform.machine() == args.arch
    assert re.fullmatch(r"[0-9a-f]{64}", args.sha256)
    assert digest(args.archive) == args.sha256, "Candidate checksum mismatch"
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    print(f"Native {args.arch}, macOS {platform.mac_ver()[0]}; archive {args.sha256}", flush=True)

    with tempfile.TemporaryDirectory(prefix="iphone2gkit-release-") as temporary:
        work = Path(temporary)
        run(["/usr/bin/ditto", "-x", "-k", args.archive.resolve(), work / "unpacked"])
        apps = list((work / "unpacked").glob("*.app"))
        assert len(apps) == 1, "Expected one app in the ZIP"
        app = apps[0]
        resources = app / "Contents/Resources"
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
        with (app / "Contents/Info.plist").open("rb") as source:
            info = plistlib.load(source)
        assert info["LSMinimumSystemVersion"] == "12.0"
        assert verify_macho(app) >= 8, "Incomplete GUI/Python/native runtime"
        assert (resources / "engine/kit/resources/legacy-kit-manifest.json").is_file()
        assert not list(app.rglob("*.ipsw")), "Public app must download stock firmware separately"
        assert not (resources / "kit-assets").exists(), "Private kit leaked into public app"

        home = work / "profile"
        home.mkdir()
        env = {"HOME": str(home), "TMPDIR": str(work),
               "PATH": f"{resources}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        python = resources / "python/bin/python3"
        cli = resources / "bin/iphone2gkit"
        runtime = run([python, "-B", "-c",
                       "import ssl,sqlite3,ctypes,zlib,bz2,lzma,hashlib,platform;print(platform.machine())"], env=env)
        assert runtime.strip() == args.arch, runtime
        run([cli, "--help"], env=env)
        for name in ("irecovery", "idevicerestore", "ideviceinfo", "idevice_id"):
            run([resources / "bin" / name, "--help"], env=env)
        doctor = reply(run([cli, "doctor", "--json"], env=env, allowed=(0, 1)))
        assert doctor["irecovery"] and doctor["setup"]["ready"] is False, doctor
        assert doctor["kit"] is None, "Fresh profile unexpectedly found a kit"
        print("Fresh-profile CLI, private Python extensions and native tools passed.", flush=True)

        # Uses the app's own view capture. No screen-recording privilege needed.
        screenshot = evidence / f"macos-{args.arch}-setup.png"
        gui_env = dict(env, IOS1KIT_SNAPSHOT=str(screenshot), IOS1KIT_SNAPSHOT_DELAY="8")
        run([app / "Contents/MacOS" / info["CFBundleExecutable"]], env=gui_env, timeout=60)
        assert screenshot.is_file() and screenshot.stat().st_size > 1000
        print("Native GUI launched and saved its first-run window.", flush=True)

        if args.downloads:
            setup = reply(run([cli, "setup"], env=env, timeout=1200))
            assert setup["ready"] is True and setup["verified"] is True and setup["apps"] == 48, setup
            doctor = reply(run([cli, "doctor", "--json"], env=env))
            assert doctor["ok"] is True and doctor["setup"]["ready"] is True, doctor
            apps = reply(run([cli, "list", "--json"], env=env))
            assert len(apps) == 48 and sum(bool(a["in_apps"]) for a in apps) == 42
            link = work / "terminal-command"
            link.symlink_to(cli)
            assert reply(run([link, "doctor", "--json"], env=env))["ok"] is True
            for selection in (["--mode", "probe"], ["--apps", "apps", "--no-activate"]):
                output = work / ("probe" if "probe" in selection else "apps")
                run([cli, "build", "--out", output, "--ramdisk-mb", "14", *selection],
                    env=env, timeout=600)
                assert list(output.glob("*.bin")), "No ramdisk output"
            print("Downloaded setup, verified 48 packs and built probe + all 42 app images.", flush=True)
        # Host-only builds/downloads must not invalidate the application signature.
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
    print("macOS package validation passed; no phone actions performed.", flush=True)


if __name__ == "__main__":
    main()

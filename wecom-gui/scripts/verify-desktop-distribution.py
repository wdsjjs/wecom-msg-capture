#!/usr/bin/env python3
"""Verify a relocated app with isolated state and no developer environment."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def verify_native_dependencies(app: Path, minimum_os: str):
    magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
    for path in app.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            if handle.read(4) not in magic:
                continue
        dependencies = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout
        for line in dependencies.splitlines():
            if not line.startswith("\t"):
                continue
            dependency = line.strip().split(" (", 1)[0]
            assert dependency.startswith(("@", "/System/Library/", "/usr/lib/")), (
                f"External native dependency in {path.name}: {dependency}")
        build = subprocess.run(["vtool", "-show-build", str(path)], capture_output=True, text=True, check=True).stdout
        for line in build.splitlines():
            parts = line.split()
            if parts and parts[0] == "minos":
                version = tuple(map(int, parts[1].split(".")))
                assert version <= tuple(map(int, minimum_os.split("."))), f"Unsupported minimum OS: {path}"


def verify_package(package: Path):
    with tempfile.TemporaryDirectory(prefix="wecom-installer-check-") as temporary:
        expanded = Path(temporary) / "expanded"
        subprocess.run(["pkgutil", "--expand-full", str(package), str(expanded)], check=True)
        distribution = ET.parse(expanded / "Distribution").getroot()
        options = distribution.find("options")
        assert options is not None and options.get("hostArchitectures") in {"arm64", "x86_64"}
        assert distribution.find(".//os-version[@min='14.0']") is not None
        components = list(expanded.glob("*/PackageInfo"))
        assert len(components) == 1
        package_info = ET.parse(components[0]).getroot()
        assert package_info.get("install-location") == "/Applications"
        component = components[0].parent
        assert (component / "Scripts/preinstall").is_file()
        app = component / "Payload/WeCom Capture.app"
        verify(app.resolve())
        print("PASS: installer payload, fixed Applications destination, platform requirements and upgrade guard")


def verify(source: Path):
    with tempfile.TemporaryDirectory(prefix="wecom-clean-install-") as temporary:
        base = Path(temporary).resolve()
        app = base / source.name
        shutil.copytree(source, app, symlinks=True)
        resources = app / "Contents/Resources"
        gui = resources / "wecom-gui"
        with (app / "Contents/Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        assert info.get("LSUIElement") is False
        verify_native_dependencies(app, info["LSMinimumSystemVersion"])
        assert (resources / (info["CFBundleIconFile"] + ".icns")).is_file()
        assert not list(app.rglob(".env.local"))
        assert not list(app.rglob("*.sqlite"))
        for path in app.rglob("*"):
            if path.is_symlink():
                assert path.resolve().is_relative_to(app), f"External symlink: {path}"
        env = {"HOME": str(base / "home"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
               "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": str(gui) + ":" + str(resources / "site-packages"),
               "WECOM_GUI_PYTHON": str(resources / "python/bin/python3"),
               "WECOM_GUI_PYTHONPATH": str(gui) + ":" + str(resources / "site-packages"),
               "WECOM_DESKTOP_CONFIG": str(base / "device.json"),
               "WECOM_CHANNEL_ENV": "/dev/null", "WECOM_GUI_STATE_DIR": str(base / "state"),
               "WECOM_GUI_RUN_DIR": str(base / "logs")}
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        result = subprocess.run([str(gui / "scripts/wecom-control"), "runtime-status"],
                                cwd=base, env=env, capture_output=True, text=True, timeout=30, check=True)
        report = json.loads(result.stdout)
        assert isinstance(report, dict)
        assert (base / "state/state.sqlite").is_file()
        subprocess.run([env["WECOM_GUI_PYTHON"], "-c", "import ssl,sqlite3,requests,click"],
                       cwd=base, env=env, check=True, timeout=30)
        for helper in ("ax_wecom", "vision_ocr"):
            assert os.access(resources / "helpers" / helper, os.X_OK)
        # Running telemetry must not mutate the signed application bundle.
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        print("PASS: relocated runtime, isolated state, immutable bundle, Dock metadata and bundled helpers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Application bundle or PKG installer")
    artifact = parser.parse_args().artifact.resolve()
    if artifact.suffix == ".pkg":
        verify_package(artifact)
    else:
        verify(artifact)

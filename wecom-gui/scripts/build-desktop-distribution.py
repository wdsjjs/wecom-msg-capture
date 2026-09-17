#!/usr/bin/env python3
"""Build a relocatable macOS app and installer without copying local credentials."""

import argparse
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.1.0"
MINIMUM_OS = "14.0"


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def build(output: Path, python: Path, identity: str, installer_identity: str):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError(f"Choose an empty output directory: {output}")
    arch = platform.machine()
    runtime_arch = run(python, "-I", "-c", "import platform; print(platform.machine())",
                       capture_output=True, text=True).stdout.strip()
    if runtime_arch != arch:
        raise ValueError(f"Python architecture {runtime_arch} does not match build host {arch}")
    with tempfile.TemporaryDirectory(prefix="wecom-distribution-") as tmp:
        stage = Path(tmp)
        payload = stage / "payload"
        app = payload / "WeCom Capture.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        gui = resources / "wecom-gui"
        helpers = resources / "helpers"
        (contents / "MacOS").mkdir(parents=True)
        helpers.mkdir(parents=True)
        # uv-managed Python is relocatable; system/Homebrew Python is not.
        runtime = python.resolve().parent.parent
        if not (runtime / "lib" / "libpython3.12.dylib").exists():
            raise ValueError("Use a standalone CPython 3.12 runtime (uv python install 3.12)")
        shutil.copytree(runtime, resources / "python", symlinks=True)
        # Runtime libraries may carry a managed absolute path or a bare install
        # ID. Keep every ID relative to the bundled library directory.
        libraries = resources / "python/lib"
        for library in libraries.rglob("*.dylib"):
            if not library.is_symlink():
                run("install_name_tool", "-id", "@rpath/" + str(library.relative_to(libraries)), library)
        tracked = run("git", "ls-files", "-z", "wecom-gui",
                      cwd=ROOT, capture_output=True).stdout.decode().split("\0")
        for name in filter(None, tracked):
            relative = Path(name)
            if "/tests/" in name or name.endswith(".env.example"):
                continue
            if relative.parts[0] == "wecom-gui" and relative.parts[1] not in {"cli_anything", "scripts"}:
                continue
            source = ROOT / relative
            if not source.is_file():
                continue
            target = resources / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        run("uv", "pip", "install", "--python", python, "--target", resources / "site-packages",
            "click==8.1.8", "requests==2.32.5", "certifi==2026.7.22",
            "charset-normalizer==3.5.1", "idna==3.19", "urllib3==2.8.0")
        for source, target in [
            (ROOT / "wecom-gui/desktop-client/WeComCapture.swift", contents / "MacOS/WeComCapture"),
            (gui / "cli_anything/wecom_gui/scripts/ax_wecom.swift", helpers / "ax_wecom"),
            (gui / "cli_anything/wecom_gui/scripts/vision_ocr.swift", helpers / "vision_ocr"),
        ]:
            run("swiftc", "-O", "-target", f"{arch}-apple-macosx{MINIMUM_OS}", source, "-o", target)
        with (ROOT / "wecom-gui/desktop-client/Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        iconset = stage / "AppIcon.iconset"
        run("swift", ROOT / "wecom-gui/desktop-client/BuildIcon.swift", iconset)
        run("iconutil", "-c", "icns", iconset, "-o", resources / "AppIcon.icns")
        info.update(CFBundleShortVersionString=VERSION, CFBundleVersion="110", CFBundleIconFile="AppIcon",
                    LSMinimumSystemVersion=MINIMUM_OS,
                    NSAppleEventsUsageDescription="用于操作企业微信窗口并执行已确认的消息发送。")
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump(info, handle)
        # Sign nested Mach-O objects before sealing the application resources.
        magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
        for path in contents.rglob("*"):
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as handle:
                    macho = handle.read(4) in magic
                if macho:
                    run("codesign", "--force", "--sign", identity, *([] if identity == "-" else ["--options", "runtime", "--timestamp"]), path)
        run("codesign", "--force", "--sign", identity,
            *([] if identity == "-" else ["--options", "runtime", "--timestamp"]), app)
        run("codesign", "--verify", "--deep", "--strict", app)
        destination = output / app.name
        if destination.exists():
            raise FileExistsError(f"Choose an empty output directory: {destination}")
        component_plist = stage / "components.plist"
        with component_plist.open("wb") as handle:
            plistlib.dump([{"RootRelativeBundlePath": app.name, "BundleIsRelocatable": False,
                           "BundleOverwriteAction": "upgrade", "BundleHasStrictIdentifier": True}], handle)
        install_scripts = stage / "installer-scripts"
        install_scripts.mkdir()
        preinstall = install_scripts / "preinstall"
        shutil.copy2(ROOT / "wecom-gui/desktop-client/installer/preinstall", preinstall)
        preinstall.chmod(0o755)
        component = stage / "WeCom-Capture-Component.pkg"
        run("pkgbuild", "--root", payload, "--component-plist", component_plist, "--install-location", "/Applications",
            "--scripts", install_scripts,
            "--identifier", "org.wecomcapture.desktop", "--version", VERSION, component)
        requirements = stage / "requirements.plist"
        with requirements.open("wb") as handle:
            plistlib.dump({"os": [MINIMUM_OS], "arch": [arch], "home": False}, handle)
        distribution = stage / "Distribution.xml"
        run("productbuild", "--synthesize", "--product", requirements, "--package", component, distribution)
        run("productbuild", "--distribution", distribution, "--package-path", stage,
            *(["--sign", installer_identity, "--timestamp"] if installer_identity else []),
            output / "WeCom-Capture-Agent.pkg")
        shutil.copytree(app, destination, symlinks=True)
        print(f"Built {destination}")
        if identity == "-" or not installer_identity:
            print("LOCAL TEST BUILD: not Developer ID signed/notarized; not ready for customer distribution.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--identity", default="-")
    parser.add_argument("--installer-identity", default="")
    args = parser.parse_args()
    build(args.output.resolve(), args.python.resolve(), args.identity, args.installer_identity)

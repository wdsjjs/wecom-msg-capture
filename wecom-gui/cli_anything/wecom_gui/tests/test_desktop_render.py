import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def test_native_desktop_repaints_without_reallocating_labels_or_intercepting_controls(tmp_path):
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        pytest.skip("Native desktop rendering requires macOS and Swift")
    source = Path(__file__).resolve().parents[3] / "desktop-client" / "WeComCapture.swift"
    binary = tmp_path / "desktop-render-test"
    subprocess.run([
        "swiftc", "-D", "DESKTOP_RENDER_TEST", "-framework", "AppKit", "-framework", "Foundation",
        "-framework", "ApplicationServices", str(source), "-o", str(binary),
    ], check=True, capture_output=True, timeout=90)
    result = subprocess.run([str(binary), "4000", str(tmp_path / "desktop-render.png")],
                            check=True, capture_output=True, text=True, timeout=90)
    report = json.loads(result.stdout)
    assert report["frames"] == 4000
    assert report["max_text_controls"] < 40
    assert report["nonblank"] and report["control_click"] and report["breathing"]

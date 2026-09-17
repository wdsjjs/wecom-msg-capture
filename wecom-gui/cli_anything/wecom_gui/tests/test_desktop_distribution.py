import json

import pytest

from cli_anything.wecom_gui.wecom_gui_cli import _load_env_local
from cli_anything.wecom_gui.utils import macos_backend


def test_desktop_config_loads_only_device_fields(monkeypatch, tmp_path):
    config = tmp_path / "device.json"
    config.write_text(json.dumps({"WECOM_CHANNEL_BASE_URL": "https://example.com",
                                  "WECOM_CHANNEL_DEVICE_ID": "test-device",
                                  "WECOM_CHANNEL_DEVICE_TOKEN": "test-token",
                                  "WECOM_GUI_PYTHON": "/untrusted/python"}))
    monkeypatch.setenv("WECOM_DESKTOP_CONFIG", str(config))
    monkeypatch.setenv("WECOM_GUI_PYTHON", "/bundled/python")
    monkeypatch.setenv("WECOM_CHANNEL_DEVICE_TOKEN", "stale-token")
    _load_env_local(tmp_path / ".env.local")
    import os
    assert os.environ["WECOM_CHANNEL_DEVICE_TOKEN"] == "test-token"
    assert os.environ["WECOM_GUI_PYTHON"] == "/bundled/python"


def test_desktop_config_never_loads_a_sibling_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("WECOM_DESKTOP_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.delenv("WECOM_CHANNEL_DEVICE_TOKEN", raising=False)
    env_file = tmp_path / ".env.local"
    env_file.write_text("WECOM_CHANNEL_DEVICE_TOKEN=untrusted\n")
    _load_env_local(env_file)
    import os
    assert "WECOM_CHANNEL_DEVICE_TOKEN" not in os.environ


def test_ax_helper_runs_without_swift_on_customer_machine(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("WECOM_GUI_AX_HELPER", "/bundled/ax_wecom")
    monkeypatch.setattr(macos_backend.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(macos_backend.subprocess, "run", lambda command, **kw:
                        calls.append(command) or SimpleNamespace(returncode=0, stdout='{"ok":true}\n'))
    assert macos_backend._swift_ax("send-ready") == [{"ok": True}]
    assert calls == [["/bundled/ax_wecom", "send-ready"]]


def test_ocr_helper_runs_without_swift_on_customer_machine(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("WECOM_GUI_OCR_HELPER", "/bundled/vision_ocr")
    monkeypatch.setattr(macos_backend.shutil, "which", lambda name: None)
    calls = []
    monkeypatch.setattr(macos_backend.subprocess, "run", lambda command, **kw:
                        calls.append(command) or SimpleNamespace(returncode=0, stdout=""))
    assert macos_backend._vision_ocr_lines("/tmp/test.png") == []
    assert calls == [["/bundled/vision_ocr", "/tmp/test.png"]]

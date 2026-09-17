from __future__ import annotations

import os

import pytest
import requests


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("WECOM_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("WECOM_GUI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WECOM_GUI_RUN_DIR", str(tmp_path / "logs"))

    def reject_network(*args, **kwargs):
        raise AssertionError("Tests must mock HTTP transport")

    monkeypatch.setattr(requests.sessions.Session, "send", reject_network)


@pytest.fixture(autouse=True)
def _disable_real_wecom_uid_lookup(monkeypatch):
    monkeypatch.setattr(
        "cli_anything.wecom_gui.utils.macos_backend.current_external_user_id",
        lambda: "",
    )

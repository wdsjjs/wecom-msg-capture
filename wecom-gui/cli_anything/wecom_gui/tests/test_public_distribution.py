from pathlib import Path
import plistlib
import re
import subprocess
from urllib.parse import urlsplit

from click.testing import CliRunner

from cli_anything.wecom_gui.core import state
from cli_anything.wecom_gui.wecom_gui_cli import cli


ROOT = Path(__file__).resolve().parents[4]


def test_transport_storage_contains_only_identity_table_before_runtime_initialization():
    state.bind_wecom_customer(uid="example-person", customer_name="Example Person")
    assert (
        state.lookup_wecom_customer(uid="example-person")["customer_name"]
        == "Example Person"
    )
    assert len(state.list_wecom_customer_bindings()) == 1
    with state.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert tables == {"wecom_customer_bindings"}


def test_cli_loads_without_optional_business_modules():
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0
    assert set(cli.commands) == {
        "doctor",
        "app",
        "inbox",
        "chat",
        "reply",
        "wecom",
        "edge-channel",
        "supervisor",
        "runtime",
    }
    result = CliRunner().invoke(
        cli, ["reply", "send", "--text", "Example", "--dry-run"]
    )
    assert result.exit_code == 0


def test_application_identity_and_default_cli_state_are_independent(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("WECOM_GUI_STATE_DIR")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert state.state_dir() == tmp_path / ".wecom-capture"
    with (ROOT / "wecom-gui/desktop-client/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleIdentifier"] == "org.wecomcapture.desktop"
    assert info["CFBundleExecutable"] == "WeComCapture"
    assert info["CFBundleName"] == "WeCom Capture"


def test_public_sources_have_no_runtime_artifacts_or_unreviewed_url_hosts():
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    reviewed_hosts = {
        "www.apple.com",
        "example.com",
        "example.test",
        "example.invalid",
        "channel.example.com",
        "localhost",
    }
    for name in filter(None, names):
        path = ROOT / name
        assert not path.is_symlink(), name
        assert path.name not in {".env.local", "device.json"}, name
        assert path.suffix not in {
            ".sqlite",
            ".sqlite3",
            ".db",
            ".log",
            ".pkg",
            ".pem",
            ".key",
        }, name
        content = path.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s\x22\x27<>`]+", content):
            host = urlsplit(url).hostname
            assert host is None or host in reviewed_hosts, (name, host)

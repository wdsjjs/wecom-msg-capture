from __future__ import annotations

import fcntl
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_anything.wecom_gui.core import edge_worker, recovery_state, state


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "state_dir", lambda: tmp_path)
    mocks = SimpleNamespace(
        open_row=Mock(),
        opened=Mock(return_value=True),
        input_ready=Mock(return_value={"ok": True, "input": {"valueLength": 0}}),
        read=Mock(return_value={"messages": []}),
        download=Mock(return_value=[]),
        send=Mock(),
        text_echo=Mock(return_value=True),
        media_echo=Mock(return_value=[{"capture_row_id": "sent-image", "match_key": "fixture"}]),
        command={"command_id": "send-gate-test", "conversation": {"title": "Test Customer"}, "text": "Test reply"},
    )
    monkeypatch.setattr(edge_worker.inbox, "open_row", mocks.open_row)
    monkeypatch.setattr(edge_worker, "_wait_for_opened_conversation", mocks.opened)
    monkeypatch.setattr(edge_worker.macos_backend, "send_input_ready", mocks.input_ready)
    monkeypatch.setattr(edge_worker.chat, "read_current", mocks.read)
    monkeypatch.setattr(edge_worker, "_download_attachments", mocks.download)
    monkeypatch.setattr(edge_worker.reply, "send_message", mocks.send)
    monkeypatch.setattr(edge_worker, "_wait_for_visible_outbound_reply", mocks.text_echo)
    monkeypatch.setattr(edge_worker, "_wait_for_media_command_echoes", mocks.media_echo)
    return mocks


@pytest.mark.parametrize("mode", ["recovery", "paused", "normal"])
def test_active_recovery_blocks_entry_before_gui(runtime, mode):
    recovery_state.request_start()
    if mode == "paused":
        recovery_state.request_pause()
    elif mode == "normal":
        recovery_state.request_normal()
    assert recovery_state.active()

    result = edge_worker.execute_command(object(), runtime.command)

    assert result == {"status": "precondition_failed", "reason": "history_recovery_requested"}
    for operation in (runtime.open_row, runtime.opened, runtime.input_ready, runtime.read,
                      runtime.download, runtime.send, runtime.text_echo, runtime.media_echo):
        operation.assert_not_called()


@pytest.mark.parametrize("has_media", [False, True])
def test_recovery_requested_during_download_prevents_submit(runtime, has_media):
    def download(client, command):
        recovery_state.request_start()
        return [{"type": "image", "path": "mock-image.png"}] if has_media else []

    runtime.download.side_effect = download
    result = edge_worker.execute_command(object(), runtime.command)

    assert recovery_state.current()["desired_mode"] == "recovery"
    assert result == {"status": "precondition_failed", "reason": "history_recovery_requested"}
    runtime.download.assert_called_once()
    runtime.send.assert_not_called()
    runtime.text_echo.assert_not_called()
    runtime.media_echo.assert_not_called()


@pytest.mark.parametrize("has_media", [False, True])
def test_normal_send_after_recovery_release_still_verifies_echo(runtime, has_media):
    job = recovery_state.request_start()
    recovery_state.request_normal()
    recovery_state.update(job["id"], release_pending=0, remote_active=0, phase="resumed")
    attachments = [{"type": "image", "path": "mock-image.png"}] if has_media else []
    runtime.download.return_value = attachments

    result = edge_worker.execute_command(object(), runtime.command)

    assert result["status"] == "succeeded"
    assert result["verification"] == "reply_visible"
    runtime.send.assert_called_once_with("Test reply", attachments=attachments, dry_run=False,
                                         submit=True, allow_clipboard_fallback=False)
    (runtime.media_echo if has_media else runtime.text_echo).assert_called_once()


@pytest.mark.parametrize("send_raises", [False, True])
def test_started_send_finishes_before_recovery_and_echo_still_runs(runtime, tmp_path, send_raises):
    physical_send_started = Event()
    finish_physical_send = Event()
    recovery_attempted = Event()
    recovery_requested = Event()
    order = []
    runtime.download.return_value = [{"type": "image", "path": "mock-image.png"}]

    def send(*args, **kwargs):
        physical_send_started.set()
        assert finish_physical_send.wait(5)
        order.append("send_finished")
        if send_raises:
            raise RuntimeError("mock error after submit")

    def request_recovery():
        recovery_attempted.set()
        job = recovery_state.request_start()
        order.append("recovery_requested")
        recovery_requested.set()
        return job

    def verify_echo(*args, **kwargs):
        # Recovery must be able to acquire the control lock before echo checks finish.
        assert recovery_requested.wait(5)
        assert recovery_state.active()
        order.append("echo_checked")
        return [{"capture_row_id": "sent-image", "match_key": "fixture"}]

    runtime.send.side_effect = send
    runtime.media_echo.side_effect = verify_echo
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(edge_worker.execute_command, object(), runtime.command)
        try:
            assert physical_send_started.wait(5)
            with (tmp_path / "recovery-control.lock").open("a+") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            requesting = pool.submit(request_recovery)
            assert recovery_attempted.wait(5)
            assert not recovery_requested.is_set()
            finish_physical_send.set()
            result = sending.result(timeout=5)
            assert requesting.result(timeout=5)["desired_mode"] == "recovery"
        finally:
            finish_physical_send.set()

    assert order == ["send_finished", "recovery_requested", "echo_checked"]
    assert result["status"] == "succeeded"
    assert result["verification"] == ("visible_after_send_error" if send_raises else "reply_visible")
    runtime.send.assert_called_once()
    runtime.media_echo.assert_called_once()

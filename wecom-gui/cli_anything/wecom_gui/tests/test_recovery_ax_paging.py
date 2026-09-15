"""Recovery contracts and native fixtures. Never connect to a running WeCom UI."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli_anything.wecom_gui.utils import macos_backend as backend


VIEWPORT = {"x": 300, "y": 100, "width": 700, "height": 500}


@pytest.fixture(autouse=True)
def forbid_live_ui(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Recovery tests must not touch live UI or capture/send messages")

    for name in ("_swift_ax", "activate_app", "run_osascript", "scroll_sidebar",
                 "capture_chat_images", "window_geometry", "_ax_chat_all_items"):
        monkeypatch.setattr(backend, name, forbidden)
    monkeypatch.setattr(backend, "_media_capture_skip_cached", lambda *args: False)
    token = backend._RECOVERY_INBOX_CURSOR.set(None)
    yield
    backend._RECOVERY_INBOX_CURSOR.reset(token)


@pytest.fixture(scope="module")
def native_helper(tmp_path_factory):
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        pytest.skip("Native AX fixtures require the macOS Swift SDK")
    binary = tmp_path_factory.mktemp("native-recovery-tests") / "ax_wecom"
    source = Path(backend.__file__).resolve().parents[1] / "scripts" / "ax_wecom.swift"
    compiled = subprocess.run(["swiftc", "-O", str(source), "-o", str(binary)],
                              capture_output=True, text=True, timeout=90, env=backend._swift_ax_env())
    assert compiled.returncode == 0, compiled.stderr
    return binary


def node(role, texts=(), *, depth=2, y=120, width=180, height=22):
    return {"role": role, "subrole": "", "texts": list(texts), "depth": depth,
            "x": 320 if depth else 300, "y": y, "width": width, "height": height,
            "hasRect": True, "selected": False}


def frame(start=0, stop=60, **kwargs):
    rows = [[node("AXRow", depth=0, width=700, height=60),
             node("AXStaticText", ["10:00"]), node("AXTextArea", [f"message {i}"])]
            for i in range(start, stop)]
    return {"rows": rows, "ids": [f"pid:launch:row-{i}" for i in range(start, stop)],
            "viewport": VIEWPORT, "visible": list(range(stop - start)), **kwargs}


def run_page(binary, tmp_path, action="latest", *, frames=None, cursor=None, kind="chat", size=20, **kwargs):
    fixture = tmp_path / "recovery.json"
    fixture.write_text(json.dumps({"kind": kind, "action": action, "size": size,
                                   "frames": frames or [frame()], "cursor": cursor, **kwargs}))
    # A new helper process on every call proves the cursor is not an in-memory offset.
    proc = subprocess.run([str(binary), "recovery-fixture", str(fixture)],
                          check=True, capture_output=True, text=True, timeout=10)
    return json.loads(proc.stdout)


def ids(page):
    return [row["captureRowId"] for row in page["rows"]]


def test_preview_requires_image_title_and_preview_controls(native_helper, tmp_path):
    fixture = tmp_path / 'previews.json'
    fixture.write_text(json.dumps([
        {'values': ['图片', '上一张', '保存到本地']}, {'values': ['Image', '放大']},
        {'values': []}, {'values': ['图片']}, {'values': ['放大', '关闭']}, {'values': ['这张图片', '保存到本地']},
        {'values': ['图片', ''], 'help': ['上一张', '保存到本地']},
    ]))
    proc = subprocess.run([str(native_helper), 'preview-evidence-fixture', str(fixture)],
                          check=True, capture_output=True, text=True, timeout=10)
    assert [json.loads(line)['ok'] for line in proc.stdout.splitlines()] == [True, True, False, False, False, False, True]


def test_preview_image_exceeds_bubble_limit_without_cropping_image_sides(native_helper, tmp_path):
    window = {'x': 400, 'y': 200, 'width': 640, 'height': 480}
    rect = {'x': 399, 'y': 231, 'width': 642, 'height': 402}
    fixture = tmp_path / 'preview-bounds.json'
    fixture.write_text(json.dumps([
        {'rect': rect, 'window': window},
        {'rect': {'x': 400, 'y': 200, 'width': 1280, 'height': 900},
         'window': {'x': 400, 'y': 180, 'width': 1440, 'height': 1000}},
        {'rect': rect, 'window': window, 'role': 'AXGroup'},
        {'rect': {**rect, 'x': 2000}, 'window': window},
    ]))
    proc = subprocess.run([str(native_helper), 'preview-evidence-fixture', str(fixture)],
                          check=True, capture_output=True, text=True, timeout=10)
    results = [json.loads(line) for line in proc.stdout.splitlines()]
    assert {k: results[0][k] for k in ('x', 'y', 'width', 'height')} == {**rect, 'x': 400, 'width': 640}
    assert results[1]['width'] == 1280 and results[1]['height'] == 900
    assert results[2:] == [{}, {}]


def test_chat_walks_older_then_replays_newer_with_overlap(native_helper, tmp_path):
    latest = run_page(native_helper, tmp_path, frames=[frame(bottomVerified=True)])
    assert latest["ok"] and latest["at_latest"] and not latest["at_start"]
    assert ids(latest) == frame()["ids"][-20:]
    older = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"])
    assert older["ok"] and older["moved"] and not older["gap"]
    assert ids(older) == frame()["ids"][25:45]
    assert ids(older)[-5:] == ids(latest)[:5]
    assert older["fixture_scrolls"] == [{"target_id": frame()["ids"][25], "direction": "up", "edge": False}]
    newer = run_page(native_helper, tmp_path, "newer", cursor=older["cursor"], frames=[frame(bottomVerified=True)])
    assert ids(newer) == ids(latest)
    assert newer["progress_token"] == latest["progress_token"]
    assert newer["at_latest"]
    assert newer["fixture_scrolls"][0]["direction"] == "down"


def test_history_prepend_relocates_cursor_and_loads_older(native_helper, tmp_path):
    original = frame(20, 40)
    latest = run_page(native_helper, tmp_path, frames=[original])
    loaded = frame(5, 40)
    older = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"], frames=[original, loaded])
    assert older["ok"] and older["moved"] and not older["gap"]
    assert ids(older) == loaded["ids"][:20]
    assert ids(older)[-5:] == ids(latest)[:5]
    assert older["fixture_scrolls"][0]["edge"] is True
    assert older["reason"] == "history_boundary_unverified"
    assert older["at_start"] is False


def test_lazy_prepend_reveals_relocated_page_before_returning(native_helper, tmp_path):
    original = frame(20, 40)
    latest = run_page(native_helper, tmp_path, frames=[original])
    loaded = frame(0, 40, visible=list(range(20, 30)))
    revealed = frame(0, 40, visible=list(range(5, 15)))
    older = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"],
                     frames=[original, loaded, revealed])
    assert older["ok"] and older["moved"] and not older["gap"]
    assert ids(older) == revealed["ids"][5:25]
    assert len(older["fixture_scrolls"]) == 2
    assert older["fixture_scrolls"][-1] == {"target_id": revealed["ids"][5], "direction": "up", "edge": False}


def test_lazy_prepend_followup_keeps_scroll_attempts_bounded(native_helper, tmp_path):
    original = frame(20, 40)
    latest = run_page(native_helper, tmp_path, frames=[original])
    loaded = frame(0, 40, visible=list(range(20, 30)))
    older = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"], frames=[original, loaded])
    assert older["gap"] and older["reason"] == "target_row_not_visible"
    assert len(older["fixture_scrolls"]) == 3


def test_lazy_prepend_followup_rejects_conversation_changes(native_helper, tmp_path):
    original = frame(20, 40)
    latest = run_page(native_helper, tmp_path, frames=[original])
    loaded = frame(0, 40, visible=list(range(20, 30)))
    changed = frame(0, 40, conversationID="different-chat")
    older = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"], frames=[original, loaded, changed])
    assert not older["ok"] and older["reason"] == "conversation_changed"
    assert older["rows"] == [] and older["cursor"] is None


def test_current_preserves_exact_page_after_prepend_append_and_geometry_change(native_helper, tmp_path):
    latest = run_page(native_helper, tmp_path, frames=[frame(20, 40)])
    expanded = frame(0, 45)
    for row in expanded["rows"]:
        for item in row:
            item["y"] -= 500
    expanded["visible"] = []
    current = run_page(native_helper, tmp_path, "current", cursor=latest["cursor"], frames=[expanded])
    assert current["ok"] and not current["moved"]
    assert ids(current) == ids(latest)
    assert current["progress_token"] == latest["progress_token"]
    assert current["cursor"]["anchors"] == latest["cursor"]["anchors"]
    assert current["cursor"]["row_offset"] == 20
    assert latest["cursor"]["row_offset"] == 0
    assert current["rows"][0]["index"] == 21
    assert current["fixture_scrolls"] == []


def test_newer_includes_new_arrivals_without_jumping_over_intermediate_rows(native_helper, tmp_path):
    latest = run_page(native_helper, tmp_path, frames=[frame(0, 40)])
    newer = run_page(native_helper, tmp_path, "newer", cursor=latest["cursor"], frames=[frame(0, 80, bottomVerified=True)])
    assert ids(newer) == frame(0, 80)["ids"][35:55]
    assert not newer["at_latest"]
    assert len(newer["rows"]) == 20


@pytest.mark.parametrize(("mutation", "reason"), [
    ("missing", "cursor_anchor_missing"), ("insert", "cursor_sequence_changed"),
    ("reorder", "cursor_sequence_changed"), ("recycled", "cursor_content_changed"),
    ("conversation", "conversation_changed"), ("table", "table_changed"),
    ("window", "window_changed"), ("duplicate", "ax_row_identity_unverified"),
])
def test_invalid_cursor_is_an_explicit_gap(native_helper, tmp_path, mutation, reason):
    base = frame()
    latest = run_page(native_helper, tmp_path, frames=[base])
    changed = copy.deepcopy(base)
    if mutation == "missing":
        changed = frame(41, 60)
    elif mutation == "insert":
        changed["ids"].insert(45, "inserted")
        changed["rows"].insert(45, copy.deepcopy(changed["rows"][0]))
    elif mutation == "reorder":
        changed["ids"][45:47] = reversed(changed["ids"][45:47])
    elif mutation == "recycled":
        changed["rows"][45][2]["texts"] = ["replacement message"]
    elif mutation == "duplicate":
        changed["ids"][45] = changed["ids"][40]
    else:
        changed[f"{mutation}ID"] = "different"
    result = run_page(native_helper, tmp_path, "current", cursor=latest["cursor"], frames=[changed])
    assert not result["ok"] and result["gap"]
    assert result["reason"] == reason
    assert result["rows"] == [] and result["cursor"] is None
    assert not result["at_start"] and not result["at_end"] and not result["at_latest"]
    assert result["fixture_scrolls"] == []


def test_conversation_change_during_scroll_reports_observed_identity(native_helper, tmp_path):
    latest = run_page(native_helper, tmp_path)
    changed = frame(conversationID="other-chat")
    result = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"], frames=[frame(), changed])
    assert result["reason"] == "conversation_changed" and result["gap"]
    assert result["observed_scope"]["conversation_id"] == "other-chat"
    assert result["rows"] == []


def test_current_rejects_content_change_between_observations(native_helper, tmp_path):
    latest = run_page(native_helper, tmp_path)
    changed = frame()
    changed["rows"][-1][2]["texts"] = ["changed during capture"]
    result = run_page(native_helper, tmp_path, "current", cursor=latest["cursor"], frames=[frame(), frame(), changed])
    assert not result["ok"] and result["reason"] == "cursor_content_changed"


def test_unmoving_history_edge_never_claims_complete(native_helper, tmp_path):
    small = frame(0, 20, topVerified=True, bottomVerified=True)
    latest = run_page(native_helper, tmp_path, frames=[small])
    for method in ("AXScrollUp", "targeted_scroll_unavailable"):
        older = run_page(native_helper, tmp_path, "older", cursor=latest["cursor"], frames=[small], scrollMethod=method)
        assert older["ok"] and older["gap"] and not older["moved"]
        assert older["reason"] == "history_boundary_unverified"
        assert not older["at_start"] and not older["history_boundary_verified"]
        assert older["progress_token"] == latest["progress_token"]


@pytest.mark.parametrize("action", ["older", "newer", "current"])
def test_chat_continuation_requires_cursor(native_helper, tmp_path, action):
    result = run_page(native_helper, tmp_path, action)
    assert not result["ok"] and result["reason"] == "cursor_required"
    assert result["fixture_scrolls"] == []


@pytest.mark.parametrize("size", [1, 3, 5, 20, 200])
def test_page_size_bound_and_progress_for_small_pages(native_helper, tmp_path, size):
    latest = run_page(native_helper, tmp_path, size=size)
    older = run_page(native_helper, tmp_path, "older", size=size, cursor=latest["cursor"])
    assert len(older["rows"]) == min(size, 20)
    assert older["moved"] and older["progress_token"] != latest["progress_token"]


def test_inbox_boundaries_and_forward_overlap(native_helper, tmp_path):
    top = run_page(native_helper, tmp_path, "top", kind="inbox", frames=[frame(0, 40, topVerified=True)])
    assert top["at_top"] and not top["at_end"]
    middle = run_page(native_helper, tmp_path, "next", kind="inbox", cursor=top["cursor"], frames=[frame(0, 40)])
    assert ids(middle) == frame(0, 40)["ids"][15:35]
    end = run_page(native_helper, tmp_path, "next", kind="inbox", cursor=middle["cursor"], frames=[frame(0, 40)])
    assert not end["at_end"] and end["reason"] == "list_boundary_unverified"
    verified = run_page(native_helper, tmp_path, "current", kind="inbox", cursor=end["cursor"],
                        frames=[frame(0, 40, bottomVerified=True)])
    assert verified["at_end"] and verified["boundary_scope"] == "currently_exposed_ax_rows"


def test_inbox_virtualization_without_overlap_reports_gap(native_helper, tmp_path):
    top = run_page(native_helper, tmp_path, "top", kind="inbox", frames=[frame(0, 20)])
    result = run_page(native_helper, tmp_path, "next", kind="inbox", cursor=top["cursor"], frames=[frame(0, 20), frame(20, 40)])
    assert not result["ok"] and result["reason"] == "cursor_anchor_missing"
    assert not result["at_end"]


def test_inbox_current_starts_at_visible_offset(native_helper, tmp_path):
    result = run_page(native_helper, tmp_path, "current", kind="inbox", frames=[frame(0, 80, visible=list(range(30, 42)))])
    assert ids(result) == frame(0, 80)["ids"][30:50]
    assert result["fixture_scrolls"] == []


def test_latest_boundary_requires_stable_exposed_inventory(native_helper, tmp_path):
    result = run_page(native_helper, tmp_path, frames=[frame(0, 40, bottomVerified=True),
                      frame(0, 40, bottomVerified=True), frame(0, 41, bottomVerified=True)])
    assert result["ok"] and not result["at_latest"]
    assert result["reason"] == "latest_boundary_unverified"


def scrollbar_frame(*, short=False):
    snapshot = frame(0, 8 if short else 21, scrollbar={"value": 0 if short else 1, "enabled": not short})
    for index, row in enumerate(snapshot["rows"]):
        row[0].update(y=100 + index * 50 if short else 600 - (21 - index) * 50, height=50)
    if short:
        snapshot["rows"][0][0]["height"] = 0
        snapshot["visible"] = list(range(1, 8))
    return snapshot


def test_short_inbox_without_scroll_range_or_row_count_has_verified_boundaries(native_helper, tmp_path):
    result = run_page(native_helper, tmp_path, "top", kind="inbox", frames=[scrollbar_frame(short=True)])
    assert result["ok"] and result["single_chat_verified"]
    assert result["at_top"] and result["at_end"] and not result["gap"]
    assert result["row_count"] == 8


@pytest.mark.parametrize("scrollbar", [
    {"value": 1, "enabled": True},
    {"value": 50, "minimum": 10, "maximum": 50, "enabled": True},
])
def test_latest_accepts_supported_scrollbar_ranges_without_optional_row_count(native_helper, tmp_path, scrollbar):
    snapshot = scrollbar_frame()
    snapshot["scrollbar"] = scrollbar
    result = run_page(native_helper, tmp_path, frames=[snapshot])
    assert result["ok"] and result["at_latest"] and not result["gap"]
    assert not result["at_start"] and not result["history_boundary_verified"]
    assert result["row_count"] == 20


@pytest.mark.parametrize("mutation", [
    "missing_bar", "missing_value", "out_of_range", "partial_range", "invalid_range",
    "middle", "row_count_mismatch", "clipped_tail", "missing_tail_rect", "disabled_overflow",
])
def test_scrollbar_compatibility_keeps_unproven_boundaries_open(native_helper, tmp_path, mutation):
    snapshot = scrollbar_frame()
    if mutation == "missing_bar":
        del snapshot["scrollbar"]
    elif mutation == "missing_value":
        del snapshot["scrollbar"]["value"]
    elif mutation == "out_of_range":
        snapshot["scrollbar"]["value"] = 2
    elif mutation == "partial_range":
        snapshot["scrollbar"]["minimum"] = 0
    elif mutation == "invalid_range":
        snapshot["scrollbar"].update(minimum=1, maximum=0)
    elif mutation == "middle":
        snapshot["scrollbar"]["value"] = 0.5
    elif mutation == "row_count_mismatch":
        snapshot["reportedCount"] = 22
    elif mutation == "clipped_tail":
        snapshot["rows"][-1][0]["y"] += 1
    elif mutation == "missing_tail_rect":
        snapshot["rows"][-1][0]["hasRect"] = False
    elif mutation == "disabled_overflow":
        snapshot["scrollbar"]["enabled"] = False
    result = run_page(native_helper, tmp_path, frames=[snapshot])
    assert result["ok"] and result["gap"] and not result["at_latest"]
    assert result["reason"] == "latest_boundary_unverified"


def test_disabled_scrollbar_never_proves_all_historical_messages_loaded(native_helper, tmp_path):
    snapshot = scrollbar_frame(short=True)
    latest = run_page(native_helper, tmp_path, frames=[snapshot])
    older = run_page(native_helper, tmp_path, "older", frames=[snapshot], cursor=latest["cursor"])
    assert latest["at_latest"]
    assert older["gap"] and not older["at_start"] and not older["history_boundary_verified"]
    assert older["reason"] == "history_boundary_unverified"


def test_python_recovery_uses_identical_raw_message_parser(native_helper, tmp_path, monkeypatch):
    mixed = frame(0, 4)
    mixed["rows"][1] = [node("AXRow", depth=0, width=700, height=160),
                         node("AXImage", ["image"], width=160, height=160)]
    mixed["rows"][2] = [node("AXRow", depth=0, width=700, height=120)]
    native = run_page(native_helper, tmp_path, frames=[mixed])
    calls = []

    def read(command):
        calls.append(command)
        return native["rows"] if command[0] == "chat" else [native]

    monkeypatch.setattr(backend, "_swift_ax", read)
    baseline = backend._ax_chat_messages(20, include_hidden_images=True, include_hidden_image_media=True)
    page = backend.recovery_chat_page()
    assert page["messages"] == baseline
    assert page["message_count"] == 4
    assert all(item["role"] == "unknown" for item in page["messages"])
    assert page["messages"][0]["identity_text"] and page["messages"][0]["capture_row_id"]
    assert page["messages"][1]["media"] and page["messages"][2]["media"]
    assert page["capture_images"] is False
    assert calls == [["chat", "20"], ["recovery-chat-page", "latest", "20", "null"]]


def test_python_transmits_cursor_as_single_json_argument(native_helper, tmp_path, monkeypatch):
    native = run_page(native_helper, tmp_path)
    calls = []
    monkeypatch.setattr(backend, "_swift_ax", lambda command: calls.append(command) or [native])
    backend.recovery_chat_page("current", last=100, cursor=native["cursor"])
    assert calls[0][:3] == ["recovery-chat-page", "current", "20"]
    assert json.loads(calls[0][3]) == native["cursor"]


def test_python_inbox_two_argument_api_retains_returned_cursor(native_helper, tmp_path, monkeypatch):
    native = run_page(native_helper, tmp_path, "top", kind="inbox", frames=[frame(0, 30)])
    calls = []
    monkeypatch.setattr(backend, "_swift_ax", lambda command: calls.append(command) or [native])
    first = backend.recovery_inbox_page("top", 20)
    backend.recovery_inbox_page("next", 20)
    backend.recovery_inbox_page("top", 20)
    assert json.loads(calls[1][3]) == first["cursor"]
    assert calls[2][3] == "null"
    assert first["conversations"][0]["capture_row_id"]
    assert first["conversations"][0]["table_id"] == native["table_id"]


@pytest.mark.parametrize("native", [[], [{"ok": False, "error": "swift_ax_timeout", "at_start": True}],
                                   [{"ok": True, "rows": []}], [{"ok": True, "rows": [None]}]])
def test_python_failures_are_explicit_without_live_fallback(monkeypatch, native):
    monkeypatch.setattr(backend, "_swift_ax", lambda command: native)
    for call in (backend.recovery_chat_page, backend.recovery_inbox_page):
        result = call()
        assert not result["ok"] and result["reason"] and result["gap"]
        assert not result["at_start"] and not result["at_end"]
        assert result["cursor"] is None


@pytest.mark.parametrize("size", [0, -1, True, 1.5, "20"])
def test_invalid_size_is_rejected_before_native_call(size):
    with pytest.raises(ValueError):
        backend.recovery_chat_page(last=size)
    with pytest.raises(ValueError):
        backend.recovery_inbox_page(limit=size)


def test_invalid_action_and_cursor_are_rejected_before_native_call():
    with pytest.raises(ValueError):
        backend.recovery_chat_page("send")
    with pytest.raises(ValueError):
        backend.recovery_inbox_page("scroll")
    with pytest.raises(ValueError):
        backend.recovery_chat_page(cursor=[])
    with pytest.raises(ValueError):
        backend.recovery_chat_page(cursor={"oversize": "x" * 32769})


def test_recovery_native_path_has_no_global_input_or_send_fallback():
    source = (Path(backend.__file__).resolve().parents[1] / "scripts" / "ax_wecom.swift").read_text()
    recovery = source[source.index("struct RecoveryError"):source.index("func intArg")]
    assert "AXScrollToVisible" in recovery and "kAXVerticalScrollBarAttribute" in recovery
    assert "kAXValueAttribute" in recovery and "AXScrollUp" in recovery
    for forbidden in ("CGEvent", "clickAt(", "ensureSingleChat(", "activate(", "sendText(", "kAXPressAction"):
        assert forbidden not in recovery


def historical_pixel_frame():
    snapshot = frame()
    for row in snapshot["rows"]:
        for item in row:
            item["y"] = -1000
    for index, x, y, width in [(25, 326, 137, 160), (26, 794, 237, 180)]:
        snapshot["rows"][index] = [
            node("AXRow", depth=0, y=y - 7, width=700, height=45),
            node("AXTextArea", [f"message {index}"], y=y, width=width),
        ]
        snapshot["rows"][index][1]["x"] = x
    snapshot["visible"] = [25, 26]
    snapshot["fullyVisible"] = [25, 26]
    return snapshot


def pixel_fixture(tmp_path):
    from cli_anything.wecom_gui.tests.test_bubble_geometry import write_png

    path = tmp_path / "historical-bubbles.png"
    write_png(path, 700, 500, [(16, 30, 180, 40, (232, 232, 233)),
                               (484, 130, 200, 40, (207, 230, 253))])
    return {"imagePath": str(path), "window": VIEWPORT}


@pytest.mark.parametrize('changed', [False, True])
def test_native_frame_exports_verified_pixels_and_deletes_them_if_page_changes(native_helper, tmp_path, changed):
    from cli_anything.wecom_gui.tests.test_bubble_geometry import write_png
    import struct

    snapshot = frame(0, 1, visible=[0], fullyVisible=[0])
    snapshot['rows'] = [[node('AXRow', depth=0, y=120, width=700, height=160),
                         node('AXImage', ['动画表情'], y=140, width=100, height=100)]]
    snapshot['rows'][0][1]['x'] = 316
    cursor = run_page(native_helper, tmp_path, frames=[snapshot])['cursor']
    digests = []
    for index, color in enumerate([(100, 50, 150), (60, 160, 90)]):
        image = tmp_path / f'animation-{index}.png'
        output = tmp_path / f'frame-{index}.png'
        write_png(image, 700, 500, [(16, 40, 100, 100, color)])
        after = copy.deepcopy(snapshot)
        if changed:
            after['conversationID'] = 'other-chat'
        page = run_page(native_helper, tmp_path, 'current', cursor=cursor,
            frames=[snapshot, snapshot, snapshot, snapshot, after],
            imagePath=str(image), window=VIEWPORT, frameOutput=str(output), frameRowID=snapshot['ids'][0])
        if changed:
            assert not page['ok'] and not output.exists()
        else:
            assert page['frame']['ok'] and page['frame']['capture_row_id'] == snapshot['ids'][0]
            assert page['frame']['direction_evidence'] == page['rows'][0]['directionEvidence']
            assert page['frame']['direction_evidence']['side'] == 'left'
            assert struct.unpack('>II', output.read_bytes()[16:24]) == (100, 100)
            digests.append(page['frame']['direction_evidence']['imageFingerprint'])
    if not changed:
        assert digests[0] != digests[1]


@pytest.mark.parametrize('native_error', [False, True])
def test_python_frame_result_is_local_png_and_failures_remove_partial_files(monkeypatch, tmp_path, native_error):
    monkeypatch.setattr(backend, '_image_capture_dir', lambda: tmp_path)
    def native(command):
        assert command[:3] == ['recovery-chat-frame', '2', '20']
        output = Path(command[4])
        assert 'single-frame' in output.name and output.suffix == '.png'
        output.write_bytes(b'\x89PNG\r\n\x1a\n' + b'pixels' * 10)
        if native_error:
            return [{'ok': False, 'reason': 'conversation_changed'}]
        return [{'ok': True, 'path': str(output), 'capture_row_id': 'row-2', 'cursor': {'page': 1},
                 'direction_evidence': {'source': 'screencapturekit', 'status': 'matched', 'side': 'left', 'imageFingerprint': 'pixels'}}]
    monkeypatch.setattr(backend, '_swift_ax', native)
    if native_error:
        with pytest.raises(RuntimeError, match='conversation_changed'):
            backend.capture_recovery_frame(2, {'page': 1})
        assert list(tmp_path.iterdir()) == []
    else:
        result = backend.capture_recovery_frame(2, {'page': 1}, animated=True)
        assert result['media'][0]['capture_mode'] == 'single_frame'
        assert result['media'][0]['frame_kind'] == 'animated-sticker'


def historical_page(binary, tmp_path, snapshot=None):
    snapshot = snapshot or frame()
    latest = run_page(binary, tmp_path, frames=[snapshot])
    return run_page(binary, tmp_path, "older", cursor=latest["cursor"], frames=[snapshot])


def test_sck_pixels_verify_historical_cursor_window_not_last_twenty(native_helper, tmp_path, monkeypatch):
    from cli_anything.wecom_gui.core import chat

    snapshot = historical_pixel_frame()
    older = historical_page(native_helper, tmp_path, snapshot)
    verified = run_page(native_helper, tmp_path, "current", cursor=older["cursor"], frames=[snapshot], **pixel_fixture(tmp_path))
    assert verified["ok"] and ids(verified) == ids(older)
    assert [row["index"] for row in verified["rows"]] == list(range(26, 46))
    assert [row["directionEvidence"]["side"] for row in verified["rows"][:2]] == ["left", "right"]
    assert all(row["directionEvidence"]["status"] == "matched" for row in verified["rows"][:2])
    assert verified["rows"][2]["directionEvidence"]["side"] == "unknown"
    assert verified["progress_token"] == older["progress_token"]
    monkeypatch.setattr(backend, "_swift_ax", lambda command: [verified])
    raw = backend.recovery_chat_page("current", cursor=older["cursor"])
    assert raw["messages"][0]["role"] == "unknown"
    assert [message["role"] for message in chat.infer_roles(raw["messages"])[:2]] == ["\u7528\u6237", "\u5ba2\u670d"]


@pytest.mark.parametrize("mutation,reason", [
    ("content", "cursor_content_changed"), ("position", "chat_changed_during_capture"),
    ("table", "table_changed"), ("conversation", "conversation_changed"),
])
def test_sck_revalidation_rejects_page_change_during_capture(native_helper, tmp_path, mutation, reason):
    snapshot = historical_pixel_frame()
    older = historical_page(native_helper, tmp_path, snapshot)
    after = copy.deepcopy(snapshot)
    if mutation == "content":
        after["rows"][25][1]["texts"] = ["changed while capturing"]
    elif mutation == "position":
        after["rows"][25][1]["x"] += 40
    else:
        after[f"{mutation}ID"] = "different"
    result = run_page(native_helper, tmp_path, "current", cursor=older["cursor"],
                      frames=[snapshot, snapshot, snapshot, after], **pixel_fixture(tmp_path))
    assert not result["ok"] and result["gap"]
    assert result["reason"] == reason
    assert result["rows"] == [] and result["cursor"] is None


def test_sck_revalidation_relocates_prepend_and_preserves_pixel_alignment(native_helper, tmp_path):
    snapshot = historical_pixel_frame()
    older = historical_page(native_helper, tmp_path, snapshot)
    after = copy.deepcopy(snapshot)
    prefix = frame(-10, 0)
    after["ids"] = prefix["ids"] + snapshot["ids"]
    after["rows"] = prefix["rows"] + snapshot["rows"]
    after["visible"] = [35, 36]
    result = run_page(native_helper, tmp_path, "current", cursor=older["cursor"],
                      frames=[snapshot, snapshot, snapshot, after], **pixel_fixture(tmp_path))
    assert result["ok"] and result["progress_token"] == older["progress_token"]
    assert result["cursor"]["row_offset"] == 35 and result["rows"][0]["index"] == 36
    assert ids(result) == ids(older)
    assert result["rows"][0]["directionEvidence"]["side"] == "left"


def test_sck_final_gate_drops_evidence_if_page_changes_after_pixel_analysis(native_helper, tmp_path):
    snapshot = historical_pixel_frame()
    older = historical_page(native_helper, tmp_path, snapshot)
    after = copy.deepcopy(snapshot)
    after["rows"][26][1]["x"] -= 60
    result = run_page(native_helper, tmp_path, "current", cursor=older["cursor"],
                      frames=[snapshot, snapshot, snapshot, snapshot, after], **pixel_fixture(tmp_path))
    assert result["reason"] == "chat_changed_during_capture" and not result["ok"]


def test_reveal_maps_saved_row_to_identity_after_history_prepend(native_helper, tmp_path):
    older = historical_page(native_helper, tmp_path)
    before = frame(-10, 60, fullyVisible=[], visible=list(range(50, 60)))
    after = frame(-10, 60, fullyVisible=[36], visible=[36])
    result = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"], frames=[before, after])
    assert result["ok"] and result["changed"] and not result["gap"]
    assert result["row"] == 37 and result["capture_row_id"] == "pid:launch:row-26"
    assert result["cursor"]["row_offset"] == 35
    assert result["progress_token"] == older["progress_token"]
    assert len(result["fixture_scrolls"]) == 1
    assert result["fixture_scrolls"][0]["target_id"] == "pid:launch:row-26"
    reread = run_page(native_helper, tmp_path, "current", cursor=older["cursor"], frames=[after])
    assert ids(reread) == ids(older)
    assert reread["cursor"] == result["cursor"]


def test_reveal_already_visible_old_row_requires_no_scroll(native_helper, tmp_path):
    older = historical_page(native_helper, tmp_path)
    result = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"])
    assert result["ok"] and not result["changed"]
    assert result["fixture_scrolls"] == [] and result["row"] == 27


@pytest.mark.parametrize("row", [1, 25, 46, 60])
def test_reveal_refuses_row_outside_cursor_even_if_loaded(native_helper, tmp_path, row):
    older = historical_page(native_helper, tmp_path)
    result = run_page(native_helper, tmp_path, operation="reveal", row=row, cursor=older["cursor"])
    assert not result["ok"] and result["reason"] == "row_outside_cursor"
    assert result["fixture_scrolls"] == []


def test_reveal_rejects_scope_change_before_scroll(native_helper, tmp_path):
    older = historical_page(native_helper, tmp_path)
    result = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"],
                      frames=[frame(conversationID="different", fullyVisible=[])])
    assert result["reason"] == "conversation_changed" and result["fixture_scrolls"] == []


def test_reveal_revalidates_row_after_scroll_and_caps_attempts(native_helper, tmp_path):
    older = historical_page(native_helper, tmp_path)
    before = frame(fullyVisible=[])
    after = frame(fullyVisible=[26])
    after["rows"][26][2]["texts"] = ["recycled"]
    changed = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"], frames=[before, after])
    assert changed["reason"] == "cursor_content_changed" and not changed["ok"]
    stalled = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"], frames=[before])
    assert stalled["reason"] == "recovery_row_not_visible" and stalled["gap"]
    assert len(stalled["fixture_scrolls"]) == 2


def test_reveal_requires_cursor_row_mapping(native_helper, tmp_path):
    older = historical_page(native_helper, tmp_path)
    older["cursor"].pop("row_offset")
    result = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"])
    assert result["reason"] == "cursor_row_mapping_missing" and result["fixture_scrolls"] == []


def test_python_reveal_and_reader_use_the_same_cursor(native_helper, tmp_path, monkeypatch):
    older = historical_page(native_helper, tmp_path)
    revealed = run_page(native_helper, tmp_path, operation="reveal", row=27, cursor=older["cursor"])
    calls = []

    def read(command):
        calls.append(command)
        return [revealed] if command[0] == "recovery-chat-reveal" else [older]

    monkeypatch.setattr(backend, "_swift_ax", read)
    result = backend.recovery_reveal_chat_row(27, older["cursor"])
    page = backend.recovery_chat_page("current", cursor=result["cursor"])
    assert result["ok"] and page["ok"]
    assert calls[0][:3] == ["recovery-chat-reveal", "27", "20"]
    assert calls[1][:3] == ["recovery-chat-page", "current", "20"]
    assert json.loads(calls[0][3]) == json.loads(calls[1][3]) == older["cursor"]


def test_python_reveal_unavailable_never_falls_back_to_latest_or_global_ui(monkeypatch):
    monkeypatch.setattr(backend, "_swift_ax", lambda command: [])
    result = backend.recovery_reveal_chat_row(27, {})
    assert not result["ok"] and result["gap"] and result["reason"] == "recovery_reveal_unavailable"
    assert backend.recovery_reveal_chat_row(27, None)["reason"] == "cursor_required"
    assert backend.recovery_reveal_chat_row(0, {})["reason"] == "invalid_chat_row"
    with pytest.raises(ValueError):
        backend.recovery_reveal_chat_row(27, {}, last=0)


def test_native_recovery_dispatch_calls_cursor_aware_sck_verifier():
    source = (Path(backend.__file__).resolve().parents[1] / "scripts" / "ax_wecom.swift").read_text()
    dispatch = source[source.index('if command == "recovery-chat-page"'):source.index('} else if command == "rows"')]
    assert "recoveryChatDirections(page" in dispatch
    assert "verifyBubbleDirections(rows" in dispatch and "last: size, cursor: cursor" in dispatch
    assert "recoveryReveal(row:" in dispatch
    assert "last: 0" not in dispatch and "chatSnapshotContext" not in dispatch


def test_navigation_spacers_and_additional_filters_do_not_hide_single_chat(native_helper, tmp_path):
    navigation = [[], ['未读'], ['@我'], ['单聊'], ['群聊'], ['内部聊天'], ['外部聊天'], ['标记'], []]
    cases = [navigation, navigation + [[]] * 11, navigation + [[]] * 12,
             [['单聊'], ['普通客户']], navigation + [['单聊']]]
    fixture = tmp_path / 'navigation.json'
    fixture.write_text(json.dumps([{'navigationTexts': rows} for rows in cases]))
    proc = subprocess.run([str(native_helper), 'single-chat-fixture', str(fixture)],
                          check=True, capture_output=True, text=True, timeout=10)
    assert [json.loads(line)['indices'] for line in proc.stdout.splitlines()] == [[3], [3], [], [], [3, 9]]


def test_native_prepare_verifies_selection_after_navigation(native_helper, tmp_path):
    fixture = tmp_path / "single-chat.json"
    fixture.write_text(json.dumps([
        {"selectedBefore": False, "rowAfter": {"texts": ["\u5355\u804a"], "selected": True}},
        {"selectedBefore": True, "rowAfter": {"texts": ["\u5355\u804a"], "selected": True}},
        {"selectedBefore": False, "rowAfter": {"texts": ["\u5355\u804a"], "selected": False}},
        {"selectedBefore": True, "rowAfter": {"texts": ["\u5355\u804a"], "selected": False}},
        {"selectedBefore": False, "rowAfter": None},
        {"selectedBefore": False, "rowAfter": {"texts": ["\u672a\u8bfb"], "selected": True}},
        {"selectedBefore": False, "rowAfter": {"texts": ["\u5355\u804a"], "selected": "true"}},
    ]))
    proc = subprocess.run([str(native_helper), "single-chat-fixture", str(fixture)],
                          check=True, capture_output=True, text=True, timeout=10)
    results = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [result["ok"] for result in results] == [True, True, False, False, False, False, False]
    assert all(result["selectedAfter"] for result in results[:2])
    assert results[2]["reason"] == "single_chat_not_selected"
    assert results[4]["reason"] == "single_chat_row_not_found"


@pytest.mark.parametrize("native,reason", [
    ([], "single_chat_prepare_unavailable"), ([None], "single_chat_prepare_unavailable"),
    ([{"ok": "true"}], "single_chat_prepare_unavailable"),
    ([{"ok": False, "error": "swift_ax_timeout"}], "swift_ax_timeout"),
    ([{"ok": False, "reason": "single_chat_not_selected"}], "single_chat_not_selected"),
])
def test_python_prepare_failure_does_not_fallback_or_keep_previous_cursor(monkeypatch, native, reason):
    calls = []
    backend._RECOVERY_INBOX_CURSOR.set({"stale": True})
    monkeypatch.setattr(backend, "_swift_ax", lambda command: calls.append(command) or native)
    result = backend.recovery_prepare_inbox()
    assert result["ok"] is False and result["gap"] is True and result["reason"] == reason
    assert calls == ["ensure-single-chat"]
    assert backend._RECOVERY_INBOX_CURSOR.get() is None


def test_prepare_then_page_only_prepares_once_and_keeps_external_candidates(native_helper, tmp_path, monkeypatch):
    from cli_anything.wecom_gui.core import inbox

    snapshot = frame(0, 1)
    snapshot["rows"] = [[node("AXRow", depth=0, width=300, height=60),
                          node("AXStaticText", ["External customer", "order question", "10:00"])]]
    native = run_page(native_helper, tmp_path, "top", kind="inbox", frames=[snapshot])
    calls = []

    def read(command):
        calls.append(command)
        if command == "ensure-single-chat":
            return [{"ok": True, "selectedBefore": False, "selectedAfter": True}]
        return [native]

    monkeypatch.setattr(backend, "_swift_ax", read)
    monkeypatch.delenv("WECOM_GUI_REQUIRE_WECHAT_TAG", raising=False)
    monkeypatch.setenv("WECOM_GUI_REQUIRED_TAGS", "@\u5fae\u4fe1")
    prepared = backend.recovery_prepare_inbox()
    assert prepared["ok"] and prepared["reason"] == "" and not prepared["gap"]
    page = backend.recovery_inbox_page("top")
    backend.recovery_inbox_page("next")
    assert calls[0] == "ensure-single-chat"
    assert [command[0] for command in calls[1:]] == ["recovery-inbox-page", "recovery-inbox-page"]
    row = page["conversations"][0]
    assert row["source"] == "axuielement-bounded" and not row["tags"]
    assert inbox.is_customer_candidate(row)
    monkeypatch.setenv("WECOM_GUI_REQUIRE_WECHAT_TAG", "1")
    assert not inbox.is_customer_candidate(row)


@pytest.mark.parametrize("action", ["current", "top", "next"])
def test_inbox_under_unread_filter_fails_without_navigation(native_helper, tmp_path, action):
    result = run_page(native_helper, tmp_path, action, kind="inbox", frames=[frame(singleChatSelected=False)])
    assert not result["ok"] and result["reason"] == "single_chat_not_selected"
    assert result["fixture_scrolls"] == [] and result["rows"] == []
    assert not result["at_end"]


def test_filter_change_during_page_read_invalidates_the_page(native_helper, tmp_path):
    result = run_page(native_helper, tmp_path, "current", kind="inbox",
                      frames=[frame(), frame(singleChatSelected=False)])
    assert not result["ok"] and result["gap"] and result["reason"] == "single_chat_not_selected"
    assert result["rows"] == [] and not result.get("single_chat_verified")


@pytest.mark.parametrize("verified", [None, False, "true"])
def test_inbox_rows_without_native_single_chat_proof_keep_conservative_source(native_helper, tmp_path, monkeypatch, verified):
    native = run_page(native_helper, tmp_path, "current", kind="inbox")
    native["single_chat_verified"] = verified
    monkeypatch.setattr(backend, "_swift_ax", lambda command: [native])
    page = backend.recovery_inbox_page()
    assert page["conversations"]
    assert all(row["source"] == "axuielement-recovery-inbox" for row in page["conversations"])

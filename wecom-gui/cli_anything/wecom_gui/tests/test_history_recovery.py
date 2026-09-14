from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from threading import Event

import pytest

from cli_anything.wecom_gui.core import (
    edge_channel, edge_message_ledger, edge_state, edge_worker, history_recovery,
    recovery_state, runtime_reporting, state, supervisor,
)


def message(index, *, direction='left', text=None):
    return {'text': text or f'message-{index}', 'row': index + 1, 'capture_row_id': f'ax-{index}',
            'direction_evidence': {'source': 'screencapturekit', 'status': 'matched', 'side': direction}}


@pytest.fixture
def recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(state, 'state_dir', lambda: tmp_path)
    monkeypatch.setattr(runtime_reporting, 'publish', lambda *a, **k: None)
    row = {'title': 'Test Customer', 'external_user_id': 'customer-1', 'source': 'axuielement-bounded'}
    monkeypatch.setattr(edge_worker, '_current_identity_for_row', lambda row: ('customer-1', ''))
    monkeypatch.setattr(edge_worker.macos_backend, 'selected_conversation_row', lambda **k: row)
    monkeypatch.setattr(history_recovery, '_locate', lambda row, **k: row)
    monkeypatch.setattr(history_recovery.macos_backend, 'reveal_chat_row', lambda *a, **k: None)
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_prepare_inbox', lambda: {'ok': True})
    return row


def events():
    with edge_state._connect() as conn:
        return [edge_state._event_row(r) for r in conn.execute('SELECT * FROM edge_inbound_events ORDER BY id')]


def pager(monkeypatch, messages):
    calls = []
    def read(action='latest', last=20, cursor=None):
        assert last == 20
        start = max(0, len(messages) - last) if action == 'latest' else cursor['start']
        if action == 'older':
            start = max(0, start - 15)
        calls.append((action, start))
        return {'ok': True, 'messages': messages[start:start + last], 'cursor': {'start': start},
                'progress_token': str(start), 'at_start': start == 0,
                'at_latest': start + last >= len(messages)}
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_chat_page', read, raising=False)
    return calls


def seed(row, messages):
    enriched = history_recovery.chat.infer_roles(messages)
    return edge_worker._capture_ordered_snapshot(row, {'messages': enriched}, 'customer-1', 'uid:customer-1',
        edge_worker._visible_observation_fingerprints(enriched), bootstrap_recent_count=len(messages))


def task_for(row):
    job = recovery_state.request_start()
    recovery_state.add_chat(job['id'], row)
    return job, recovery_state.chats(job['id'])[0]


def test_recovery_reads_backwards_in_twenty_row_pages_and_replays_without_duplicates(recovery, monkeypatch):
    messages = [message(i) for i in range(60)]
    seed(recovery, messages[:10])
    original_ids = [e['client_event_id'] for e in events()]
    calls = pager(monkeypatch, messages)
    job, task = task_for(recovery)
    history_recovery._recover_chat(job, task)
    assert len(events()) == 60
    assert [e['client_event_id'] for e in events()[:10]] == original_ids
    assert all(e['payload']['message']['source']['recovery_id'] == job['id'] for e in events()[10:])
    assert [e['payload']['message']['text'] for e in events()] == [m['text'] for m in messages]
    assert len([c for c in calls if c[0] == 'older']) == 3
    history_recovery._recover_chat(job, task)
    assert len(events()) == 60


def test_resume_after_partial_commit_reuses_message_ids(recovery, monkeypatch):
    messages = [message(i) for i in range(48)]
    seed(recovery, messages[:5])
    pager(monkeypatch, messages)
    job, task = task_for(recovery)
    capture = history_recovery._capture_page
    count = 0
    def interrupted(*args, **kwargs):
        nonlocal count
        result = capture(*args, **kwargs)
        count += 1
        if count == 1:
            recovery_state.request_pause()
        return result
    monkeypatch.setattr(history_recovery, '_capture_page', interrupted)
    with pytest.raises(history_recovery.RecoveryPaused):
        history_recovery._recover_chat(job, task)
    before = [e['client_event_id'] for e in events()]
    monkeypatch.setattr(history_recovery, '_capture_page', capture)
    assert recovery_state.request_start()['id'] == job['id']
    history_recovery._recover_chat(job, task)
    assert len(events()) == 48
    assert [e['client_event_id'] for e in events()[:len(before)]] == before


def test_read_conversations_are_discovered_without_unread_and_previews_not_stored(recovery, monkeypatch):
    other = {**recovery, 'title': 'Already Read', 'external_user_id': 'customer-2', 'unread': False, 'preview': 'private text'}
    pages = iter([
        {'ok': True, 'conversations': [recovery], 'progress_token': 'one'},
        {'ok': True, 'conversations': [other], 'progress_token': 'two', 'at_end': True},
    ])
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_inbox_page', lambda **k: next(pages), raising=False)
    job = recovery_state.request_start()
    history_recovery._discovery(job)
    tasks = recovery_state.chats(job['id'])
    assert len(tasks) == 2
    assert all('private text' not in t['row_json'] for t in tasks)


def test_missing_anchor_never_creates_new_duplicate_stream(recovery, monkeypatch):
    seed(recovery, [message(i) for i in range(5)])
    pager(monkeypatch, [message(i) for i in range(100, 125)])
    job, task = task_for(recovery)
    with pytest.raises(history_recovery.RecoveryGap, match='history_anchor_missing'):
        history_recovery._recover_chat(job, task)
    assert len(events()) == 5


def test_first_read_chat_is_explicitly_backfilled_and_manual_outgoing_is_preserved(recovery, monkeypatch):
    messages = [message(0), message(1, direction='right'), message(2)]
    pager(monkeypatch, messages)
    job, task = task_for(recovery)
    history_recovery._recover_chat(job, task)
    assert [e['payload']['message']['direction'] for e in events()] == ['inbound', 'outbound', 'inbound']
    assert all(e['payload']['message']['source']['recovery_id'] == job['id'] for e in events())


def test_duplicate_button_requests_are_atomic_and_runtime_does_not_leak_payloads(recovery):
    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = list(executor.map(lambda _: recovery_state.request_start(), range(8)))
    assert len({j['id'] for j in jobs}) == 1
    assert recovery_state.snapshot()['status'] == 'requested'


def test_old_central_cannot_run_recovery_and_no_messages_are_sent(recovery, monkeypatch):
    recovery_state.request_start()
    client = Mock(supports_history_recovery=False)
    result = history_recovery.step(client)
    assert result['recovery']['error_code'] == 'central_history_recovery_upgrade_required'
    client.pull_command.assert_not_called()
    client.post_inbound.assert_not_called()


def test_gate_failure_prevents_gui_and_records_only_exception_type(recovery, monkeypatch):
    recovery_state.request_start()
    client = Mock(supports_history_recovery=True)
    client.history_recovery.side_effect = RuntimeError('secret customer response')
    discover = Mock()
    monkeypatch.setattr(history_recovery, '_discovery', discover)
    history_recovery.step(client)
    discover.assert_not_called()
    assert recovery_state.snapshot()['error_code'] == 'RuntimeError'
    assert 'secret' not in json.dumps(recovery_state.snapshot())


def test_paused_recovery_and_completed_recovery_do_not_pull_commands(recovery, monkeypatch):
    job = recovery_state.request_start()
    recovery_state.update(job['id'], desired_mode='paused', status='completed', remote_active=1)
    client = Mock(supports_history_recovery=True)
    monkeypatch.setattr(history_recovery, '_flush', lambda client: None)
    history_recovery.step(client)
    client.pull_command.assert_not_called()
    client.history_recovery.assert_not_called()
    assert recovery_state.snapshot()['desired_mode'] == 'paused'


def test_normal_resume_waits_for_ack_and_does_not_clear_gate_on_network_failure(recovery, monkeypatch):
    job = recovery_state.request_start()
    recovery_state.update(job['id'], remote_active=1)
    recovery_state.request_normal()
    monkeypatch.setattr(history_recovery, '_flush', lambda client: None)
    client = Mock(supports_history_recovery=True)
    client.history_recovery.side_effect = RuntimeError('offline')
    history_recovery.step(client)
    assert recovery_state.active()
    client.history_recovery.side_effect = None
    recovery_state.update(job['id'], next_attempt_at=0)
    history_recovery.step(client)
    assert not recovery_state.active()
    client.history_recovery.assert_called_with(job['id'], 'end')


def test_supervisor_recovery_reuses_session_and_normal_start_is_explicit(recovery, monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, 'RUN_DIR', tmp_path)
    start = Mock(return_value={'ok': True, 'changed': False})
    monkeypatch.setattr(supervisor, '_start', start)
    monkeypatch.setattr(supervisor, '_session_running', lambda _: False)
    first = supervisor.recover_history()
    second = supervisor.recover_history()
    assert first['recovery_id'] == second['recovery_id']
    assert recovery_state.snapshot()['desired_mode'] == 'recovery'
    supervisor.start('edge')
    assert recovery_state.snapshot()['desired_mode'] == 'normal'


def test_client_requires_matching_recovery_ack():
    client = edge_channel.ChannelClient(edge_channel.ChannelConfig('https://example.invalid', 'device', 'token', False, 1))
    client._json_request = Mock(return_value={'accepted': True, 'recovery_id': 'other', 'active': True})
    with pytest.raises(edge_channel.ChannelError, match='not acknowledged'):
        client.history_recovery('requested', 'begin')


def test_complete_recovery_requires_central_ack_and_stays_paused(recovery, monkeypatch):
    messages = [message(i) for i in range(30)]
    seed(recovery, messages[:8])
    pager(monkeypatch, messages)
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_inbox_page', lambda **k: {
        'ok': True, 'conversations': [recovery], 'at_end': True, 'progress_token': 'list-end'}, raising=False)
    job = recovery_state.request_start()
    client = Mock(supports_history_recovery=True, supports_deferred_media=True)
    client.register_message.side_effect = RuntimeError('offline')
    for _ in range(8):
        history_recovery.step(client)
    assert recovery_state.snapshot()['desired_mode'] == 'paused'
    assert recovery_state.snapshot()['status'] == 'partial'
    assert recovery_state.snapshot()['registered'] == 0
    assert recovery_state.snapshot()['pending_uploads'] == 30
    client.register_message.side_effect = None
    client.register_message.return_value = {'accepted': True, 'message': {'mediaState': 'ready'}}
    with edge_state._connect() as conn:
        conn.execute('UPDATE edge_inbound_events SET registration_next_attempt_at=0')
    for _ in range(3):
        history_recovery.step(client)
    assert recovery_state.snapshot()['status'] == 'completed'
    assert recovery_state.snapshot()['registered'] == 30
    assert recovery_state.snapshot()['pending_uploads'] == 0
    assert recovery_state.snapshot()['desired_mode'] == 'paused'
    assert len(events()) == 30
    client.pull_command.assert_not_called()
    client.history_recovery.assert_called_once_with(job['id'], 'begin')


def test_recovery_adopts_only_never_registered_spool_rows(recovery):
    seed(recovery, [message(0), message(1)])
    first = events()[0]
    edge_state.start_registration(first['client_event_id'])
    recovery_state.adopt_unregistered('recovery-123')
    saved = events()
    assert 'recovery_id' not in saved[0]['payload']['message']['source']
    assert saved[1]['payload']['message']['source']['recovery_id'] == 'recovery-123'
    assert saved[0]['client_event_id'] == first['client_event_id']


def test_recovery_spool_never_falls_back_to_an_older_server(recovery):
    seed(recovery, [message(0)])
    recovery_state.adopt_unregistered('recovery-123')
    client = Mock(supports_history_recovery=False, supports_deferred_media=True)
    edge_worker.flush_registrations(client)
    edge_worker.flush_inbound(client)
    client.register_message.assert_not_called()
    client.post_inbound.assert_not_called()
    assert len(events()) == 1


def test_finishing_cannot_override_newer_pause_or_normal_request(recovery):
    job = recovery_state.request_start()
    recovery_state.request_normal()
    recovery_state.finish(job['id'], 'completed')
    assert recovery_state.snapshot()['desired_mode'] == 'normal'
    recovery_state.request_pause()
    recovery_state.finish(job['id'], 'failed')
    assert recovery_state.snapshot()['status'] == 'paused'


def test_discovery_gap_does_not_discard_already_discovered_chats(recovery, monkeypatch):
    job = recovery_state.request_start()
    recovery_state.add_chat(job['id'], recovery)
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_inbox_page', lambda **k: {
        'ok': False, 'reason': 'list_boundary_unverified'}, raising=False)
    client = Mock(supports_history_recovery=True)
    monkeypatch.setattr(history_recovery, '_flush', lambda _: None)
    result = history_recovery.step(client)
    assert result['recovery']['gaps'] == 1
    assert len(recovery_state.chats(job['id'])) == 1
    assert recovery_state.current()['discovery_complete'] == 1


def test_failed_preflight_can_be_cancelled_without_contacting_old_server(recovery):
    recovery_state.request_start()
    client = Mock(supports_history_recovery=False)
    history_recovery.step(client)
    recovery_state.request_normal()
    history_recovery.step(client)
    assert not recovery_state.active()
    client.history_recovery.assert_not_called()


def test_registered_missing_image_does_not_deadlock_explicit_normal_resume(recovery, monkeypatch):
    seed(recovery, [message(0)])
    event = events()[0]
    edge_state.mark_registered(event['client_event_id'], 'inbound')
    edge_state.wait_for_inbound_media(event['client_event_id'])
    job = recovery_state.request_start()
    recovery_state.update(job['id'], remote_active=1)
    recovery_state.request_normal()
    client = Mock(supports_history_recovery=True)
    monkeypatch.setattr(history_recovery, '_flush', lambda _: None)
    history_recovery.step(client)
    client.history_recovery.assert_called_once_with(job['id'], 'end')
    assert not recovery_state.active()


def test_recovery_click_during_end_ack_gets_a_fresh_job(recovery, monkeypatch):
    old = recovery_state.request_start()
    recovery_state.update(old['id'], remote_active=1)
    recovery_state.request_normal()
    entered, release, clicked = Event(), Event(), Event()
    def acknowledge(*args):
        entered.set()
        assert release.wait(5)
    def click():
        clicked.set()
        return recovery_state.request_start()
    monkeypatch.setattr(history_recovery, '_flush', lambda _: None)
    client = Mock(supports_history_recovery=True)
    client.history_recovery.side_effect = acknowledge
    with ThreadPoolExecutor(max_workers=2) as executor:
        ending = executor.submit(history_recovery.step, client)
        assert entered.wait(5)
        starting = executor.submit(click)
        assert clicked.wait(5)
        release.set()
        ending.result(timeout=5)
        new = starting.result(timeout=5)
    assert new['id'] != old['id']
    assert recovery_state.snapshot()['desired_mode'] == 'recovery'
    assert recovery_state.current()['release_pending'] == 1
    assert recovery_state.current()['remote_active'] == 0


def test_pause_before_discovery_prevents_navigation(recovery, monkeypatch):
    job = recovery_state.request_start()
    monkeypatch.setattr(history_recovery, '_publish', lambda *a, **k: recovery_state.request_pause())
    prepare = Mock()
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_prepare_inbox', prepare)
    with pytest.raises(history_recovery.RecoveryPaused):
        history_recovery._discovery(job)
    prepare.assert_not_called()


def test_one_shot_cannot_bypass_running_worker_lock(recovery, monkeypatch):
    tick = Mock()
    monkeypatch.setattr(edge_worker, 'tick', tick)
    with edge_worker.worker_lock():
        with pytest.raises(RuntimeError, match='already running'):
            edge_worker.run_once()
    tick.assert_not_called()
    edge_worker.run_once()
    tick.assert_called_once()


def test_explicit_recovery_grants_one_media_retry_budget_without_resetting_identity(recovery):
    job = recovery_state.request_start()
    with edge_message_ledger.transaction() as conn:
        entries, _ = edge_message_ledger.prepare(conn, 'uid:customer-1', [{'match_key': 'image'}], bootstrap_recent_count=1)
        entry = entries[0]
        edge_message_ledger.mark(conn, entry, status='pending_media')
        edge_message_ledger.remember_media_identity(conn, entry, {
            'capture_row_id': 'pinned-row', 'direction_evidence': {'imageFingerprint': 'pinned-pixels'}})
        conn.execute("UPDATE edge_media_capture_state SET attempts=5, paused_reason='media_retry_limit'")
    recovery_state.resume_paused_media(job['id'])
    current = edge_message_ledger.media_state(entry)
    assert current['attempts'] == 0 and current['paused_reason'] == ''
    assert current['capture_row_id'] == 'pinned-row' and current['image_fingerprint'] == 'pinned-pixels'
    with edge_message_ledger.transaction() as conn:
        conn.execute("UPDATE edge_media_capture_state SET attempts=5, paused_reason='media_retry_limit'")
    recovery_state.resume_paused_media(job['id'])
    assert edge_message_ledger.media_state(entry)['attempts'] == 5
    recovery_state.request_pause()
    recovery_state.request_start()
    recovery_state.resume_paused_media(job['id'])
    assert edge_message_ledger.media_state(entry)['attempts'] == 0


def test_new_recovery_counts_existing_pending_media_without_changing_old_source(recovery, monkeypatch):
    old = recovery_state.request_start()
    photo = message(0, direction='unknown', text='[图片]')
    photo['media'] = [{'type': 'image'}]
    monkeypatch.setattr(edge_worker, '_prepare_snapshot_media', lambda *a, **k: None)
    seed(recovery, [photo])
    recovery_state.adopt_unregistered(old['id'])
    event = events()[0]
    edge_state.mark_registered(event['client_event_id'], 'unknown')
    recovery_state.update(old['id'], desired_mode='normal', release_pending=0)
    new = recovery_state.request_start()
    assert new['id'] != old['id']
    with edge_message_ledger.transaction() as conn:
        entries = [dict(r) for r in conn.execute('SELECT * FROM edge_message_ledger')]
    assert entries[0]['event_id'] != event['client_event_id']
    recovery_state.link_messages(new['id'], entries)
    view = recovery_state.snapshot()
    assert view['pending_media'] == 1 and view['pending_direction'] == 1
    assert view['registered'] == 1
    assert events()[0]['payload']['message']['source']['recovery_id'] == old['id']


def test_unverified_first_boundary_survives_retry_and_new_job(recovery, monkeypatch):
    messages = [message(i) for i in range(10, 30)]
    def read(action='latest', last=20, cursor=None):
        if action == 'older':
            return {'ok': False, 'reason': 'older_page_unavailable'}
        return {'ok': True, 'messages': messages, 'cursor': {'start': 10},
                'progress_token': '10', 'at_start': False, 'at_latest': True}
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_chat_page', read)
    job, task = task_for(recovery)
    for _ in range(2):
        with pytest.raises(history_recovery.RecoveryGap, match='first_history_boundary_unverified'):
            history_recovery._recover_chat(job, task)
        recovery_state.request_pause()
        assert recovery_state.request_start()['id'] == job['id']
    original_ids = [event['client_event_id'] for event in events()]
    assert len(original_ids) == 20
    recovery_state.update(job['id'], desired_mode='normal', release_pending=0)
    new, task = task_for(recovery)
    assert new['id'] != job['id']
    with pytest.raises(history_recovery.RecoveryGap, match='first_history_boundary_unverified'):
        history_recovery._recover_chat(new, task)
    assert [event['client_event_id'] for event in events()] == original_ids
    assert recovery_state.boundary_reason('uid:customer-1') == 'first_history_boundary_unverified'


def test_only_verified_start_at_first_ledger_row_can_clear_boundary(recovery, monkeypatch):
    messages = [message(i) for i in range(10)]
    seed(recovery, messages)
    recovery_state.remember_boundary('uid:customer-1', 'first_history_boundary_unverified')
    pager(monkeypatch, messages)
    job, task = task_for(recovery)
    history_recovery._recover_chat(job, task)
    assert recovery_state.boundary_reason('uid:customer-1') == ''
    assert len(events()) == 10


def test_retry_revisits_pending_image_older_than_normal_ledger_window(recovery, monkeypatch, tmp_path):
    messages = [message(i) for i in range(250)]
    messages[5] = message(5, text='[图片]')
    messages[5]['media'] = [{'type': 'image'}]
    monkeypatch.setattr(edge_worker, '_prepare_snapshot_media', lambda *a, **k: None)
    pager(monkeypatch, messages)
    old, task = task_for(recovery)
    history_recovery._recover_chat(old, task)
    original_ids = [event['client_event_id'] for event in events()]
    assert len(original_ids) == 250
    assert recovery_state.snapshot()['pending_media'] == 1
    assert history_recovery._pending_floor('uid:customer-1') == 6
    for event in events():
        edge_state.mark_registered(event['client_event_id'], event['payload']['message']['direction'])
    recovery_state.update(old['id'], desired_mode='normal', release_pending=0)
    new, task = task_for(recovery)
    calls = pager(monkeypatch, messages)
    captured_file = tmp_path / 'old-image.png'
    captured_file.write_bytes(b'image-fixture')
    capture = Mock(side_effect=lambda row, candidates, index, *a, **k: {
        **candidates[index][1], 'media': [{'type': 'image', 'capture_path': str(captured_file)}]})
    monkeypatch.setattr(edge_worker, '_prepare_snapshot_media', capture)
    history_recovery._recover_chat(new, task)
    assert ('older', 5) in calls
    capture.assert_called_once()
    assert [event['client_event_id'] for event in events()] == original_ids
    image = events()[5]
    assert image['media'][0]['capture_path'] == str(captured_file)
    assert image['payload']['message']['source']['sequence'] == 6
    assert image['payload']['message']['source']['recovery_id'] == old['id']
    assert recovery_state.snapshot()['pending_media'] == 0


@pytest.mark.parametrize('at_latest,gap', [(False, False), (True, True)])
def test_unverified_latest_boundary_never_completes(recovery, monkeypatch, at_latest, gap):
    messages = [message(i) for i in range(5)]
    seed(recovery, messages)
    pager(monkeypatch, messages)
    read = history_recovery.macos_backend.recovery_chat_page
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_chat_page', lambda **kwargs: {
        **read(**kwargs), 'at_latest': at_latest, 'gap': gap, 'reason': 'latest_boundary_unverified'})
    job, task = task_for(recovery)
    with pytest.raises(history_recovery.RecoveryGap, match='latest_boundary_unverified'):
        history_recovery._recover_chat(job, task)
    assert len(events()) == 5
    assert recovery_state.chats(job['id'])[0]['status'] != 'completed'


def test_page_before_ledger_start_repairs_known_image_and_retains_older_gap(recovery, monkeypatch, tmp_path):
    messages = [message(i) for i in range(70)]
    messages[30] = message(30, text='[图片]')
    messages[30]['media'] = [{'type': 'image'}]
    monkeypatch.setattr(edge_worker, '_prepare_snapshot_media', lambda *a, **k: None)
    seed(recovery, messages[27:])
    original_ids = [event['client_event_id'] for event in events()]
    calls = pager(monkeypatch, messages)
    captured_file = tmp_path / 'known-image.png'
    captured_file.write_bytes(b'image-fixture')
    capture = Mock(side_effect=lambda row, candidates, index, *a, **k: {
        **candidates[index][1], 'media': [{'type': 'image', 'capture_path': str(captured_file)}]})
    monkeypatch.setattr(edge_worker, '_prepare_snapshot_media', capture)
    job, task = task_for(recovery)
    with pytest.raises(history_recovery.RecoveryGap, match='earlier_history_unregistered'):
        history_recovery._recover_chat(job, task)
    assert ('older', 20) in calls
    capture.assert_called_once()
    assert [event['client_event_id'] for event in events()] == original_ids
    assert events()[3]['media'][0]['capture_path'] == str(captured_file)
    assert recovery_state.boundary_reason('uid:customer-1') == 'earlier_history_unregistered'


def test_ambiguous_suffix_does_not_reuse_arbitrary_message_identity(recovery, monkeypatch):
    seed(recovery, [message(0), message(1)])
    pager(monkeypatch, [message(9), message(0), message(1), message(8), message(0), message(1)])
    job, task = task_for(recovery)
    with pytest.raises(history_recovery.RecoveryGap, match='history_anchor_missing'):
        history_recovery._recover_chat(job, task)
    assert len(events()) == 2


@pytest.mark.parametrize('clip_older_prefix', [False, True])
def test_old_page_image_uses_cursor_reveal_and_same_page_verification(recovery, monkeypatch, tmp_path, clip_older_prefix):
    page_cursor = {'start': 0}
    shown = False
    saved_image = tmp_path / 'captured.png'
    saved_image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 32)

    def image_page():
        image = message(1, text='[图片]')
        image['direction_evidence']['imageFingerprint'] = 'fixture-pixels'
        image['media'] = [{'type': 'image', 'rect': {'x': 10, 'y': 10 if shown else 900, 'width': 30, 'height': 30},
                           'chat_viewport': {'x': 0, 'y': 0, 'width': 500, 'height': 500}}]
        return {'ok': True, 'messages': [message(0), image, message(2)], 'cursor': page_cursor,
                'progress_token': 'page', 'at_start': True, 'at_latest': True}

    def read(action='latest', last=20, cursor=None):
        assert action in {'current', 'latest'} and last == 20
        return image_page()

    def reveal(row, cursor, last=20):
        nonlocal shown
        assert row == 2 and cursor == page_cursor and last == 20
        shown = True
        return {'ok': True}

    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_chat_page', read)
    reveal_call = Mock(side_effect=reveal)
    monkeypatch.setattr(history_recovery.macos_backend, 'recovery_reveal_chat_row', reveal_call)
    monkeypatch.setattr(edge_worker.macos_backend, 'activate_app', lambda: None)
    monkeypatch.setattr(edge_worker.chat, 'read_current', Mock(side_effect=AssertionError('must not read latest page for old image')))
    monkeypatch.setattr(edge_worker.macos_backend, 'reveal_chat_row', Mock(side_effect=AssertionError('must use recovery cursor')))
    monkeypatch.setattr(edge_worker.macos_backend, 'capture_chat_images', lambda messages, **k: [
        {**m, 'media': [{**item, 'capture_path': str(saved_image)} for item in m['media']]} for m in messages])
    if clip_older_prefix:
        with monkeypatch.context() as pending_media:
            pending_media.setattr(edge_worker, '_prepare_snapshot_media', lambda *a, **k: None)
            seed(recovery, image_page()['messages'][1:])
    job, task = task_for(recovery)
    if clip_older_prefix:
        with pytest.raises(history_recovery.RecoveryGap, match='earlier_history_unregistered'):
            history_recovery._recover_chat(job, task)
    else:
        history_recovery._recover_chat(job, task)
    reveal_call.assert_called_once_with(2, page_cursor, last=20)
    image = events()[0 if clip_older_prefix else 1]
    if not clip_older_prefix:
        assert image['payload']['message']['source']['recovery_id'] == job['id']
    assert image['payload']['message']['direction'] == 'inbound'
    assert image['media'][0]['capture_path'] == str(saved_image)
    assert len(events()) == (2 if clip_older_prefix else 3)

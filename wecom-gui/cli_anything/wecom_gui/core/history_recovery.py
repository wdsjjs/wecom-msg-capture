"""Explicit historical capture. This path never pulls or executes deliveries."""

from __future__ import annotations

import hashlib
import json
import time

from cli_anything.wecom_gui.core import (
    chat, edge_message_ledger, edge_state, inbox, recovery_state, runtime_reporting, state,
)
from cli_anything.wecom_gui.utils import macos_backend


WINDOW = 20
MAX_LIST_PAGES = 100
MAX_CHAT_PAGES = 100
_last_report = (None, 0.0)


class RecoveryGap(RuntimeError):
    pass


class RecoveryPaused(RuntimeError):
    pass


def _checkpoint(job):
    if not recovery_state.should_continue(job['id']):
        raise RecoveryPaused()


def _publish(job, phase, *, label='', error=''):
    global _last_report
    recovery_state.update(job['id'], phase=phase, conversation_label=label, error_code=error)
    view = recovery_state.snapshot()
    signature = (phase, label, error, *(view[k] for k in ('discovered', 'completed', 'registered', 'pending_uploads', 'pending_media', 'pending_direction', 'gaps')))
    if signature == _last_report[0] and time.monotonic() - _last_report[1] < 5:
        return
    _last_report = (signature, time.monotonic())
    runtime_reporting.publish('edge_channel', status='waiting' if error or phase in {'paused', 'completed', 'partial'} else 'running',
        phase='recovery_' + phase, conversation_label=label, error_code=error,
        metrics={k: view[k] for k in ('discovered', 'completed', 'registered', 'pending_uploads', 'pending_media', 'pending_direction', 'gaps')})


def _discovery(job):
    _publish(job, 'discovering')
    with state.gui_lock():
        _checkpoint(job)
        _prepare_inbox()
    # Known contacts stay in the work list even if hidden from today's inbox;
    # failure to locate them becomes an explicit gap instead of an omission.
    bindings = state.list_wecom_customer_bindings(limit=10001)
    if len(bindings) > 10000:
        raise RecoveryGap('known_conversation_limit')
    for binding in bindings:
        recovery_state.add_chat(job['id'], {
            'title': binding.get('customer_name', ''), 'external_user_id': binding.get('uid', ''),
            'source': 'axuielement-bounded',
        })
    seen = set()
    action = 'top'
    for _ in range(MAX_LIST_PAGES):
        _checkpoint(job)
        with state.gui_lock():
            page = macos_backend.recovery_inbox_page(action=action, limit=WINDOW)
        if not page.get('ok'):
            raise RecoveryGap(page.get('reason') or 'inbox_page_unavailable')
        rows = page.get('conversations') or []
        for row in rows:
            if inbox.is_customer_candidate(row):
                bound = [b for b in bindings if b.get('customer_name') == row.get('title')]
                if not row.get('external_user_id') and len(bound) == 1:
                    row = {**row, 'external_user_id': bound[0]['uid']}
                recovery_state.add_chat(job['id'], row)
        if page.get('at_end') is True:
            recovery_state.update(job['id'], discovery_complete=1)
            return
        token = page.get('progress_token')
        if not token or token in seen:
            raise RecoveryGap('inbox_boundary_unverified')
        seen.add(token)
        action = 'next'
    raise RecoveryGap('inbox_page_limit')


def _prepare_inbox():
    result = macos_backend.recovery_prepare_inbox()
    if result.get('ok') is not True:
        raise RecoveryGap(result.get('reason') or 'single_chat_prepare_unavailable')


def _locate(row, *, checkpoint=lambda: None):
    """Re-find the list row after reordering; never replay saved coordinates."""
    checkpoint()
    _prepare_inbox()
    action = 'top'
    seen = set()
    matches = {}
    for _ in range(MAX_LIST_PAGES):
        checkpoint()
        page = macos_backend.recovery_inbox_page(action=action, limit=WINDOW)
        if not page.get('ok'):
            raise RecoveryGap(page.get('reason') or 'conversation_list_unavailable')
        for item in page.get('conversations', []):
            if item.get('title') == row.get('title'):
                identity = item.get('capture_row_id')
                if not identity:
                    raise RecoveryGap('conversation_row_identity_missing')
                matches[identity] = item
        if len(matches) > 1:
            raise RecoveryGap('conversation_title_ambiguous')
        token = page.get('progress_token')
        if page.get('at_end'):
            break
        if not token or token in seen:
            raise RecoveryGap('conversation_list_boundary_unverified')
        seen.add(token)
        action = 'next'
    else:
        raise RecoveryGap('conversation_list_page_limit')
    if matches:
        selected = next(iter(matches.values()))
        inbox.open_row(selected)
        return selected
    raise RecoveryGap('conversation_not_found')


def _page(action='latest', cursor=None):
    result = macos_backend.recovery_chat_page(action=action, last=WINDOW, cursor=cursor)
    if not result.get('ok'):
        raise RecoveryGap(result.get('reason') or 'chat_page_unavailable')
    messages = result.get('messages') or []
    if len(messages) > WINDOW:
        raise RecoveryGap('chat_window_contract_invalid')
    return {**result, 'messages': chat.infer_roles(messages),
            'hash': hashlib.sha256(json.dumps([m.get('text', '') for m in messages], ensure_ascii=False).encode()).hexdigest()}


def _keys(messages):
    from cli_anything.wecom_gui.core import edge_worker
    candidates = edge_worker._visible_observation_fingerprints(messages)
    return [d['match_key'] for d in edge_worker._snapshot_descriptors('', candidates)]


def _page_alignment(conversation_key, page):
    keys = _keys(page['messages'])
    with edge_message_ledger.transaction() as conn:
        sample = conn.execute('SELECT sequence FROM edge_message_ledger WHERE conversation_key=? ORDER BY sequence LIMIT 2',
                              (conversation_key,)).fetchall()
        alignment = edge_message_ledger.recovery_alignment(conn, conversation_key, keys)
    if not sample:
        return 'first', []
    if not alignment:
        return 'missing', []
    rows, size = alignment
    if size < len(sample):
        return 'missing', []
    overlap = page['messages'][:size]
    if all(m.get('media') and str(m.get('text') or '') in {'', '[图片]'} for m in overlap):
        return 'missing', []
    return 'found', rows


def _recovery_page_alignment(conversation_key, page):
    anchor, rows = _page_alignment(conversation_key, page)
    if anchor != 'missing':
        return anchor, rows
    # A backward page can straddle the first registered row. Repair its known
    # suffix without appending the unknown older prefix under newer sequences.
    keys = _keys(page['messages'])
    with edge_message_ledger.transaction() as conn:
        first = [dict(r) for r in conn.execute('SELECT * FROM edge_message_ledger WHERE conversation_key=? ORDER BY sequence LIMIT ?',
                                             (conversation_key, WINDOW))]
    matches = []
    for offset in range(1, len(keys)):
        size = min(len(first), len(keys) - offset)
        if size < min(2, len(first)) or not first:
            continue
        if keys[offset:offset + size] != [r['match_key'] for r in first[:size]]:
            continue
        if all(m.get('media') and str(m.get('text') or '') in {'', '[图片]'}
               for m in page['messages'][offset:offset + size]):
            continue
        matches.append((size, offset))
    if not matches:
        return anchor, rows
    size = max(size for size, _ in matches)
    offsets = [offset for length, offset in matches if length == size]
    if len(offsets) != 1:
        return anchor, rows
    offset = offsets[0]
    page['capture_offset'] = offset
    return 'found', first[:size]


def _pending_floor(conversation_key):
    with edge_message_ledger.transaction() as conn:
        row = conn.execute("""SELECT min(sequence) FROM edge_message_ledger WHERE conversation_key=?
            AND capture_status IN ('baseline','pending_media','pending_direction')""", (conversation_key,)).fetchone()
        return row[0]


def _same_page(saved, fresh):
    if _keys(saved['messages']) != _keys(fresh['messages']):
        return False
    # These IDs are checked only within one visit. On process restart all
    # native cursors are discarded; the persisted ordered ledger is re-used.
    before = [m.get('capture_row_id') for m in saved['messages']]
    after = [m.get('capture_row_id') for m in fresh['messages']]
    return bool(before) and all(before) and before == after


def _checked_identity(row, uid):
    from cli_anything.wecom_gui.core import edge_worker
    if not edge_worker._row_matches_opened(row, macos_backend.selected_conversation_row(limit=30)):
        raise RecoveryGap('conversation_changed')
    actual_uid, error = edge_worker._current_identity_for_row(row)
    if error or actual_uid != uid:
        raise RecoveryGap('conversation_identity_changed')


def _capture_page(job, row, uid, key, saved):
    from cli_anything.wecom_gui.core import edge_worker

    def reader():
        _checked_identity(row, uid)
        fresh = _page('current', saved.get('cursor'))
        if not _same_page(saved, fresh):
            raise RecoveryGap('history_page_changed')
        _checked_identity(row, uid)
        return {**fresh, 'messages': fresh['messages'][saved.get('capture_offset', 0):]}

    def reveal(row_id):
        observed = reader()
        target = next((m for m in observed['messages'] if m.get('capture_row_id') == row_id), None)
        if not target:
            raise RecoveryGap('history_row_changed')
        result = macos_backend.recovery_reveal_chat_row(int(target['row']), observed['cursor'], last=WINDOW)
        if not result.get('ok'):
            raise RecoveryGap(result.get('reason') or 'history_row_not_visible')

    reader.reveal = reveal

    fresh = reader()
    # Bring pending text bubbles into the viewport so direction comes from
    # actual pixels, never from wording or alternating speaker guesses.
    for index, message in enumerate(fresh['messages']):
        _checkpoint(job)
        if message.get('role') != 'unknown' or message.get('media') or not message.get('row'):
            continue
        reveal(message.get('capture_row_id'))
        observed = reader()
        fresh['messages'][index] = observed['messages'][index]
    candidates = edge_worker._visible_observation_fingerprints(fresh['messages'])
    result = edge_worker._capture_ordered_snapshot(row, fresh, uid, key, candidates,
        bootstrap_recent_count=len(candidates), recovery_id=job['id'], snapshot_reader=reader,
        media_budget=edge_worker.MediaCaptureBudget())
    if not result.get('ok'):
        raise RecoveryGap(result.get('reason') or 'message_alignment_pending')
    return result


def _recover_chat(job, task):
    from cli_anything.wecom_gui.core import edge_worker
    row = task['row']
    _publish(job, 'locating', label=row.get('title', ''))
    recovery_state.clear_pages(job, task)
    recovery_state.update_chat(job, task, status='reading', pages=0)
    with state.gui_lock():
        _checkpoint(job)
        selected = _locate(row, checkpoint=lambda: _checkpoint(job))
        if not edge_worker._wait_for_opened_conversation(selected):
            raise RecoveryGap('conversation_open_unconfirmed')
        _publish(job, 'reading', label=row.get('title', ''))
        uid, error = edge_worker._current_identity_for_row(selected)
        if error or not uid:
            raise RecoveryGap(error or 'conversation_identity_missing')
        if row.get('external_user_id') and row['external_user_id'] != uid:
            raise RecoveryGap('conversation_identity_changed')
        key = state.conversation_key_for_uid(uid)
        if task.get('conversation_key') and task['conversation_key'] != key:
            raise RecoveryGap('conversation_identity_changed')
        recovery_state.update_chat(job, task, conversation_key=key)
        first = _page()
        if not first['messages']:
            raise RecoveryGap('empty_history_unverified')
        page = first
        tokens = set()
        boundary_error = recovery_state.boundary_reason(key)
        pending_floor = _pending_floor(key)
        verified_start = False
        for index in range(MAX_CHAT_PAGES):
            _checkpoint(job)
            _checked_identity(selected, uid)
            anchor, aligned = _recovery_page_alignment(key, page)
            if page.get('capture_offset'):
                boundary_error = boundary_error or 'earlier_history_unregistered'
                recovery_state.remember_boundary(key, boundary_error)
            recovery_state.save_page(job, task, index, page)
            recovery_state.update_chat(job, task, pages=index + 1)
            if anchor == 'found' and (pending_floor is None or aligned[0]['sequence'] <= pending_floor):
                verified_start = bool(page.get('at_start') and aligned[0]['sequence'] == 1 and not page.get('capture_offset'))
                break
            if page.get('at_start'):
                if anchor != 'first':
                    raise RecoveryGap('history_anchor_missing')
                verified_start = True
                break
            token = page.get('progress_token')
            if not token or token in tokens or index == MAX_CHAT_PAGES - 1:
                if anchor != 'first':
                    raise RecoveryGap('history_anchor_missing')
                boundary_error = 'first_history_boundary_unverified'
                break
            tokens.add(token)
            try:
                page = _page('older', page.get('cursor'))
            except RecoveryGap:
                if anchor != 'first':
                    raise
                boundary_error = 'first_history_boundary_unverified'
                break
        # Persist before replay: a crash or a new recovery job must not convert
        # a just-created anchor into proof that the older history was complete.
        if verified_start:
            recovery_state.confirm_boundary(key)
            boundary_error = ''
        elif boundary_error:
            recovery_state.remember_boundary(key, boundary_error)
        _publish(job, 'capturing', label=row.get('title', ''))
        for page_index in range(index, -1, -1):
            _checkpoint(job)
            saved = recovery_state.load_page(job, task, page_index)
            _capture_page(job, selected, uid, key, saved)
        # The customer may have sent again while older pages were being read.
        latest = _page()
        if _recovery_page_alignment(key, latest)[0] != 'found':
            raise RecoveryGap('new_arrivals_exceed_overlap')
        if latest.get('capture_offset'):
            boundary_error = boundary_error or 'earlier_history_unregistered'
            recovery_state.remember_boundary(key, boundary_error)
        _capture_page(job, selected, uid, key, latest)
        if latest.get('at_latest') is not True or latest.get('gap'):
            raise RecoveryGap('latest_boundary_unverified')
        if boundary_error:
            raise RecoveryGap(boundary_error)
    recovery_state.update_chat(job, task, status='completed', error_code='')
    recovery_state.clear_pages(job, task)


def _flush(client):
    from cli_anything.wecom_gui.core import edge_worker
    edge_worker.flush_registrations(client)
    edge_worker.flush_inbound(client)
    edge_worker.flush_command_results(client)


def step(client):
    """Run one resumable unit after the previous normal tick has ended."""
    job = recovery_state.current()
    if not job:
        return {'ok': True}
    if job['next_attempt_at'] > time.time():
        return {'ok': True, 'recovery': recovery_state.snapshot()}
    try:
        if job['desired_mode'] == 'normal' and not job['begin_attempted'] and not job['remote_active']:
            with recovery_state.control_lock():
                if recovery_state.current()['desired_mode'] == 'normal':
                    recovery_state.update(job['id'], release_pending=0, phase='resumed')
            return {'ok': True, 'recovery': recovery_state.snapshot()}
        if not getattr(client, 'supports_history_recovery', False):
            raise RecoveryGap('central_history_recovery_upgrade_required')
        if job['desired_mode'] == 'normal':
            _flush(client)
            # A queued historical registration must not arrive as live work
            # after the central barrier is released.
            with edge_state._connect() as conn:
                unregistered = conn.execute("""SELECT count(*) FROM edge_inbound_events
                    WHERE status != 'delivered' AND registered_direction=''""").fetchone()[0]
            if unregistered:
                _publish(job, 'uploading', error='recovery_registrations_pending')
                return {'ok': True, 'recovery': recovery_state.snapshot()}
            # Serialize only the boundary handshake with user control changes.
            # A recovery click arriving during end gets a fresh ID after ACK.
            with recovery_state.control_lock():
                latest = recovery_state.current()
                if latest['id'] == job['id'] and latest['desired_mode'] == 'normal':
                    client.history_recovery(job['id'], 'end')
                    recovery_state.update(job['id'], release_pending=0, remote_active=0, phase='resumed')
            return {'ok': True, 'recovery': recovery_state.snapshot()}
        if not job['remote_active']:
            recovery_state.update(job['id'], begin_attempted=1)
            client.history_recovery(job['id'], 'begin')
            recovery_state.update(job['id'], remote_active=1, attempts=0, next_attempt_at=0)
        recovery_state.adopt_unregistered(job['id'])
        _flush(client)
        if job['desired_mode'] == 'paused':
            view = recovery_state.snapshot()
            if job['status'] == 'partial' and not any(view[k] for k in ('gaps', 'pending_uploads', 'pending_media', 'pending_direction')):
                recovery_state.update(job['id'], status='completed')
                job['status'] = 'completed'
            _publish(job, job['status'] if job['status'] in {'completed', 'partial'} else 'paused')
            return {'ok': True, 'recovery': recovery_state.snapshot()}
        recovery_state.update(job['id'], status='running')
        recovery_state.resume_paused_media(job['id'])
        if not job['discovery_complete']:
            try:
                _discovery(job)
                recovery_state.update(job['id'], discovery_error='')
            except RecoveryGap as exc:
                recovery_state.update(job['id'], discovery_complete=1, discovery_error=str(exc))
            return {'ok': True, 'recovery': recovery_state.snapshot()}
        tasks = recovery_state.chats(job['id'])
        task = next((t for t in tasks if t['status'] in {'pending', 'reading'}), None)
        if task:
            try:
                _recover_chat(job, task)
            except RecoveryGap as exc:
                recovery_state.update_chat(job, task, status='gap', error_code=str(exc))
                _publish(job, 'gap', label=task['row'].get('title', ''), error=str(exc))
            _flush(client)
            _publish(job, 'uploading')
        elif job['discovery_pass'] == 0:
            # Revisit after list reordering and new arrivals. Counts remain
            # cumulative; durable identities prevent duplicate registration.
            recovery_state.update(job['id'], discovery_complete=0, discovery_pass=1)
            with recovery_state.transaction() as conn:
                conn.execute("UPDATE edge_history_recovery_chat SET status='pending' WHERE recovery_id=? AND status='completed'", (job['id'],))
        else:
            view = recovery_state.snapshot()
            result = 'partial' if view['gaps'] or view['pending_uploads'] or view['pending_media'] or view['pending_direction'] else 'completed'
            recovery_state.finish(job['id'], result)
            _publish(job, result)
    except RecoveryPaused:
        pass
    except RecoveryGap as exc:
        recovery_state.finish(job['id'], 'failed')
        _publish(job, 'paused', error=str(exc))
    except Exception as exc:
        # Keep only a bounded code in operational state, never remote response
        # text, credentials, or customer content from exception messages.
        _publish(job, 'network_retry', error=type(exc).__name__)
        attempts = job['attempts'] + 1
        recovery_state.update(job['id'], attempts=attempts, next_attempt_at=time.time() + min(60, 2 ** min(attempts, 6)))
    return {'ok': True, 'recovery': recovery_state.snapshot()}

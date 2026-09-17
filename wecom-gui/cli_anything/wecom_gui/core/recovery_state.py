"""Durable, local-only checkpoints for explicitly requested history recovery."""

from __future__ import annotations

import json
import fcntl
import time
import uuid
from contextlib import contextmanager

from cli_anything.wecom_gui.core import edge_state, state


@contextmanager
def control_lock():
    with (state.state_dir() / 'recovery-control.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def transaction():
    with edge_state._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS edge_history_recovery (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            id TEXT NOT NULL, desired_mode TEXT NOT NULL, status TEXT NOT NULL,
            phase TEXT NOT NULL, remote_active INTEGER NOT NULL DEFAULT 0,
            release_pending INTEGER NOT NULL DEFAULT 0,
            discovery_complete INTEGER NOT NULL DEFAULT 0,
            discovery_pass INTEGER NOT NULL DEFAULT 0,
            conversation_label TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            updated_at REAL NOT NULL)""")
        edge_state._ensure_column(conn, 'edge_history_recovery', 'discovery_error', "TEXT NOT NULL DEFAULT ''")
        edge_state._ensure_column(conn, 'edge_history_recovery', 'next_attempt_at', 'REAL NOT NULL DEFAULT 0')
        edge_state._ensure_column(conn, 'edge_history_recovery', 'attempts', 'INTEGER NOT NULL DEFAULT 0')
        edge_state._ensure_column(conn, 'edge_history_recovery', 'begin_attempted', 'INTEGER NOT NULL DEFAULT 0')
        edge_state._ensure_column(conn, 'edge_history_recovery', 'media_retry_requested', 'INTEGER NOT NULL DEFAULT 1')
        conn.execute("""CREATE TABLE IF NOT EXISTS edge_history_recovery_chat (
            recovery_id TEXT NOT NULL, task_key TEXT NOT NULL, row_json TEXT NOT NULL,
            conversation_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT NOT NULL DEFAULT '', pages INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL, PRIMARY KEY(recovery_id, task_key))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS edge_history_recovery_page (
            recovery_id TEXT NOT NULL, task_key TEXT NOT NULL, page INTEGER NOT NULL,
            payload_json TEXT NOT NULL, PRIMARY KEY(recovery_id, task_key, page))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS edge_history_recovery_message (
            recovery_id TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(recovery_id, event_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS edge_history_recovery_boundary (
            conversation_key TEXT PRIMARY KEY, reason TEXT NOT NULL,
            updated_at REAL NOT NULL)""")
        yield conn


def current() -> dict | None:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM edge_history_recovery WHERE singleton = 1").fetchone()
        return dict(row) if row else None


def request_start() -> dict:
    with control_lock():
        return _request_start()


def _request_start() -> dict:
    now = time.time()
    with transaction() as conn:
        old = conn.execute("SELECT * FROM edge_history_recovery WHERE singleton = 1").fetchone()
        if old and (old['desired_mode'] != 'normal' or old['release_pending']):
            if old['desired_mode'] == 'recovery' and old['status'] in {'requested', 'running'}:
                return dict(old)
            recovery_id = old['id']
            conn.execute("""UPDATE edge_history_recovery SET desired_mode='recovery',
                status='requested', phase='preflight', error_code='', discovery_error='', discovery_complete=0,
                discovery_pass=0, attempts=0, next_attempt_at=0, media_retry_requested=1, updated_at=? WHERE singleton=1""", (now,))
            conn.execute("""UPDATE edge_history_recovery_chat SET status='pending', error_code=''
                WHERE recovery_id=? AND status != 'completed'""", (recovery_id,))
            if old['status'] in {'completed', 'partial'}:
                conn.execute("UPDATE edge_history_recovery_chat SET status='pending' WHERE recovery_id=?", (recovery_id,))
        else:
            recovery_id = str(uuid.uuid4())
            conn.execute("""INSERT OR REPLACE INTO edge_history_recovery
                (singleton,id,desired_mode,status,phase,release_pending,created_at,updated_at)
                VALUES (1,?,'recovery','requested','preflight',1,?,?)""", (recovery_id, now, now))
        # Completed jobs have no resumable cursors. Keep the active job only;
        # the message ledger and upload spool remain the durable history.
        conn.execute("DELETE FROM edge_history_recovery_page WHERE recovery_id != ?", (recovery_id,))
        conn.execute("DELETE FROM edge_history_recovery_chat WHERE recovery_id != ?", (recovery_id,))
        conn.execute("DELETE FROM edge_history_recovery_message WHERE recovery_id != ?", (recovery_id,))
        return dict(conn.execute("SELECT * FROM edge_history_recovery WHERE singleton=1").fetchone())


def update(recovery_id: str, **values) -> None:
    allowed = {'desired_mode', 'status', 'phase', 'remote_active', 'release_pending',
               'discovery_complete', 'discovery_pass', 'conversation_label', 'error_code', 'discovery_error',
               'attempts', 'next_attempt_at', 'begin_attempted'}
    if not values.keys() <= allowed:
        raise ValueError('unsupported recovery field')
    with transaction() as conn:
        conn.execute(f"UPDATE edge_history_recovery SET {','.join(key + '=?' for key in values)}, updated_at=? WHERE id=?",
                     (*values.values(), time.time(), recovery_id))


def request_pause() -> dict:
    with control_lock():
        job = current()
        if job and (job['desired_mode'] != 'normal' or job['release_pending']):
            update(job['id'], desired_mode='paused', status='paused', phase='paused')
        return snapshot()


def request_normal() -> None:
    with control_lock():
        job = current()
        if job and job['release_pending']:
            update(job['id'], desired_mode='normal', phase='resuming', attempts=0, next_attempt_at=0)


def finish(recovery_id: str, outcome: str) -> None:
    with transaction() as conn:
        conn.execute("""UPDATE edge_history_recovery SET desired_mode='paused', status=?, updated_at=?
            WHERE id=? AND desired_mode='recovery'""", (outcome, time.time(), recovery_id))


def active() -> bool:
    job = current()
    return bool(job and (job['desired_mode'] != 'normal' or job['release_pending']))


def should_continue(recovery_id: str) -> bool:
    job = current()
    return bool(job and job['id'] == recovery_id and job['desired_mode'] == 'recovery')


def add_chat(recovery_id: str, row: dict) -> bool:
    key = state.conversation_key_for_row(row)
    # Keep operational identity only; list previews may contain customer text.
    saved = {k: row[k] for k in ('title', 'tags', 'source', 'external_user_id') if k in row}
    with transaction() as conn:
        if row.get('external_user_id'):
            existing = conn.execute('SELECT task_key FROM edge_history_recovery_chat WHERE recovery_id=? AND conversation_key=?',
                                    (recovery_id, state.conversation_key_for_uid(row['external_user_id']))).fetchone()
            if existing:
                conn.execute('UPDATE edge_history_recovery_chat SET row_json=? WHERE recovery_id=? AND task_key=?',
                             (json.dumps(saved, ensure_ascii=False), recovery_id, existing['task_key']))
                return False
        result = conn.execute("""INSERT OR IGNORE INTO edge_history_recovery_chat
            (recovery_id,task_key,row_json,updated_at) VALUES (?,?,?,?)""",
            (recovery_id, key, json.dumps(saved, ensure_ascii=False), time.time()))
        return result.rowcount == 1


def chats(recovery_id: str, *, status: str | None = None) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute("SELECT * FROM edge_history_recovery_chat WHERE recovery_id=?" +
                            (' AND status=?' if status else '') + ' ORDER BY rowid',
                            (recovery_id, status) if status else (recovery_id,)).fetchall()
        return [{**dict(row), 'row': json.loads(row['row_json'])} for row in rows]


def update_chat(job: dict, task: dict, **values) -> None:
    if not values.keys() <= {'status', 'error_code', 'pages', 'conversation_key'}:
        raise ValueError('unsupported recovery chat field')
    with transaction() as conn:
        conn.execute(f"UPDATE edge_history_recovery_chat SET {','.join(k + '=?' for k in values)}, updated_at=? WHERE recovery_id=? AND task_key=?",
                     (*values.values(), time.time(), job['id'], task['task_key']))


def clear_pages(job: dict, task: dict) -> None:
    with transaction() as conn:
        conn.execute('DELETE FROM edge_history_recovery_page WHERE recovery_id=? AND task_key=?',
                     (job['id'], task['task_key']))


def save_page(job: dict, task: dict, index: int, page: dict) -> None:
    with transaction() as conn:
        conn.execute('INSERT OR REPLACE INTO edge_history_recovery_page VALUES (?,?,?,?)',
                     (job['id'], task['task_key'], index, json.dumps(page, ensure_ascii=False)))


def load_page(job: dict, task: dict, index: int) -> dict:
    with transaction() as conn:
        row = conn.execute('SELECT payload_json FROM edge_history_recovery_page WHERE recovery_id=? AND task_key=? AND page=?',
                           (job['id'], task['task_key'], index)).fetchone()
        return json.loads(row[0])


def boundary_reason(conversation_key: str) -> str:
    with transaction() as conn:
        row = conn.execute('SELECT reason FROM edge_history_recovery_boundary WHERE conversation_key=?',
                           (conversation_key,)).fetchone()
        return row[0] if row else ''


def remember_boundary(conversation_key: str, reason: str) -> None:
    with transaction() as conn:
        conn.execute('INSERT OR REPLACE INTO edge_history_recovery_boundary VALUES (?,?,?)',
                     (conversation_key, reason, time.time()))


def confirm_boundary(conversation_key: str) -> None:
    with transaction() as conn:
        conn.execute('DELETE FROM edge_history_recovery_boundary WHERE conversation_key=?', (conversation_key,))


def snapshot(*, include_details: bool = False) -> dict:
    with transaction() as conn:
        row = conn.execute('SELECT * FROM edge_history_recovery WHERE singleton=1').fetchone()
        if not row:
            return {'status': 'idle', 'desired_mode': 'normal'}
        job = dict(row)
        has_ledger = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='edge_message_ledger'").fetchone()
        system_notice = ("EXISTS (SELECT 1 FROM edge_message_ledger l WHERE "
                         "e.dedupe_key=l.conversation_key||':'||l.event_hash "
                         "AND l.capture_status='ignored' AND l.direction='system')") if has_ledger else '0'
        counts = {r[0]: r[1] for r in conn.execute('SELECT status, count(*) FROM edge_history_recovery_chat WHERE recovery_id=? GROUP BY status', (job['id'],))}
        stats = conn.execute("""SELECT count(*) AS total,
            coalesce(sum(registered_direction != '' OR status='delivered'),0) AS registered,
            coalesce(sum(status != 'delivered'),0) AS pending_uploads,
            coalesce(sum(status='waiting_media'),0) AS pending_media
            FROM edge_inbound_events WHERE json_extract(payload_json,'$.message.source.recovery_id')=?
            OR client_event_id IN (SELECT event_id FROM edge_history_recovery_message WHERE recovery_id=?)""",
            (job['id'], job['id'])).fetchone()
        direction_pending = conn.execute(f"""SELECT count(*) FROM edge_inbound_events e
            WHERE (json_extract(payload_json,'$.message.source.recovery_id')=?
                OR client_event_id IN (SELECT event_id FROM edge_history_recovery_message WHERE recovery_id=?))
            AND json_extract(payload_json,'$.message.direction')='unknown'
            AND NOT ({system_notice})""", (job['id'], job['id'])).fetchone()[0]
        details = {}
        if include_details:
            # Local UI only. Never include message previews in telemetry or worker logs.
            has_media_state = has_ledger and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='edge_media_capture_state'").fetchone()
            media_state = ("LEFT JOIN edge_message_ledger l ON e.dedupe_key=l.conversation_key||':'||l.event_hash "
                           "LEFT JOIN edge_media_capture_state m ON m.event_hash=l.event_hash") if has_media_state else ''
            media_errors = "coalesce(m.last_error,'') AS media_error, coalesce(m.paused_reason,'') AS media_paused_reason" if has_media_state else "'' AS media_error, '' AS media_paused_reason"
            tasks = conn.execute("""SELECT row_json,status,error_code,pages FROM edge_history_recovery_chat
                WHERE recovery_id=? ORDER BY updated_at DESC, rowid DESC LIMIT 20""", (job['id'],)).fetchall()
            messages = conn.execute(f"""SELECT payload_json,media_json,status,registered_direction,
                {system_notice} AS system_notice, {media_errors} FROM edge_inbound_events e {media_state}
                WHERE json_extract(payload_json,'$.message.source.recovery_id')=?
                OR client_event_id IN (SELECT event_id FROM edge_history_recovery_message WHERE recovery_id=?)
                ORDER BY e.id DESC LIMIT 20""", (job['id'], job['id'])).fetchall()
            details['recent_chats'] = [{'title': str(json.loads(r['row_json']).get('title') or '')[:128],
                'status': r['status'], 'error_code': r['error_code'], 'pages': r['pages']} for r in tasks]
            details['recent_messages'] = []
            for row in messages:
                event = json.loads(row['payload_json'])
                message = event.get('message') or {}
                frames = [item for item in json.loads(row['media_json']) if item.get('capture_mode') == 'single_frame']
                details['recent_messages'].append({
                    'title': str((event.get('conversation') or {}).get('title') or '')[:128],
                    'text': str(message.get('text') or ('[图片]' if message.get('media') else ''))[:160],
                    'direction': 'system' if row['system_notice'] else message.get('direction') or 'unknown',
                    'observed_at': str(event.get('occurred_at') or ''),
                    'status': row['status'], 'registered': bool(row['registered_direction']) or row['status'] == 'delivered',
                    'media_error': str(row['media_error'])[:96] if row['status'] == 'waiting_media' else '',
                    'media_paused_reason': str(row['media_paused_reason'])[:96] if row['status'] == 'waiting_media' else '',
                    'media_note': ('动态表情截图' if all(item.get('frame_kind') == 'animated-sticker' for item in frames)
                                   else '图片单帧截图') if frames else '',
                })
            details['discovery_error'] = job['discovery_error']
        return {k: job[k] for k in ('id','desired_mode','status','phase','conversation_label','error_code','updated_at')} | details | {
            'hold_normal_operation': job['desired_mode'] != 'normal' or bool(job['release_pending']),
            'discovered': sum(counts.values()), 'completed': counts.get('completed', 0),
            'gaps': counts.get('gap', 0) + int(bool(job['discovery_error'])), 'registered': stats['registered'],
            'pending_uploads': stats['pending_uploads'], 'pending_media': stats['pending_media'],
            'pending_direction': direction_pending,
        }


def link_messages(recovery_id: str, entries: list[dict]) -> None:
    # The spool client_event_id and the ledger's message event_id are distinct.
    with transaction() as conn:
        conn.executemany("""INSERT OR IGNORE INTO edge_history_recovery_message
            SELECT ?, client_event_id FROM edge_inbound_events WHERE dedupe_key=?""",
            [(recovery_id, f"{entry['conversation_key']}:{entry['event_hash']}") for entry in entries])


def adopt_unregistered(recovery_id: str) -> None:
    """Classify unsent old spool rows before upload; never mutate a sent contract."""
    from cli_anything.wecom_gui.core import edge_message_ledger
    with edge_message_ledger.transaction() as conn:
        for row in conn.execute("""SELECT id, payload_json FROM edge_inbound_events
            WHERE registration_started=0 AND status != 'delivered'
            AND json_extract(payload_json,'$.message.source.stream_id') IS NOT NULL
            AND json_extract(payload_json,'$.message.source.recovery_id') IS NULL""").fetchall():
            event = json.loads(row['payload_json'])
            event['message']['source']['recovery_id'] = recovery_id
            conn.execute('UPDATE edge_inbound_events SET payload_json=? WHERE id=?',
                         (json.dumps(event, ensure_ascii=False), row['id']))
            conn.execute('UPDATE edge_message_ledger SET recovery_id=? WHERE event_id=?',
                         (recovery_id, event['message']['id']))


def resume_paused_media(recovery_id: str) -> None:
    """An explicit button press permits one fresh retry budget, not a new identity."""
    from cli_anything.wecom_gui.core import edge_message_ledger
    with edge_message_ledger.transaction() as conn:
        job = conn.execute('SELECT media_retry_requested FROM edge_history_recovery WHERE id=?', (recovery_id,)).fetchone()
        if not job or not job[0]:
            return
        conn.execute("""UPDATE edge_media_capture_state SET attempts=0, next_attempt_at=0,
            paused_reason='', last_error='' WHERE (paused_reason != '' OR attempts >= ?)
            AND event_hash IN (SELECT event_hash FROM edge_message_ledger WHERE capture_status='pending_media')""",
            (edge_message_ledger.MEDIA_ATTEMPT_LIMIT,))
        conn.execute('UPDATE edge_history_recovery SET media_retry_requested=0 WHERE id=?', (recovery_id,))

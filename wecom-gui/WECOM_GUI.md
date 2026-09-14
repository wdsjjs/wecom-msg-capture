# WeCom GUI Agent SOP

This SOP covers the macOS desktop automation agent in `wecom-gui`.

The agent is intentionally conservative. It does not use an official WeCom
messaging API; it operates the visible desktop app through Accessibility,
AppleScript, clipboard paste, and a local SQLite queue.

## Operating Modes

The legacy AI modes below are separate from the central-channel client. The
current Mac edge receives central delivery commands; it does not run a local AI.

## Manual History Recovery

The native control panel provides **补齐聊天记录** and **暂停补录**. These use the
same supervisor and edge process as normal collection:

```bash
./scripts/wecom-control recover-history
./scripts/wecom-control pause-recovery
./scripts/wecom-control runtime-status
# Explicitly resume ordinary collection and command delivery after recovery:
./scripts/wecom-control start-edge
```

Deploy the matching `uda-kefu` backend first. Its device heartbeat must advertise
`historyRecoveryV1`; older servers cannot accept recovery events safely. A Mac
worker started before this upgrade must be stopped and started once. The panel
reports `edge_worker_restart_required` instead of claiming an old worker began
recovery. Opening/installing the panel never starts collection automatically.

Recovery begins at a normal tick boundary, after any already submitting delivery.
The button and the physical submit share a local lock: a command still downloading
attachments is stopped before submission if recovery was requested meanwhile.
The central `POST /api/wecom-channel/edge/recovery` boundary durably pauses new
command leases and AI work. Recovery does not pull commands. A lost response is
retried with the same recovery ID; failures back off up to 60 seconds. Only an
explicit normal start releases the boundary, after pending message registrations
are acknowledged. Already-registered images can remain pending; their immutable
history-only identity prevents later attachment repairs from triggering replies.
Paused/finished recovery continues network retries but does not navigate WeCom.

Discovery includes read conversations and known contact bindings. Each chat is
read in windows of at most 20 AX rows with overlapping pages. Recovery walks
backward until it can join the persisted ordered ledger, then registers messages
chronologically. Native cursors are revalidated against conversation/table/row
identity. A resumed visit discards native cursors and uses persisted message IDs.
Pictures use the same-page identity and pixel checks before and after capture.
Older pending images and uncertain directions determine how far recovery goes
back, even beyond the ordinary 200-entry matching window. Clicking recovery again
grants paused images one new retry budget while preserving their event identities
and pinned pixel evidence. Joining a recent known page does not hide older work.

There are bounded limits of 100 list pages and 100 history pages per visit. Limits,
missing anchors, inaccessible conversations and unavailable older history are
reported as gaps. A scrollbar at the top alone does not prove the complete WeCom
history was loaded. For a first-seen chat, accessible history is backfilled with
an explicit incomplete-boundary result when its beginning cannot be proven.
This result persists across retries and new recovery jobs. Only a verified start
matching the first ledger row can clear it; a newly created recent anchor cannot.
Earlier rows discovered before that first registered row remain an explicit gap;
recovery never renumbers already registered messages to force them into history.
If a page straddles that boundary, its uniquely matched registered suffix can still
receive image repairs. The final recheck must also confirm the latest boundary;
an `ok` page response with an unverified end cannot produce a completed result.
Recovery is intended for records available in the logged-in desktop client;
deleted/expired/server-only records cannot be promised. `occurred_at` remains the
first local observation time when the original WeCom timestamp is unavailable.

`state.sqlite` holds the recovery request, per-chat checkpoints and temporary
20-row pages. The normal ordered ledger and upload spool retain message identity,
registration acknowledgement, direction and attachment progress. Telemetry shows
counts, conversation labels and reason codes only, never message bodies or keys.
Recovery events retain `message.source.recovery_id` through retries and repairs;
the central history-only guard prevents old questions from triggering AI replies.

Focused offline validation (no customer sends or production writes):

```bash
python -m pytest cli_anything/wecom_gui/tests/test_history_recovery.py \
  cli_anything/wecom_gui/tests/test_recovery_send_gate.py \
  cli_anything/wecom_gui/tests/test_recovery_ax_paging.py \
  cli_anything/wecom_gui/tests/test_desktop_render.py -q
```

## Image Loading And Retry

A digest-checked `rgb32-v2` sample containing only a near-white neutral surface
is insufficient image identity evidence. Collection retains the AX row identity
and registers the message, but waits for content pixels without spending capture
attempts or blocking later text registrations. A genuinely blank white image is
indistinguishable from this loading surface and remains pending.

Older releases could pin that loading frame and later report
`media_fingerprint_changed` when the actual image appeared. Such a loading anchor
can be replaced only on the exact original AX row, including its process identity,
after normal ordered snapshot validation. Real content anchors retain the existing
pixel checks; another row or restarted process cannot repair a blank anchor.
Already exhausted retries require explicit `edge-channel resume-media <event-id>`.
Attachment repair preserves the original event ID, registration source and time.

## Legacy AI Modes

Use the modes in this order:

1. `dry-run`: read chats and generate drafts without sending.
2. `review`: require a human to approve each draft in the LAN review page.
3. `auto`: send drafts automatically after the send-time context check.

Recommended production default:

```bash
WECOM_AGENT_MODE=review ./scripts/wecom-agent start
./scripts/wecom-agent review-start
```

Use `auto` only after the business has explicitly approved fully automatic
responses.

## Safety Invariants

- GUI operations are serialized by `state.gui_lock()`.
- AI calls run outside the GUI lock.
- Sending always reopens the target chat and checks that the latest customer
  message still matches the message used for drafting.
- If the customer sent a newer message, the draft is marked `skipped`.
- `review` approval never sends directly from the browser.
- The default scanner requires `@微信` and unread status.
- External groups and system/department rows are ignored by default.
- Local queue and event logs are audit data under
  `~/.cli-anything-wecom-gui/`.

## Main Flow

```text
scan visible inbox rows
  -> filter external-contact customer candidates
  -> enqueue changed conversations
  -> open one pending chat under GUI lock
  -> read recent visible messages and latest customer turn
  -> draft reply concurrently
  -> mark queue item ready
  -> auto mode: claim ready
     review mode: wait for web approval, then claim approved
  -> reopen chat under GUI lock
  -> recheck latest customer message
  -> paste and submit reply
  -> verify reply is visible
  -> mark done
```

Queue state meanings:

```text
pending       waiting to be opened
reading       current GUI read step
drafting      AI request in progress
ready         draft generated; in review mode waits for human approval
approved      human approved; agent may send after recheck
sending       current GUI send step
done          completed
skipped       intentionally not sent
failed        error
```

## First Validation On A Mac

Open WeCom and make sure the target account is logged in. Then run:

```bash
npm run doctor
python -m cli_anything.wecom_gui --json inbox scan --limit 5
python -m cli_anything.wecom_gui --json chat read --last 12
```

Expected:

- WeCom is detected as running.
- Accessibility checks pass.
- Inbox rows include customer conversations with expected tags.
- `chat read` returns recent messages with inferred `用户` / `客服` roles.

If Accessibility fails, grant permission to the terminal app, Codex app, or
LaunchAgent runner that starts the process. Restart that app before retrying.

## Start And Stop

Start agent:

```bash
./scripts/wecom-agent start
```

Stop agent:

```bash
./scripts/wecom-agent stop
```

Logs:

```bash
./scripts/wecom-agent logs
```

## Native Mac Agent

The compact native panel follows the system light/dark appearance. Normal command
polling is shown as running, with pending history alignment reported separately.
It distinguishes the local WeCom process, message collection and central connection;
connection status does not claim that AI reception is enabled. Recovery details
expand while recovery runs and can be collapsed independently. The reception button
pauses an active worker or explicitly resumes normal operation after recovery.
Logs and internal phase/error codes remain available through details and the menu.

Install the AppKit menu-bar client to `~/Applications`:

```bash
./scripts/install-desktop-client
```

The client shows a Dock/menu-bar control surface and only reads redacted local
operational state. The AI reply is generated in the central service and is not
a Mac process. It never provides a direct customer read/send action.

The breathing indicator uses Core Animation independently of state refreshes.
Text controls are retained and updated when status changes; animation does not
redraw or reconstruct text attributes. Native rendering regression tests run
without reading WeCom, starting an edge worker, or sending messages:

```bash
python -m pytest cli_anything/wecom_gui/tests/test_desktop_render.py -q
```

All local lifecycle actions go through one script:

```bash
./scripts/wecom-control start       # only opens the control panel
./scripts/wecom-control start-wecom # starts Enterprise WeChat
./scripts/wecom-control start-edge  # starts the edge channel
./scripts/wecom-control stop        # stops the edge channel, WeChat, and panel
./scripts/wecom-control status
```

The normal `启动客服.command` entry point only opens the control panel. Use its
buttons to start or stop WeChat and the edge channel, so opening the desktop
client cannot unexpectedly resume message processing. `start-all` remains
available for an explicit one-shot startup. Installation does not start the
edge channel. Existing channel commands remain compatible and still delegate
to the same supervisor:

```bash
./scripts/wecom-channel status
./scripts/wecom-supervisor start edge
```

One-time foreground dry run:

```bash
python -u -m cli_anything.wecom_gui agent --mode dry-run --poll 0.5 --scan-interval 1 --inbox-limit 5 --max-drafts 4 --last 12 --log-interval 5
```

Export the current WeCom window payload that would be passed to the Agent,
without enqueueing, calling AI, or sending anything:

```bash
python -m cli_anything.wecom_gui --json agent-input --last 12 --output /tmp/wecom-agent-input.json
```

The JSON contains:

- `read`: raw current-window read result, including message roles and hash.
- `agent_input`: messages and customer identity passed to the GUI Agent layer.
- `csbot_input`: query and context passed to the csbot autonomous Agent.

## New Customer Welcome

Newly added WeCom customers can appear without an unread badge. The agent treats
sidebar previews like this as a fixed welcome trigger:

```text
你已添加了三水儿，现在可以开始聊天了。
```

Configure the fixed welcome text before using `auto` mode:

```bash
export WECOM_GUI_WELCOME_MESSAGE='替换成固定欢迎话术'
```

The welcome draft is marked `reply_source=welcome`. It does not call the ordinary
AI drafter. With `WECOM_GUI_REQUIRE_UNREAD=1`, the scanner still enqueues this
new-customer system preview even when the row has no red unread badge.

## Review Server

Start:

```bash
./scripts/wecom-agent review-start
```

The printed URL includes the token:

```text
http://<LAN-IP>:8122/
```

The page shows `ready` drafts. The reviewer can approve or reject. Approved
items become `approved`; rejected items become `skipped` with
`review_rejected`.

API endpoints:

```text
GET  /api/review/items?status=ready
GET  /api/review/counts
POST /api/review/items/{id}/save
POST /api/review/items/{id}/regenerate
POST /api/review/items/{id}/approve
POST /api/review/items/{id}/reject
```

The review page reads live queue items from
`~/.cli-anything-wecom-gui/state.sqlite`; it does not serve mock items.

## Central Channel Edge Client

`edge-channel` is independent from the AI/review queue. It is the unattended
Mac executor for the central customer-service channel: unread external direct
messages are first spooled in SQLite, then uploaded; centrally issued text or
image commands are re-read against the current customer message before send.

```bash
python -m cli_anything.wecom_gui edge-channel status
python -m cli_anything.wecom_gui edge-channel run --once
python -m cli_anything.wecom_gui edge-channel run
```

Set `WECOM_CHANNEL_BASE_URL` (HTTPS only), `WECOM_CHANNEL_DEVICE_ID`, and
`WECOM_CHANNEL_DEVICE_TOKEN` in `.env.local`. Current single-device mode does
not bind the Mac to a WeCom account; tenant and channel-account isolation must
be introduced before multi-tenant use.
The client records command ids before GUI execution and reports an unconfirmed
post-click state as `needs_reconciliation`; it never blindly sends it again.
For text commands, an `AXConfirmAction` success is only a submit signal: the
client waits for a newly visible right-side staff bubble before reporting
`succeeded`. It does not use a global clipboard/Return fallback for central
commands, so a stale input cannot be appended and sent to the wrong chat.

After the central channel has been deployed with `message.direction` support,
set `WECOM_GUI_CAPTURE_OUTBOUND=1` and restart the edge client to upload new
manual staff bubbles from the currently open conversation. It defaults to `0`
to prevent an older central service from treating staff messages as inbound
customer messages. The first observation establishes a local baseline; it does
not backfill the visible history.

SQLite schema notes:

- `reply_queue` includes `handoff_type` and `handoff_reason` for direct/AI
  handoff review items. These columns are added automatically on startup.
- `conversation_messages` includes persisted `message_type` values
  (`customer`, `reply`, or `unknown`) plus `media_json`; review image URLs are
  served only from recorded media rows and never from arbitrary file paths.

## Configuration

Primary file:

```text
wecom-gui/.env.local
```

Common production values:

```env
WECOM_GUI_APP_NAME=企业微信
WECOM_GUI_AI_PROVIDER=pi
WECOM_AGENT_MODE=review
WECOM_AGENT_INBOX_LIMIT=8
WECOM_AGENT_MAX_DRAFTS=10
WECOM_AGENT_TEXT_WORKERS=7
WECOM_AGENT_IMAGE_WORKERS=3
WECOM_GUI_REQUIRE_WECHAT_TAG=1
WECOM_GUI_REQUIRE_UNREAD=1
WECOM_GUI_ALLOW_UNTAGGED=0
WECOM_GUI_INCLUDE_EXTERNAL_GROUPS=0
WECOM_REVIEW_HOST=0.0.0.0
WECOM_REVIEW_PORT=8122
```

Use `WECOM_GUI_CAPTURE_IMAGES=1` when image questions are in scope. Captured
images are stored under `WECOM_GUI_CAPTURE_IMAGE_DIR`.

## Troubleshooting

Queue:

```bash
npm run queue
python -m cli_anything.wecom_gui --json queue list --status ready
python -m cli_anything.wecom_gui --json queue list --status approved
```

Clear old completed jobs:

```bash
python -m cli_anything.wecom_gui --json queue clear --status done
```

Common failures:

- `osascript` or System Events is denied: grant Accessibility permission and
  restart the runner.
- Agent drafts but does not send: check for `stale_context`, which means the
  customer sent a newer message.
- Browser review page shows nothing: confirm the agent is in `review` mode and
  queue has `ready` items.
- `approved` items do not send: confirm the agent process is running and can
  access the WeCom window.
- Replies target the wrong chat: stop immediately, return to `dry-run`, and
  recalibrate `inbox scan`, `chat read`, and window geometry.

## Development Rules

- Do not hard-code secrets.
- Do not remove `dry-run`, review mode, or send-time context checks.
- Do not make browser approval directly operate the GUI.
- Do not allow multiple threads to click WeCom at once.
- Do not default to untagged contacts or external groups.
- Unit tests should monkeypatch GUI backends and avoid real desktop actions.

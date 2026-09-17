"""Generic WeCom desktop capture and transport CLI."""

from __future__ import annotations

import shlex

import os

import json

from pathlib import Path

import click

from cli_anything.wecom_gui import __version__

from cli_anything.wecom_gui.core import app as app_core

from cli_anything.wecom_gui.core import chat as chat_core

from cli_anything.wecom_gui.core import edge_state as edge_state_core

from cli_anything.wecom_gui.core import edge_message_ledger as edge_message_ledger_core

from cli_anything.wecom_gui.core import edge_worker as edge_worker_core

from cli_anything.wecom_gui.core import inbox as inbox_core

from cli_anything.wecom_gui.core import reply as reply_core

from cli_anything.wecom_gui.core import runtime_state as runtime_state_core

from cli_anything.wecom_gui.core import state as state_core

from cli_anything.wecom_gui.core import supervisor as supervisor_core

from cli_anything.wecom_gui.utils import output


def _load_env_local(path: str | Path = ".env.local") -> None:
    """Load simple KEY=VALUE pairs from .env.local without overriding env."""
    config_path = os.environ.get("WECOM_DESKTOP_CONFIG")
    if config_path:
        config_file = Path(config_path)
        if config_file.exists():
            config = json.loads(config_file.read_text(encoding="utf-8"))
            for key in (
                "WECOM_CHANNEL_BASE_URL",
                "WECOM_CHANNEL_DEVICE_ID",
                "WECOM_CHANNEL_DEVICE_TOKEN",
            ):
                value = config.get(key, "")
                if not isinstance(value, str):
                    raise ValueError("Invalid desktop device configuration")
                if value:
                    os.environ[key] = value
        return
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if not parts or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value


def _emit_or_fail(func, *args, message: str | None = None, **kwargs) -> None:
    """Run a command body and convert runtime failures to clean CLI errors."""
    try:
        output.emit(func(*args, **kwargs), message)
    except Exception as exc:
        output.error(str(exc), code=type(exc).__name__)
        raise click.exceptions.Exit(1)


@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON.")
@click.version_option(__version__, prog_name="cli-anything-wecom-gui")
@click.pass_context
def cli(ctx: click.Context, use_json: bool) -> None:
    """Drive WeCom desktop GUI for customer-service automation.

    Run without a subcommand to show available commands.
    """
    _load_env_local()
    output.USE_JSON = use_json
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
def doctor() -> None:
    """Check local WeCom GUI automation prerequisites."""
    _emit_or_fail(app_core.doctor, message="WeCom GUI automation doctor")


@cli.group()
def app() -> None:
    """Launch and focus the WeCom desktop app."""


@app.command()
@click.option(
    "--app-name", default=None, help="Override app name, e.g. WeCom or 企业微信."
)
def focus(app_name: str | None) -> None:
    """Focus the WeCom desktop app."""
    _emit_or_fail(app_core.focus, app_name, message="Focused WeCom")


@cli.group()
def inbox() -> None:
    """Scan visible inbox/conversation labels."""


@inbox.command("scan")
@click.option("--limit", default=30, show_default=True, type=click.IntRange(min=1))
@click.option("--app-name", default=None, help="Override app name.")
def inbox_scan(limit: int, app_name: str | None) -> None:
    """Scan visible conversation labels from the WeCom window."""
    _emit_or_fail(inbox_core.scan_visible, app_name, limit=limit)


@cli.group()
def chat() -> None:
    """Inspect the current WeCom chat."""


@chat.command("open")
@click.option("--name", required=True, help="Visible conversation label to click.")
@click.option("--app-name", default=None, help="Override app name.")
def chat_open(name: str, app_name: str | None) -> None:
    """Open a visible chat by exact label."""
    _emit_or_fail(inbox_core.open_by_name, name, app_name)


@chat.command("read")
@click.option(
    "--last",
    default=10,
    show_default=True,
    type=click.IntRange(min=1),
    help="Visible lines to return.",
)
@click.option("--app-name", default=None, help="Override app name.")
@click.option(
    "--capture-images",
    is_flag=True,
    help="Open image previews and capture image attachments.",
)
def chat_read(last: int, app_name: str | None, capture_images: bool) -> None:
    """Read recent visible text from the current chat window."""
    _emit_or_fail(
        chat_core.read_current,
        last=last,
        app_name=app_name,
        capture_images=capture_images,
    )


@cli.group()
def reply() -> None:
    """Send or stage replies in the current chat."""


@reply.command("send")
@click.option("--text", required=True, help="Reply text to paste/send.")
@click.option("--dry-run", is_flag=True, help="Do not touch the GUI.")
@click.option("--no-submit", is_flag=True, help="Paste text but do not press Return.")
def reply_send(text: str, dry_run: bool, no_submit: bool) -> None:
    """Paste a reply into the focused WeCom chat and optionally send it."""
    _emit_or_fail(reply_core.send_text, text, dry_run=dry_run, submit=not no_submit)


@cli.group("wecom")
def wecom_group() -> None:
    """Manage WeCom sidebar identity bindings."""


@wecom_group.command("bind")
@click.option(
    "--uid", required=True, help="WeCom external_user_id / UID from sidebar H5."
)
@click.option(
    "--customer-name", required=True, help="Visible conversation/customer name."
)
@click.option("--display-name", default="", help="Optional display name.")
@click.option("--source", default="cli", show_default=True)
def wecom_bind(uid: str, customer_name: str, display_name: str, source: str) -> None:
    """Bind a WeCom UID to a visible customer name."""
    _emit_or_fail(
        lambda: {
            "ok": True,
            "binding": state_core.bind_wecom_customer(
                uid=uid,
                customer_name=customer_name,
                display_name=display_name,
                source=source,
                raw={
                    "uid": uid,
                    "customer_name": customer_name,
                    "display_name": display_name,
                    "source": source,
                },
            ),
        }
    )


@wecom_group.command("lookup")
@click.option("--uid", default="", help="WeCom external_user_id / UID.")
@click.option("--customer-name", default="", help="Visible conversation/customer name.")
def wecom_lookup(uid: str, customer_name: str) -> None:
    """Lookup a WeCom UID/customer-name binding."""
    _emit_or_fail(
        lambda: {
            "ok": True,
            "binding": state_core.lookup_wecom_customer(
                uid=uid, customer_name=customer_name
            ),
        }
    )


@wecom_group.command("bindings")
@click.option("--limit", default=100, show_default=True, type=click.IntRange(min=1))
def wecom_bindings(limit: int) -> None:
    """List recent WeCom UID bindings."""
    _emit_or_fail(
        lambda: {
            "ok": True,
            "bindings": state_core.list_wecom_customer_bindings(limit=limit),
        }
    )


@cli.group("edge-channel")
def edge_channel_group() -> None:
    """Run the unattended central-channel client on this Mac."""


@cli.group("supervisor")
def supervisor_group() -> None:
    """Manage the controlled local edge-channel session."""


@supervisor_group.command("status")
def supervisor_status() -> None:
    _emit_or_fail(supervisor_core.status)


@supervisor_group.command("recover-history")
def supervisor_recover_history() -> None:
    """Start or resume historical capture without enabling message delivery."""
    _emit_or_fail(supervisor_core.recover_history)


@supervisor_group.command("pause-recovery")
def supervisor_pause_recovery() -> None:
    """Pause recovery after the current bounded GUI operation."""
    _emit_or_fail(supervisor_core.pause_recovery)


@supervisor_group.command("start")
@click.argument("service", type=click.Choice(["edge", "all"]))
def supervisor_start(service: str) -> None:
    _emit_or_fail(supervisor_core.action, service, "start")


@supervisor_group.command("stop")
@click.argument("service", type=click.Choice(["edge", "all"]))
def supervisor_stop(service: str) -> None:
    _emit_or_fail(supervisor_core.action, service, "stop")


@supervisor_group.command("restart")
@click.argument("service", type=click.Choice(["edge", "all"]))
def supervisor_restart(service: str) -> None:
    _emit_or_fail(supervisor_core.action, service, "restart")


@cli.group("runtime")
def runtime_group() -> None:
    """Read the redacted local runtime state for the native desktop client."""


@runtime_group.command("status")
@click.option(
    "--limit", default=20, show_default=True, type=click.IntRange(min=1, max=20)
)
def runtime_status(limit: int) -> None:
    _emit_or_fail(runtime_state_core.snapshot, limit=limit)


@edge_channel_group.command("status")
def edge_channel_status() -> None:
    """Show the local durable spool; this never contacts the central service."""
    _emit_or_fail(edge_state_core.edge_status)


@edge_channel_group.command("run")
@click.option(
    "--once", is_flag=True, help="Run one capture/upload/pull/send tick and exit."
)
@click.option("--poll", default=1.0, show_default=True, type=click.FloatRange(min=0.1))
@click.option(
    "--inbox-limit", default=30, show_default=True, type=click.IntRange(min=1, max=100)
)
@click.option(
    "--last", default=20, show_default=True, type=click.IntRange(min=1, max=100)
)
def edge_channel_run(once: bool, poll: float, inbox_limit: int, last: int) -> None:
    """Capture external direct chats and execute centrally issued commands."""
    if once:
        _emit_or_fail(edge_worker_core.run_once, inbox_limit=inbox_limit, last=last)
        return
    edge_worker_core.run_forever(poll_seconds=poll, inbox_limit=inbox_limit, last=last)


@edge_channel_group.command("resume-media")
@click.argument("event_id")
def edge_channel_resume_media(event_id: str) -> None:
    """Resume one paused image task locally; collection still verifies its identity."""
    with state_core.gui_lock():
        _emit_or_fail(edge_message_ledger_core.resume_media, event_id)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

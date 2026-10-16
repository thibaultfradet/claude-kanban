from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, Optional, Tuple

logger = logging.getLogger("kanban.notify")

_FALSE_VALUES = {"0", "false", "no"}
ENABLED = os.environ.get("KANBAN_NOTIFY", "1").strip().lower() not in _FALSE_VALUES
EVENTS = {
    e.strip()
    for e in os.environ.get("KANBAN_NOTIFY_EVENTS", "standby,a_valider,error").split(",")
    if e.strip()
}

TITLES = {
    "standby": "Claude Kanban — Question",
    "a_valider": "Claude Kanban — À valider",
    "error": "Claude Kanban — Erreur",
}
MAX_BODY_LENGTH = 200

_WINDOWS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_WINDOWS_SCRIPT = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:KANBAN_NOTIFY_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:KANBAN_NOTIFY_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_WINDOWS_APP_ID}').Show($toast)
"""


def build_message(event: str, project_name: str, ticket_id: int, ticket_title: str, reason: str = "") -> Tuple[str, str]:
    """Title and body for an event; the body is `<project> · #<id> <title>` (+ reason)."""
    body = f"{project_name} · #{ticket_id} {ticket_title}"
    if reason:
        body += f" — {reason}"
    return TITLES.get(event, "Claude Kanban"), body


def _applescript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _build_command(title: str, body: str) -> Optional[Tuple[list, Optional[Dict[str, str]]]]:
    """Return (cmd, env) for the current platform, or None if unsupported."""
    if sys.platform == "darwin":
        script = (
            f'display notification "{_applescript_escape(body)}" '
            f'with title "{_applescript_escape(title)}"'
        )
        return ["osascript", "-e", script], None
    if sys.platform == "win32":
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-Command", _WINDOWS_SCRIPT,
        ]
        env = {**os.environ, "KANBAN_NOTIFY_TITLE": title, "KANBAN_NOTIFY_BODY": body}
        return cmd, env
    if sys.platform.startswith("linux"):
        if shutil.which("notify-send"):
            return ["notify-send", title, body], None
        logger.debug("notify-send not found, skipping notification")
    return None


def notify(event: str, title: str, body: str) -> None:
    """Fire-and-forget native notification. Never raises, never blocks."""
    try:
        if not ENABLED or event not in EVENTS:
            return
        built = _build_command(title, body[:MAX_BODY_LENGTH])
        if built is None:
            return
        cmd, env = built
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except Exception as exc:
        logger.warning("notification failed: %s", exc)

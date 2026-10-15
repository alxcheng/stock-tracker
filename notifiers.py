"""Free notification channels. Each is enabled by a setting (settings.json, set
from the web page) or the matching environment variable, which takes precedence.

  ntfy      ntfy_topic / NTFY_TOPIC (server: ntfy_server / NTFY_SERVER, default https://ntfy.sh)
  Discord   discord_webhook_url / DISCORD_WEBHOOK_URL
  Telegram  telegram_bot_token + telegram_chat_id
  macOS     macos_notify / MACOS_NOTIFY (Mac only)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request


_settings: dict = {}


def _get(var: str):
    return os.environ.get(var) or _settings.get(var.lower())


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def ntfy(title: str, body: str, urgent: bool) -> None:
    server = (_get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    _post(
        f"{server}/{_get('NTFY_TOPIC')}",
        body.encode(),
        {
            "Title": title,
            "Tags": "chart_with_upwards_trend",
            "Priority": "high" if urgent else "default",
        },
    )


def discord(title: str, body: str, urgent: bool) -> None:
    content = f"**{title}**\n```\n{body}\n```"
    _post(
        _get("DISCORD_WEBHOOK_URL"),
        json.dumps({"content": content[:2000]}).encode(),
        {"Content-Type": "application/json", "User-Agent": "stochastic-tracker"},
    )


def telegram(title: str, body: str, urgent: bool) -> None:
    token = _get("TELEGRAM_BOT_TOKEN")
    _post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json.dumps(
            {"chat_id": _get("TELEGRAM_CHAT_ID"), "text": f"{title}\n\n{body}"}
        ).encode(),
        {"Content-Type": "application/json"},
    )


def macos(title: str, body: str, urgent: bool) -> None:
    script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=True)


CHANNELS = {
    "ntfy": (ntfy, ["NTFY_TOPIC"]),
    "discord": (discord, ["DISCORD_WEBHOOK_URL"]),
    "telegram": (telegram, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
    "macos": (macos, ["MACOS_NOTIFY"]),
}


def send(title: str, body: str, urgent: bool = True, settings: dict | None = None) -> list[str]:
    """Send to every configured channel; return the names that succeeded."""
    global _settings
    _settings = settings or {}
    sent = []
    for name, (fn, required) in CHANNELS.items():
        if name == "macos" and sys.platform != "darwin":
            continue
        if not all(_get(var) for var in required):
            continue
        try:
            fn(title, body, urgent)
            sent.append(name)
        except Exception as exc:  # one broken channel shouldn't block the rest
            print(f"[warn] {name} notification failed: {exc}", file=sys.stderr)
    return sent

"""Discord channel: one webhook post per announcement."""

from __future__ import annotations

import json
import urllib.request

TIMEOUT_SECONDS = 5.0


def post_message(webhook_url: str, text: str, role_ids: list[str]) -> None:
    """Post `text` to the webhook's channel, pinging every role in `role_ids`."""
    mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids)
    payload = {
        "content": f"{mentions} {text}".strip(),
        # Only the listed roles ping; nothing else in the text can.
        "allowed_mentions": {"parse": [], "roles": role_ids},
    }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # Cloudflare in front of Discord rejects urllib's default agent
            # (error 1010) before the request reaches the webhook.
            "User-Agent": "sandwich-pipeline",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
        pass

"""Discord channel: one webhook post per announcement."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request

TIMEOUT_SECONDS = 5.0
SYSTEM_CA_BUNDLE = "/etc/pki/tls/certs/ca-bundle.crt"


def post_message(
    webhook_url: str,
    text: str,
    *,
    role_ids: list[str],
    user_ids: list[str],
) -> None:
    """Post `text` to the webhook's channel, pinging every role and user listed."""
    mentions = [f"<@&{role_id}>" for role_id in role_ids]
    mentions += [f"<@{user_id}>" for user_id in user_ids]
    payload = {
        "content": " ".join([*mentions, text]),
        # Only the listed roles and users ping; nothing else in the text can.
        "allowed_mentions": {"parse": [], "roles": role_ids, "users": user_ids},
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
    cafile = SYSTEM_CA_BUNDLE if os.path.isfile(SYSTEM_CA_BUNDLE) else None
    context = ssl.create_default_context(cafile=cafile)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=context):
        pass

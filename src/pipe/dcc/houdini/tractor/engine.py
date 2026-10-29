"""What the Tractor engine says about a job, from its monitor endpoint, which
needs no password, unlike `tractor.api.query`."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request

TIMEOUT_SECONDS = 5.0
# Houdini's Python looks for certificates on SideFX's build machine.
SYSTEM_CA_BUNDLE = "/etc/pki/tls/certs/ca-bundle.crt"


def ended(job_id: int) -> bool:
    """Whether the job is done, deleted, or gone from the engine. A job stopped
    by an error hasn't ended: it may be retried. Raises OSError when the engine
    can't be reached, and ValueError when its reply can't be read."""
    url = f"https://{os.environ['TRACTOR_ENGINE']}/Tractor/monitor?q=jtree&jid={job_id}"
    cafile = SYSTEM_CA_BUNDLE if os.path.isfile(SYSTEM_CA_BUNDLE) else None
    context = ssl.create_default_context(cafile=cafile)
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS, context=context) as reply:
        users = json.load(reply).get("users", {})
    # The job's owner is the one key.
    for jobs in users.values():
        data = jobs[f"J{job_id}"]["data"]
        total, _active, done, _errors = data["nTasks"]
        return data.get("deletetime") is not None or done == total
    return True

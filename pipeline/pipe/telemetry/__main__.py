"""`python -m pipe.telemetry` dispatches to the local-stack orchestrator.

Subcommands: `up`, `catch-up`, `status` (see `local_stack.py`).
"""

from __future__ import annotations

import sys

from pipe.telemetry.local_stack import main

if __name__ == "__main__":
    sys.exit(main())

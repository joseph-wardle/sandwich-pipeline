"""`python -m core.telemetry` dispatches to the local-stack orchestrator.

Run from the pipeline checkout root with `pipe` on PYTHONPATH:

    PYTHONPATH=pipeline uv run python -m core.telemetry <subcommand>

Subcommands: `up`, `catch-up`, `status` (see `local_stack.py`).
"""

from __future__ import annotations

import sys

from core.telemetry.local_stack import main

if __name__ == "__main__":
    sys.exit(main())

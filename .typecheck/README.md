# Type Check Baseline

This directory stores the locked `ty` baseline used to ratchet type-checking quality over time.

## Baseline file

- `ty-baseline.txt`: Raw output from `ty` in concise mode.

## Refresh command

Run from repository root:

```bash
UV_CACHE_DIR=/tmp/joseward/uv-cache uv run ty check --output-format concise --no-progress > .typecheck/ty-baseline.txt || true
```

Notes:
- `ty` exits non-zero when diagnostics are present, so `|| true` keeps the refresh command script-friendly.
- Keep `mypy` unchanged during migration; this baseline is for `ty` tracking only.

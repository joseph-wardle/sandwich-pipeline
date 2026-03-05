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

## DCC boundary policy

- Do not maintain local typing shims under `pipeline/typings`.
- Keep DCC boundary handling in `pyproject.toml` via global `tool.ty.analysis` module rules.

Quick verification:

```bash
test -d pipeline/typings && echo "unexpected: pipeline/typings exists" || echo "ok: no local shims"
rg -n "pipeline/typings|mypy_path" pyproject.toml
```

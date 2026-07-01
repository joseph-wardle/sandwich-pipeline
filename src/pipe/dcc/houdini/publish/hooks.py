"""Hook resolution and execution for Houdini component publish.

Design goals:
1. Keep hook behavior decoupled from `dcc.houdini.publish.main`.
2. Support both named hooks and import-path specs.
3. Keep unresolved or failed hooks explicit and machine-readable.

Hook spec formats:
- Named hook: `turnaround`
- Module with default entrypoint: `my.module` (calls `run`)
- Module with explicit function: `my.module:run` or `my.module.run`
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Mapping, NotRequired, TypedDict

DEFAULT_HOOK_FUNCTION = "run"

HOOK_STATUS_SUCCESS = "success"
HOOK_STATUS_SKIPPED = "skipped"
HOOK_STATUS_FAILED = "failed"
_VALID_STATUSES = frozenset(
    {HOOK_STATUS_SUCCESS, HOOK_STATUS_SKIPPED, HOOK_STATUS_FAILED}
)

# Backward-compatible aliases for pre-existing hook specs.
_LEGACY_SPEC_ALIASES = {
    "dcc.houdini.publish.hooks.turnaround:run": "turnaround",
    "dcc.houdini.publish.hooks.turnaround_shotgrid:run": "turnaround_shotgrid",
}


class HookResult(TypedDict):
    status: str
    message: str
    payload: NotRequired[dict[str, str]]


class HookExecution(TypedDict):
    spec: str
    hook: str
    status: str
    message: str
    payload: dict[str, str]


HookCallback = Callable[[dict[str, str]], Any]

_HOOK_REGISTRY: dict[str, HookCallback] = {}
_HOOK_DESCRIPTIONS: dict[str, str] = {}


def register_hook(name: str, callback: HookCallback, *, description: str = "") -> None:
    """Register a named hook callback."""
    token = name.strip()
    if not token:
        raise ValueError("Hook name cannot be empty.")
    if not callable(callback):
        raise TypeError(f"Hook callback is not callable for '{token}'.")
    _HOOK_REGISTRY[token] = callback
    _HOOK_DESCRIPTIONS[token] = description.strip()


def registered_hooks() -> dict[str, str]:
    """Return registered named hooks and descriptions."""
    return dict(_HOOK_DESCRIPTIONS)


def resolve_hook(spec: str) -> tuple[str, HookCallback]:
    """Resolve a hook spec into `(resolved_name, callback)`."""
    raw = spec.strip()
    if not raw:
        raise ValueError("Hook spec cannot be empty.")

    value = _LEGACY_SPEC_ALIASES.get(raw, raw)
    named = _HOOK_REGISTRY.get(value)
    if named is not None:
        return value, named

    module_name, attr_name = _parse_import_spec(value)
    module = importlib.import_module(module_name)
    callback = getattr(module, attr_name)
    if not callable(callback):
        raise TypeError(f"Hook is not callable: {spec}")
    return f"{module_name}:{attr_name}", callback


def execute_hook(spec: str, context: Mapping[str, str]) -> HookExecution:
    """Execute a hook spec and normalize to a structured result."""
    resolved_name = spec.strip()
    try:
        resolved_name, callback = resolve_hook(spec)
    except Exception as exc:
        return {
            "spec": spec,
            "hook": resolved_name or spec,
            "status": HOOK_STATUS_FAILED,
            "message": f"{type(exc).__name__}: {exc}",
            "payload": {},
        }

    try:
        raw = callback(dict(context))
    except Exception as exc:
        return {
            "spec": spec,
            "hook": resolved_name,
            "status": HOOK_STATUS_FAILED,
            "message": f"{type(exc).__name__}: {exc}",
            "payload": {},
        }

    normalized = _normalize_hook_result(raw)
    return {
        "spec": spec,
        "hook": resolved_name,
        "status": normalized["status"],
        "message": normalized["message"],
        "payload": normalized.get("payload", {}),
    }


def _parse_import_spec(spec: str) -> tuple[str, str]:
    if ":" in spec:
        module_name, attr_name = spec.split(":", 1)
        return module_name.strip(), attr_name.strip()

    # Prefer importing as module with default run() function.
    try:
        importlib.import_module(spec)
        return spec, DEFAULT_HOOK_FUNCTION
    except Exception:
        pass

    module_name, sep, attr_name = spec.rpartition(".")
    if not sep:
        available = ", ".join(sorted(_HOOK_REGISTRY.keys()))
        if available:
            raise ValueError(
                f"Invalid hook spec '{spec}'. Use a registered name ({available}) "
                "or import path ('module', 'module:function', 'module.function')."
            )
        raise ValueError(
            f"Invalid hook spec '{spec}'. Use a registered name or import path "
            "('module', 'module:function', 'module.function')."
        )
    return module_name.strip(), attr_name.strip()


def _normalize_hook_result(raw: Any) -> HookResult:
    if raw is None:
        return {"status": HOOK_STATUS_SUCCESS, "message": "ok"}
    if isinstance(raw, str):
        text = raw.strip() or "ok"
        return {"status": HOOK_STATUS_SUCCESS, "message": text}

    if isinstance(raw, Mapping):
        status = str(raw.get("status", HOOK_STATUS_SUCCESS)).strip().lower()
        if status not in _VALID_STATUSES:
            status = HOOK_STATUS_SUCCESS
        message = str(raw.get("message", "")).strip()
        if not message:
            message = {
                HOOK_STATUS_SUCCESS: "ok",
                HOOK_STATUS_SKIPPED: "skipped",
                HOOK_STATUS_FAILED: "failed",
            }[status]

        payload_raw = raw.get("payload", raw.get("data"))
        payload: dict[str, str] = {}
        if isinstance(payload_raw, Mapping):
            payload = {
                str(key): str(value)
                for key, value in payload_raw.items()
                if value is not None
            }

        result: HookResult = {"status": status, "message": message}
        if payload:
            result["payload"] = payload
        return result

    return {"status": HOOK_STATUS_SUCCESS, "message": str(raw)}


def _hook_turnaround_stub(context: dict[str, str]) -> HookResult:
    del context
    return {
        "status": HOOK_STATUS_SKIPPED,
        "message": (
            "Turnaround hook is not implemented yet. Add the Tractor turnaround "
            "job implementation in a future phase."
        ),
    }


def _hook_turnaround_shotgrid_stub(context: dict[str, str]) -> HookResult:
    del context
    return {
        "status": HOOK_STATUS_SKIPPED,
        "message": (
            "ShotGrid upload hook is not implemented yet. Add it after turnaround "
            "render jobs produce publishable media."
        ),
    }


def turnaround(context: dict[str, str]) -> HookResult:
    """Built-in named hook: `turnaround`."""
    return _hook_turnaround_stub(context)


def turnaround_shotgrid(context: dict[str, str]) -> HookResult:
    """Built-in named hook: `turnaround_shotgrid`."""
    return _hook_turnaround_shotgrid_stub(context)


register_hook(
    "turnaround",
    turnaround,
    description=(
        "Generate and submit a turnaround render for the published asset "
        "(stub in Phase 7)."
    ),
)
register_hook(
    "turnaround_shotgrid",
    turnaround_shotgrid,
    description=("Upload turnaround media and metadata to ShotGrid (stub in Phase 7)."),
)


__all__ = [
    "HookExecution",
    "HookResult",
    "execute_hook",
    "register_hook",
    "registered_hooks",
    "resolve_hook",
    "turnaround",
    "turnaround_shotgrid",
]

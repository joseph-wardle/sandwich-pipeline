"""Per-instance TTL cache for ShotGrid `find_*` list queries.

The ShotGrid client wraps every list query with `@ttl_cache(seconds=60)` so
that hot UI paths (e.g. filling an asset dropdown) do not hit the live API on
every keystroke. Write verbs call `invalidate` to drop the cache so a
read after a write always sees fresh data.

"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

_CACHE_ATTR = "_ttl_cache_state"

T = TypeVar("T")


def ttl_cache(seconds: float) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Return a decorator that caches keyword-only method results for `seconds`.

    Usage:

        class ShotGrid:
            @ttl_cache(seconds=60)
            def find_assets(self, *, type: str | None = None) -> list[Asset]:
                ...

    Calling the method with positional arguments raises `TypeError` — all
    cached methods must be invoked with keywords so the cache key is stable.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        fn_name = getattr(fn, "__name__", "<ttl_cached>")
        fn_qualname = getattr(fn, "__qualname__", fn_name)

        @wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> T:
            if args:
                raise TypeError(
                    f"{fn_qualname} is cached and must be called with keyword "
                    f"arguments only; got positional {args!r}."
                )
            state = _get_state(self)
            key = (fn_name, frozenset((k, _freeze(v)) for k, v in kwargs.items()))
            now = time.monotonic()
            with state.lock:
                hit = state.entries.get(key)
                if hit is not None and hit[0] > now:
                    return hit[1]
            result = fn(self, **kwargs)
            with state.lock:
                state.entries[key] = (now + seconds, result)
            return result

        return wrapper

    return decorator


def invalidate(instance: object) -> None:
    """Drop every cached result on `instance`. Called by write verbs."""
    state = getattr(instance, _CACHE_ATTR, None)
    if state is None:
        return
    with state.lock:
        state.entries.clear()


class _CacheState:
    __slots__ = ("lock", "entries")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entries: dict[
            tuple[str, frozenset[tuple[str, Any]]], tuple[float, Any]
        ] = {}


def _get_state(instance: object) -> _CacheState:
    state = getattr(instance, _CACHE_ATTR, None)
    if state is None:
        state = _CacheState()
        object.__setattr__(instance, _CACHE_ATTR, state)
    return state


def _freeze(value: Any) -> Any:
    """Convert a kwarg value into a hashable cache-key component.

    `find_*` methods legitimately take ``set[str]`` / ``list[str]`` / ``dict``
    filter arguments; those are unhashable by default.  This freezes them
    into hashable equivalents so the cache key can still be constructed.
    """
    if isinstance(value, (set, frozenset)):
        return frozenset(value)
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, dict):
        return frozenset((k, _freeze(v)) for k, v in value.items())
    return value

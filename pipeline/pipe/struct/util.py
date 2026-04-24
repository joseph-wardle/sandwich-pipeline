from __future__ import annotations

import json

import attrs
import cattrs

from attr._make import _frozen_setattrs
from copy import deepcopy
from typing import Any, Type, TypeVar, Union

_S = TypeVar("_S")


@attrs.define
class JsonSerializable:
    """Dataclass with methods to (de)serialize JSON"""

    @classmethod
    def from_json(cls: Type[_S], json_data: Union[str, bytes, bytearray]) -> _S:
        return cattrs.structure(json.loads(json_data), cls)

    def to_json(self) -> str:
        c = cattrs.Converter(unstruct_collection_overrides={set: list})
        return json.dumps(c.unstructure(self))


@attrs.define
class Diffable(JsonSerializable):
    """JsonSerializable dataclass that tracks changes to it since initialization"""

    _initial_state: dict[str, Any] = attrs.field(
        alias="_initial_state",
        eq=False,
        init=False,
        order=False,
        repr=False,
    )

    def __attrs_post_init__(self) -> None:
        # don't store initial state if frozen
        if type(self.__class__.__setattr__) is _frozen_setattrs:
            object.__setattr__(self, "_initial_state", {})

        # get a deepcopy of each non-private slot.  Private fields (those
        # prefixed with ``_``) hold internal bookkeeping — diff state itself,
        # ShotGrid back-references — and must never appear in diffs.
        name: str
        state: dict[str, Any] = {}
        for name in (f.name for f in attrs.fields(self.__class__)):
            if name.startswith("_"):
                continue
            state[name] = deepcopy(getattr(self, name))

        object.__setattr__(self, "_initial_state", state)

    def diff(self) -> dict[str, Any]:
        if self._initial_state == {}:
            return {}

        diff: dict[str, Any] = {}
        name: str
        for name in (f.name for f in attrs.fields(self.__class__)):
            if name.startswith("_"):
                continue
            if (val := getattr(self, name)) != self._initial_state[name]:
                diff[name] = val
        return diff

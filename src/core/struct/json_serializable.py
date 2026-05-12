from __future__ import annotations

import json
from typing import TypeVar

import attrs
import cattrs

_S = TypeVar("_S")


@attrs.define
class JsonSerializable:
    """Dataclass with methods to (de)serialize to JSON."""

    @classmethod
    def from_json(cls: type[_S], json_data: str | bytes | bytearray) -> _S:
        return cattrs.structure(json.loads(json_data), cls)

    def to_json(self) -> str:
        c = cattrs.Converter(unstruct_collection_overrides={set: list})
        return json.dumps(c.unstructure(self))

import json
from abc import abstractmethod
from typing import Generic, TypeVar

from maya import cmds as cmds

T = TypeVar("T")

"""
Example Usage:
    >>> # Define your settings
    >>> class MySettings:
    >>>     SHOW_UI = BoolOptionVar("myTool.showUI", True)
    >>>
    >>> # Usage in code
    >>> settings = MySettings()
    >>> print(settings.SHOW_UI.value)  # Gets value
    >>> settings.SHOW_UI.value = False # Sets value in Maya
"""


class OptionVar(Generic[T]):
    def __init__(self, key: str, default_value: T):
        """
        The key value NEEDS to be unique or you will accidentally overwrite or error when reading optionVars.
        You can bind multiple OptionVar objects to the same underlying optionVar by using the same key.
        Something like: rigBuilder.lastVariant
        """
        self.name = key
        self.default_value = default_value

    def exists(self) -> bool:
        return cmds.optionVar(exists=self.name)  # type: ignore

    @property
    def value(self) -> T:
        """
        The current value of the optionVar.

        Returns:
            The value stored in Maya, or `default_value` if it doesn't exist.
        """
        if not cmds.optionVar(exists=self.name):
            return self.default_value
        return self._get()

    @value.setter
    def value(self, val: T) -> None:
        """
        Updates the optionVar in the Maya session.

        Args:
            val: The new value to store. Must match type T.
        """
        self._set(val)

    def reset(self) -> None:
        if self.exists():
            cmds.optionVar(remove=self.name)

    @abstractmethod
    def _get(self) -> T:
        raise NotImplementedError

    @abstractmethod
    def _set(self, value: T) -> None:
        raise NotImplementedError


class BoolOptionVar(OptionVar[bool]):
    def _get(self) -> bool:
        return bool(cmds.optionVar(query=self.name))

    def _set(self, value: bool) -> None:
        cmds.optionVar(intValue=(self.name, int(value)))


class IntOptionVar(OptionVar[int]):
    def _get(self) -> int:
        return int(cmds.optionVar(query=self.name))  # type: ignore

    def _set(self, value: int) -> None:
        cmds.optionVar(intValue=(self.name, int(value)))


class FloatOptionVar(OptionVar[float]):
    def _get(self) -> float:
        return float(cmds.optionVar(query=self.name))  # type: ignore

    def _set(self, value: float) -> None:
        cmds.optionVar(floatValue=(self.name, value))


class StringOptionVar(OptionVar[str]):
    def _get(self) -> str:
        return str(cmds.optionVar(query=self.name))

    def _set(self, value: str) -> None:
        cmds.optionVar(stringValue=(self.name, value))


class JSONOptionVar(OptionVar[dict]):
    def _get(self) -> dict:
        raw: str = cmds.optionVar(query=self.name)  # type: ignore
        if not raw:
            return self.default_value
        return json.loads(raw)

    def _set(self, value: dict) -> None:
        cmds.optionVar(stringValue=(self.name, json.dumps(value)))

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class PublishStage(Enum):
    SAVING_PROJECT = "Saving project"
    PREPARING_PUBLISH = "Preparing publish"
    PLANNING_EXPORT = "Planning texture export"
    EXPORTING_SOURCE = "Exporting source textures"
    CONVERTING_TEX = "Converting TEX textures"
    WRITING_METADATA = "Writing material metadata"
    BACKING_UP_PROJECT = "Backing up project"
    RUNNING_HOUDINI = "Running Houdini publish"

    @property
    def label(self) -> str:
        return self.value


@dataclass(frozen=True)
class PublishProgressUpdate:
    stage: PublishStage
    message: str
    current: int | None = None
    total: int | None = None


PublishProgressCallback = Callable[[PublishProgressUpdate], None]


DEFAULT_PUBLISH_STAGE_SEQUENCE: tuple[PublishStage, ...] = (
    PublishStage.SAVING_PROJECT,
    PublishStage.PREPARING_PUBLISH,
    PublishStage.PLANNING_EXPORT,
    PublishStage.EXPORTING_SOURCE,
    PublishStage.CONVERTING_TEX,
    PublishStage.WRITING_METADATA,
    PublishStage.BACKING_UP_PROJECT,
    PublishStage.RUNNING_HOUDINI,
)

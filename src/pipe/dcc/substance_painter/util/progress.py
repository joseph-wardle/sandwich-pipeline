from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class PublishStage(Enum):
    SAVING_PROJECT = "Saving project"
    PREPARING_PUBLISH = "Preparing publish"
    PLANNING_EXPORT = "Planning texture export"
    EXPORTING_SOURCE = "Exporting source textures"
    WRITING_METADATA = "Writing material metadata"
    CONVERTING_TEX = "Converting TEX textures"
    CONVERTING_PREVIEW = "Building preview textures"
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

    @property
    def is_determinate(self) -> bool:
        return self.current is not None and self.total is not None and self.total > 0


PublishProgressCallback = Callable[[PublishProgressUpdate], None]


DEFAULT_PUBLISH_STAGE_SEQUENCE: tuple[PublishStage, ...] = (
    PublishStage.SAVING_PROJECT,
    PublishStage.PREPARING_PUBLISH,
    PublishStage.PLANNING_EXPORT,
    PublishStage.EXPORTING_SOURCE,
    PublishStage.WRITING_METADATA,
    PublishStage.CONVERTING_TEX,
    PublishStage.CONVERTING_PREVIEW,
    PublishStage.BACKING_UP_PROJECT,
    PublishStage.RUNNING_HOUDINI,
)

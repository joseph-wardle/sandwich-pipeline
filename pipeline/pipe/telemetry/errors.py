"""Pipeline error types that carry an `error_code` for telemetry classification.

Every concrete error class declares its `error_code` as a class attribute. The
telemetry `action` context manager reads this attribute from any exception that
escapes a wrapped block and uses it as the `error_code` field of the emitted
error event.

Stdlib exceptions and other exceptions that don't carry the attribute fall
through to `error_code = "UNKNOWN"`. Call sites can override on a case-by-case
basis with `t.fail(code, message)` inside an except block.

The class IS the registry. There is no separate exc_type → code mapping table.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for pipeline-level failures that should surface to the user.

    Subclasses set `error_code` to a stable string token used by the telemetry
    pipeline and by Grafana dashboards to group failures. Catchers can rely on
    `error_code` always being present on subclasses.
    """

    error_code: str = "UNKNOWN"


class PublishPrecheckError(PipelineError):
    """A publish precheck (saved scene, valid selection, etc.) failed."""

    error_code = "PUBLISH_PRECHECK_FAILED"


class USDExportError(PipelineError):
    """A USD export step (mayaUSDExport, husk, etc.) failed."""

    error_code = "USD_EXPORT_FAILED"


class PublishCopyError(PipelineError):
    """Copying a published file into its final publish location failed."""

    error_code = "PUBLISH_COPY_FAILED"


class HoudiniBuildError(PipelineError):
    """A Houdini headless component build failed."""

    error_code = "HOUDINI_BUILD_FAILED"


class TextureExportError(PipelineError):
    """Substance Painter texture export failed."""

    error_code = "TEXTURE_EXPORT_FAILED"


class TextureConversionError(PipelineError):
    """A texture conversion step (e.g. tex / txmake) failed."""

    error_code = "TEXTURE_CONVERSION_FAILED"


class PlayblastError(PipelineError):
    """Playblast creation or post-processing failed."""

    error_code = "PLAYBLAST_FAILED"


class DCCLaunchError(PipelineError):
    """Launching a DCC (Maya / Houdini / Nuke / Substance Painter) failed."""

    error_code = "DCC_LAUNCH_FAILED"


class TractorSnapshotError(PipelineError):
    """Polling Tractor for a farm-pressure snapshot failed."""

    error_code = "TRACTOR_SNAPSHOT_FAILED"


class RenderStatsHarvestError(PipelineError):
    """Harvesting render statistics from job artifacts failed."""

    error_code = "RENDER_STATS_HARVEST_FAILED"


class StorageScanError(PipelineError):
    """A storage scan run failed."""

    error_code = "STORAGE_SCAN_FAILED"


__all__ = [
    "PipelineError",
    "PublishPrecheckError",
    "USDExportError",
    "PublishCopyError",
    "HoudiniBuildError",
    "TextureExportError",
    "TextureConversionError",
    "PlayblastError",
    "DCCLaunchError",
    "TractorSnapshotError",
    "RenderStatsHarvestError",
    "StorageScanError",
]

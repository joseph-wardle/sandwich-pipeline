"""Headless helpers for the Maya shot playblast flow. The dialog
(`pipe/maya/playblast/shot/dialog.py`) collects user input, builds an
`MPlayblastConfig`, then calls into these functions to:

- compute final movie file paths,
- collect the output path list shown to the artist,
- format the success message printed in the post-export dialog.

Anything in this module is pure (no Maya / Qt access) so it can be tested
or driven from a non-UI context if a future tool needs to reuse the same
playblast plumbing."""

from __future__ import annotations

from pathlib import Path

from dcc.maya.playblast.shot.config import MPlayblastConfig, MShotPlayblastConfig
from core.playblast import FFmpegPreset


def final_movie_path_for_base(
    output_base: str | Path,
    preset: FFmpegPreset,
) -> Path:
    """Return the final encoded movie path for an output base + preset.
    `output_base` is the per-shot/per-destination base path written to the
    `MShotPlayblastConfig.paths` dict; the preset's `ext` is appended."""
    return Path(str(output_base) + f".{preset.ext}")


def final_movie_paths_for_destination(
    shot_config: MShotPlayblastConfig,
    *,
    preset: FFmpegPreset,
    destination_dir: Path,
) -> list[Path]:
    """Return the final movie paths for `shot_config` whose parent matches
    `destination_dir`. Used to map a save destination back to the movies it
    will produce."""
    resolved_destination_dir = destination_dir.expanduser().resolve()

    matching_paths: list[Path] = []
    for output_base in shot_config.paths.get(preset, []):
        resolved_output_base = Path(str(output_base)).expanduser().resolve()
        if resolved_output_base.parent != resolved_destination_dir:
            continue
        matching_paths.append(final_movie_path_for_base(resolved_output_base, preset))
    return matching_paths


def ordered_final_movie_paths_for_upload(
    shot_config: MShotPlayblastConfig,
    destinations: list[tuple[FFmpegPreset, Path]],
) -> list[Path]:
    """Deterministic list of final movie paths for upload-path resolution.

    `destinations` is `[(preset, destination_dir), ...]` in the order the
    dialog wants tried. Falls back to every path in `shot_config.paths` if
    a destination match misses.
    """
    ordered_paths: list[Path] = []
    seen_paths: set[Path] = set()

    for preset, destination_dir in destinations:
        for output_path in final_movie_paths_for_destination(
            shot_config, preset=preset, destination_dir=destination_dir
        ):
            if output_path in seen_paths:
                continue
            seen_paths.add(output_path)
            ordered_paths.append(output_path)

    for preset, output_bases in shot_config.paths.items():
        for output_base in output_bases:
            output_path = final_movie_path_for_base(output_base, preset)
            resolved_output_path = output_path.expanduser().resolve()
            if resolved_output_path in seen_paths:
                continue
            seen_paths.add(resolved_output_path)
            ordered_paths.append(resolved_output_path)

    return ordered_paths


def collect_output_paths(config: MPlayblastConfig) -> list[str]:
    """Collect every final movie path across all shots in `config`. Used to
    show the artist what files were written."""
    output_paths: list[str] = []
    for shot_cfg in config.shots:
        for preset, bases in shot_cfg.paths.items():
            for base in bases:
                output_paths.append(str(final_movie_path_for_base(base, preset)))
    return output_paths


def build_success_message(
    output_paths: list[str],
    post_playblast_messages: list[str],
) -> str:
    """Format the success-dialog message body. Mirrors the Houdini
    launcher's `_build_success_message` shape so the two dialogs feel the
    same to artists."""
    message_lines = ["Local playblast export successful."]
    if output_paths:
        message_lines.append("")
        message_lines.append("Outputs:")
        message_lines.extend(output_paths)
    if post_playblast_messages:
        message_lines.append("")
        message_lines.append("Post-export:")
        message_lines.extend(post_playblast_messages)
    return "\n".join(message_lines)


__all__ = [
    "build_success_message",
    "collect_output_paths",
    "final_movie_path_for_base",
    "final_movie_paths_for_destination",
    "ordered_final_movie_paths_for_upload",
]

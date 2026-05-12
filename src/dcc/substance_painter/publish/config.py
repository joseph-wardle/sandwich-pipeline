"""Export target resolution and Substance Painter preset generation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

import substance_painter as sp
from substance_painter.exception import ProjectError, ServiceNotFoundError

from dcc.substance_painter.publish.types import (
    ResolvedExportTarget,
    TexSetExportSettings,
)
from dcc.substance_painter.util.texture_set import texture_set_name
from core.struct.material import DisplacementSource, NormalSource, NormalType


def channel_export_name(channel: sp.textureset.Channel) -> str:
    label_attr = getattr(channel, "label", None)
    label = label_attr() if callable(label_attr) else label_attr
    if isinstance(label, str) and label:
        return label.replace(" ", "")
    return channel.type().name


def stack_root_path(stack: sp.textureset.Stack) -> str:
    ts_name = texture_set_name(stack.material())
    stack_name = stack.name()
    return f"{ts_name}/{stack_name}" if stack_name else ts_name


def resolve_export_targets(
    export_settings_arr: Sequence[TexSetExportSettings],
) -> list[ResolvedExportTarget]:
    targets: list[ResolvedExportTarget] = []
    for export_settings in export_settings_arr:
        ts_name = texture_set_name(export_settings.tex_set)
        try:
            stack = export_settings.tex_set.get_stack()
        except ValueError as exc:
            raise ValueError(
                (
                    f'Texture Set "{ts_name}" uses material layering.\n'
                    "This publish tool currently supports non-layered texture "
                    "sets only."
                )
            ) from exc

        targets.append(
            ResolvedExportTarget(
                settings=export_settings,
                stack=stack,
                texture_set_name=ts_name,
            )
        )
    return targets


def count_udim_sets(export_settings_arr: Iterable[TexSetExportSettings]) -> int:
    count = 0
    for export_settings in export_settings_arr:
        try:
            if export_settings.tex_set.has_uv_tiles():
                count += 1
        except (ProjectError, ServiceNotFoundError):
            continue
    return count


def generate_export_config(
    src_path: Path,
    export_targets: Iterable[ResolvedExportTarget],
) -> dict[str, object]:
    targets = list(export_targets)
    return {
        "exportPath": str(src_path),
        "exportShaderParams": True,
        "exportPresets": [
            {
                "name": target.texture_set_name,
                "maps": [
                    *_shader_maps(target.settings),
                    *[
                        {
                            "fileName": (
                                f"$textureSet_{channel_export_name(ch)}"
                                "(_$colorSpace)(.$udim)"
                            ),
                            "channels": [
                                {
                                    "destChannel": color,
                                    "srcChannel": color,
                                    "srcMapType": "documentMap",
                                    "srcMapName": ch.type().name.lower(),
                                }
                                for color in colors
                            ],
                            "parameters": {
                                "bitDepth": bit_depth.lower(),
                                "fileFormat": "png",
                                "sizeLog2": target.settings.resolution,
                            },
                        }
                        for ch in target.settings.extra_channels
                        for colors, bit_depth in re.findall(
                            r"^s?(L|RGB)(\d{1,2}F?)$",
                            target.stack.get_channel(ch.type()).format().name,
                        )
                    ],
                    *_preview_surface_maps(),
                ],
            }
            for target in targets
        ],
        "exportList": [
            {
                "rootPath": stack_root_path(target.stack),
                "exportPreset": target.texture_set_name,
            }
            for target in targets
        ],
        "exportParameters": [
            {
                "parameters": {
                    "dithering": False,
                    "paddingAlgorithm": "color",
                    "dilationDistance": 24,
                }
            }
        ],
    }


def _shader_maps(export_settings: TexSetExportSettings) -> list[dict[str, object]]:
    maps: list[dict[str, object]] = [
        {
            "fileName": "$textureSet_BaseColor(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": ch,
                    "srcChannel": ch,
                    "srcMapType": "documentMap",
                    "srcMapName": "baseColor",
                }
                for ch in "RGB"
            ],
            "parameters": {
                "bitDepth": "16",
                "fileFormat": "png",
                "sizeLog2": export_settings.resolution,
            },
        },
        {
            "fileName": "$textureSet_Metallic(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": "L",
                    "srcChannel": "L",
                    "srcMapType": "documentMap",
                    "srcMapName": "metallic",
                },
            ],
            "parameters": {
                "bitDepth": "8",
                "fileFormat": "png",
                "sizeLog2": export_settings.resolution,
            },
        },
        {
            "fileName": "$textureSet_IOR(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": "L",
                    "srcChannel": "L",
                    "srcMapType": "documentMap",
                    "srcMapName": "specular",
                },
            ],
            "parameters": {
                "bitDepth": "8",
                "fileFormat": "png",
                "sizeLog2": export_settings.resolution,
            },
        },
        {
            "fileName": "$textureSet_SpecularRoughness(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": "L",
                    "srcChannel": "L",
                    "srcMapType": "documentMap",
                    "srcMapName": "roughness",
                },
            ],
            "parameters": {
                "bitDepth": "8",
                "fileFormat": "png",
                "sizeLog2": export_settings.resolution,
            },
        },
        {
            "fileName": "$textureSet_Emissive(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": ch,
                    "srcChannel": ch,
                    "srcMapType": "documentMap",
                    "srcMapName": "emissive",
                }
                for ch in "RGB"
            ],
            "parameters": {
                "bitDepth": "16",
                "fileFormat": "png",
                "sizeLog2": export_settings.resolution,
            },
        },
        {
            "fileName": "$textureSet_Presence(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": "L",
                    "srcChannel": "L",
                    "srcMapType": "documentMap",
                    "srcMapName": "opacity",
                },
            ],
            "parameters": {
                "bitDepth": "8",
                "fileFormat": "png",
                "sizeLog2": export_settings.resolution,
            },
        },
        {
            "fileName": (
                "$textureSet_Normal(_$colorSpace)(.$udim)"
                f"{'.pre-b2r' if export_settings.normal_type == NormalType.BUMP_ROUGHNESS else ''}"
            ),
            "channels": [
                {
                    "destChannel": ch,
                    "srcChannel": ch,
                    **(
                        {
                            "srcMapType": "virtualMap",
                            "srcMapName": "Normal_OpenGL",
                        }
                        if export_settings.normal_source is NormalSource.NORMAL_HEIGHT
                        else {
                            "srcMapType": "documentMap",
                            "srcMapName": "normal",
                        }
                    ),
                }
                for ch in "RGB"
            ],
            "parameters": {
                **(
                    {
                        "bitDepth": "16f",
                        "fileFormat": "exr",
                    }
                    if export_settings.normal_type is NormalType.BUMP_ROUGHNESS
                    else {
                        "bitDepth": "16",
                        "fileFormat": "png",
                    }
                ),
                "sizeLog2": export_settings.resolution,
            },
        },
    ]

    if export_settings.displacement_source is not DisplacementSource.NONE:
        maps += [
            {
                "fileName": "$textureSet_Displacement(_$colorSpace)(.$udim)",
                "channels": [
                    {
                        "destChannel": "L",
                        "srcChannel": "L",
                        "srcMapType": "documentMap",
                        "srcMapName": (
                            "height"
                            if export_settings.displacement_source
                            == DisplacementSource.HEIGHT
                            else "displacement"
                        ),
                    },
                ],
                "parameters": {
                    "bitDepth": "16",
                    "fileFormat": "png",
                    "sizeLog2": export_settings.resolution,
                },
            }
        ]

    return maps


def _preview_surface_maps() -> list[dict[str, object]]:
    return [
        {
            "fileName": "$textureSet_DiffuseColor(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": ch,
                    "srcChannel": ch,
                    "srcMapType": "documentMap",
                    "srcMapName": "baseColor",
                }
                for ch in "RGB"
            ],
            "parameters": {
                "bitDepth": "8",
                "dithering": True,
                "fileFormat": "jpeg",
            },
        },
        {
            "fileName": "$textureSet_ORM(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": "R",
                    "srcChannel": "R",
                    "srcMapType": "documentMap",
                    "srcMapName": "ambientOcclusion",
                },
                {
                    "destChannel": "G",
                    "srcChannel": "G",
                    "srcMapType": "documentMap",
                    "srcMapName": "roughness",
                },
                {
                    "destChannel": "B",
                    "srcChannel": "B",
                    "srcMapType": "documentMap",
                    "srcMapName": "metallic",
                },
            ],
            "parameters": {
                "bitDepth": "8",
                "fileFormat": "jpeg",
            },
        },
        {
            "fileName": "$textureSet_Emissive(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": ch,
                    "srcChannel": ch,
                    "srcMapType": "documentMap",
                    "srcMapName": "emissive",
                }
                for ch in "RGB"
            ],
            "parameters": {
                "bitDepth": "8",
                "dithering": True,
                "fileFormat": "jpeg",
            },
        },
        {
            "fileName": "$textureSet_NormalDX(_$colorSpace)(.$udim)",
            "channels": [
                {
                    "destChannel": ch,
                    "srcChannel": ch,
                    "srcMapType": "virtualMap",
                    "srcMapName": "Normal_DirectX",
                }
                for ch in "RGB"
            ],
            "parameters": {
                "bitDepth": "8",
                "fileFormat": "jpeg",
            },
        },
    ]

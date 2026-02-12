from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import substance_painter as sp

if TYPE_CHECKING:
    import typing

    RT = typing.TypeVar("RT")  # return type

from env_sg import DB_Config
from shared.util import resolve_mapped_path

from pipe.asset.paths import paths_for_asset
from pipe.db import DB
from pipe.glui.dialogs import MessageDialog
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset
from pipe.struct.material import (
    DisplacementSource,
    MaterialInfo,
    NormalSource,
    NormalType,
    TexSetInfo,
)
from pipe.texconverter import TexConversionError, TexConverter

lib_path = resolve_mapped_path(Path(__file__).parents[1] / "lib")
log = logging.getLogger(__name__)


@dataclass
class TexSetExportSettings:
    tex_set: sp.textureset.TextureSet
    extra_channels: set[sp.textureset.Channel]
    resolution: int
    displacement_source: DisplacementSource
    normal_type: NormalType
    normal_source: NormalSource


@dataclass(frozen=True)
class _ResolvedExportTarget:
    settings: TexSetExportSettings
    stack: sp.textureset.Stack
    texture_set_name: str


def _texture_set_name(tex_set: sp.textureset.TextureSet) -> str:
    """Return texture set name across API versions."""
    name_attr = getattr(tex_set, "name", None)
    if callable(name_attr):
        return name_attr()
    if isinstance(name_attr, str):
        return name_attr
    return str(tex_set)


def _channel_export_name(channel: sp.textureset.Channel) -> str:
    label_attr = getattr(channel, "label", None)
    label = label_attr() if callable(label_attr) else label_attr
    if isinstance(label, str) and label:
        return label.replace(" ", "")
    return channel.type().name


def _stack_root_path(stack: sp.textureset.Stack) -> str:
    texture_set_name = _texture_set_name(stack.material())
    stack_name = stack.name()
    return f"{texture_set_name}/{stack_name}" if stack_name else texture_set_name


class Exporter:
    """Class to manage exporting and converting textures"""

    _asset: Asset
    _conn: DB
    _out_path: Path
    _preview_path: Path
    _src_path: Path
    _tex_path: Path

    def __init__(self, asset: Asset) -> None:
        self._asset = asset
        self._conn = DB.Get(DB_Config)

    def _init_paths(self, mat_var: str, geo_var: str, material_layer: str) -> None:
        paths = paths_for_asset(self._asset)
        material_layer_dir = paths.publish_textures_layer_dir(
            geo_var, mat_var, material_layer
        )

        self._out_path = resolve_mapped_path(material_layer_dir)
        self._src_path = resolve_mapped_path(
            paths.publish_textures_src_dir(geo_var, mat_var, material_layer)
        )
        self._preview_path = resolve_mapped_path(
            paths.publish_textures_preview_dir(geo_var, mat_var, material_layer)
        )
        self._tex_path = self._out_path

        self._out_path.mkdir(parents=True, exist_ok=True)
        self._src_path.mkdir(parents=True, exist_ok=True)
        self._preview_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_export_targets(
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
    ) -> list[_ResolvedExportTarget]:
        targets: list[_ResolvedExportTarget] = []
        for export_settings in exp_setting_arr:
            texture_set_name = _texture_set_name(export_settings.tex_set)
            try:
                stack = export_settings.tex_set.get_stack()
            except ValueError:
                MessageDialog(
                    get_main_qt_window(),
                    (
                        f'Texture Set "{texture_set_name}" uses material layering.\n'
                        "This exporter currently supports non-layered texture sets only."
                    ),
                    "Unsupported Texture Set",
                ).exec_()
                return []

            targets.append(
                _ResolvedExportTarget(
                    settings=export_settings,
                    stack=stack,
                    texture_set_name=texture_set_name,
                )
            )
        return targets

    def export(
        self,
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
        mat_var: str,
        geo_var: str,
        material_layer: str,
    ) -> bool:
        """Export all the textures of the given Texture Sets"""
        self._init_paths(mat_var, geo_var, material_layer)
        log.info("Exporting textures to %s", self._out_path)

        resolved_targets = self._resolve_export_targets(exp_setting_arr)
        if not resolved_targets:
            return False

        config = Exporter._generate_config(self._src_path, resolved_targets)
        log.debug(config)

        try:
            planned_exports = sp.export.list_project_textures(config)
        except Exception:
            log.exception("Export configuration is invalid for this project.")
            MessageDialog(
                get_main_qt_window(),
                "Export configuration is invalid for the current project. "
                "Check enabled texture sets and channel settings, then try again.",
                "Invalid Export Configuration",
            ).exec_()
            return False

        if not any(planned_exports.values()):
            MessageDialog(
                get_main_qt_window(),
                "No textures match the current export configuration.",
                "Nothing To Export",
            ).exec_()
            log.warning("Export aborted: no matching textures in export configuration.")
            return False

        export_result: sp.export.TextureExportResult
        try:
            export_result = sp.export.export_project_textures(config)
        except Exception:
            log.exception("Texture export failed in Substance Painter.")
            return False

        if export_result.status == sp.export.ExportStatus.Cancelled:
            log.warning("Texture export was cancelled: %s", export_result.message)
            MessageDialog(
                get_main_qt_window(),
                "Texture export was cancelled.",
                "Export Cancelled",
            ).exec_()
            return False

        if export_result.status == sp.export.ExportStatus.Warning:
            log.warning(
                "Texture export completed with warnings: %s", export_result.message
            )
        elif export_result.status != sp.export.ExportStatus.Success:
            log.error("Texture export failed with status %s", export_result.status)
            return False

        if not export_result.textures:
            log.error("Texture export produced no files.")
            return False

        self.write_mat_info([target.settings for target in resolved_targets])

        tex_converter = TexConverter(
            self._tex_path,
            self._preview_path,
            list(export_result.textures.values()),
        )

        try:
            tex_converter.convert_tex()
            tex_converter.convert_previewsurface()
        except TexConversionError:
            log.exception("Texture conversion failed.")
            MessageDialog(
                get_main_qt_window(),
                (
                    "Warning! Not all textures were converted! Make sure to "
                    'stop rendering this asset in Houdini and press "Reset '
                    'RenderMan RIS/XPU".'
                ),
            ).exec_()
            return False

        return True

    def write_mat_info(
        self, export_settings_arr: typing.Iterable[TexSetExportSettings]
    ) -> bool:
        """Write out JSON file with information about the texturesets"""
        mat_info_path = self._out_path / "mat.json"
        old_mat_info: MaterialInfo
        if mat_info_path.exists():
            with open(mat_info_path, "r") as f:
                old_mat_info = MaterialInfo.from_json(f.read())
        else:
            old_mat_info = MaterialInfo()

        all_tex_sets = [
            _texture_set_name(ts) for ts in sp.textureset.all_texture_sets()
        ]
        for tex_set in list(old_mat_info.tex_sets.keys()):
            if tex_set not in all_tex_sets:
                del old_mat_info.tex_sets[tex_set]

        new_mat_info = MaterialInfo(
            {
                **old_mat_info.tex_sets,
                **{
                    _texture_set_name(export_settings.tex_set): TexSetInfo(
                        displacement_source=export_settings.displacement_source,
                        has_udims=export_settings.tex_set.has_uv_tiles(),
                        normal_source=export_settings.normal_source,
                        normal_type=export_settings.normal_type,
                    )
                    for export_settings in export_settings_arr
                },
            }
        )
        with open(str(self._out_path / "mat.json"), "w", encoding="utf-8") as f:
            f.write(new_mat_info.to_json())
        return True

    @staticmethod
    def _generate_config(
        src_path: Path, export_targets: typing.Iterable[_ResolvedExportTarget]
    ) -> dict:
        targets = list(export_targets)
        return {
            "exportPath": str(src_path),
            "exportShaderParams": True,
            "exportPresets": [
                {
                    "name": target.texture_set_name,
                    "maps": [
                        # Default RenderMan maps
                        *Exporter._shader_maps(target.settings),
                        # Extra AOVs
                        *[
                            {
                                "fileName": f"$textureSet_{_channel_export_name(ch)}(_$colorSpace)(.$udim)",
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
                        # Preview Surface
                        *Exporter._preview_surface_maps(),
                    ],
                }
                for target in targets
            ],
            "exportList": [
                {
                    "rootPath": _stack_root_path(target.stack),
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

    @staticmethod
    def _shader_maps(export_settings: TexSetExportSettings) -> list:
        maps = [
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
                "fileName": f"$textureSet_Normal(_$colorSpace)(.$udim){'.pre-b2r' if export_settings.normal_type == NormalType.BUMP_ROUGHNESS else ''}",
                "channels": [
                    {
                        "destChannel": ch,
                        "srcChannel": ch,
                        **(
                            {
                                "srcMapType": "virtualMap",
                                "srcMapName": "Normal_OpenGL",
                            }
                            if export_settings.normal_source
                            is NormalSource.NORMAL_HEIGHT
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

    @staticmethod
    def _preview_surface_maps() -> list:
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
                        "srcMapName": "opacity",
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

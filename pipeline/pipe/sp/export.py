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

    def _init_paths(self, mat_var: str, geo_var: str, shader_layer: str) -> None:
        paths = paths_for_asset(self._asset)
        layer_dir = paths.publish_textures_layer_dir(geo_var, mat_var, shader_layer)

        self._out_path = resolve_mapped_path(layer_dir)
        self._src_path = resolve_mapped_path(
            paths.publish_textures_src_dir(geo_var, mat_var, shader_layer)
        )
        self._preview_path = resolve_mapped_path(
            paths.publish_textures_preview_dir(geo_var, mat_var, shader_layer)
        )
        self._tex_path = self._out_path

        self._out_path.mkdir(parents=True, exist_ok=True)
        self._src_path.mkdir(parents=True, exist_ok=True)
        self._preview_path.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        exp_setting_arr: typing.Sequence[TexSetExportSettings],
        mat_var: str,
        geo_var: str,
        shader_layer: str,
    ) -> bool:
        """Export all the textures of the given Texture Sets"""
        self._init_paths(mat_var, geo_var, shader_layer)

        try:
            [tss.tex_set.get_stack() for tss in exp_setting_arr]
        except ValueError:
            MessageDialog(
                get_main_qt_window(),
                "Warning! Exporter could not get stack! You are doing something cool with material layering. Please show this to Dallin so he can fix it.",
            ).exec_()
            return False

        config = Exporter._generate_config(self._src_path, exp_setting_arr)
        log.debug(config)

        export_result: sp.export.TextureExportResult
        try:
            export_result = sp.export.export_project_textures(config)
        except Exception as e:
            print(e)
            return False

        self.write_mat_info(exp_setting_arr)

        tex_converter = TexConverter(
            self._tex_path, self._preview_path, export_result.textures.values()
        )

        try:
            tex_converter.convert_tex()
            tex_converter.convert_previewsurface()
        except TexConversionError:
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

        all_tex_sets = [ts.name() for ts in sp.textureset.all_texture_sets()]
        for tex_set in list(old_mat_info.tex_sets.keys()):
            if tex_set not in all_tex_sets:
                del old_mat_info.tex_sets[tex_set]

        new_mat_info = MaterialInfo(
            {
                **old_mat_info.tex_sets,
                **{
                    export_settings.tex_set.name(): TexSetInfo(
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
        src_path: Path, export_settings_arr: typing.Iterable[TexSetExportSettings]
    ) -> dict:
        return {
            "exportPath": str(src_path),
            "exportShaderParams": True,
            "exportPresets": [
                {
                    "name": export_settings.tex_set.name(),
                    "maps": [
                        # Default RenderMan maps
                        *Exporter._shader_maps(export_settings),
                        # Extra AOVs
                        *[
                            {
                                "fileName": f"$textureSet_{getattr(ch, 'label', None) and ch.label().replace(' ', '') or ch.type().name}(_$colorSpace)(.$udim)",
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
                                    "sizeLog2": export_settings.resolution,
                                },
                            }
                            for ch in export_settings.extra_channels
                            for colors, bit_depth in re.findall(
                                r"^s?(L|RGB)(\d{1,2}F?)$",
                                export_settings.tex_set.get_stack()
                                .get_channel(ch.type())
                                .format()
                                .name,
                            )
                        ],
                        # Preview Surface
                        *Exporter._preview_surface_maps(),
                    ],
                }
                for export_settings in export_settings_arr
            ],
            "exportList": [
                {
                    "rootPath": str(export_settings.tex_set.get_stack()),
                    "exportPreset": export_settings.tex_set.name(),
                }
                for export_settings in export_settings_arr
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

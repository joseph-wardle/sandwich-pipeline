"""Write material metadata for exported Substance Painter texture sets."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import substance_painter as sp

from dcc.substance_painter.publish.types import TexSetExportSettings
from dcc.substance_painter.util.util import texture_set_name
from core.struct.material import MaterialInfo, TexSetInfo


def write_material_info(
    out_path: Path,
    export_settings_arr: Iterable[TexSetExportSettings],
) -> None:
    """Write the published material metadata file."""
    mat_info_path = out_path / "mat.json"
    if mat_info_path.exists():
        old_mat_info = MaterialInfo.from_json(mat_info_path.read_text(encoding="utf-8"))
    else:
        old_mat_info = MaterialInfo()

    all_tex_sets = [texture_set_name(ts) for ts in sp.textureset.all_texture_sets()]
    for tex_set in list(old_mat_info.tex_sets.keys()):
        if tex_set not in all_tex_sets:
            del old_mat_info.tex_sets[tex_set]

    new_mat_info = MaterialInfo(
        {
            **old_mat_info.tex_sets,
            **{
                texture_set_name(export_settings.tex_set): TexSetInfo(
                    displacement_source=export_settings.displacement_source,
                    has_udims=export_settings.tex_set.has_uv_tiles(),
                    normal_source=export_settings.normal_source,
                    normal_type=export_settings.normal_type,
                )
                for export_settings in export_settings_arr
            },
        }
    )
    mat_info_path.write_text(new_mat_info.to_json(), encoding="utf-8")

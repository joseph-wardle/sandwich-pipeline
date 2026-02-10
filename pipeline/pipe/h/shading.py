from __future__ import annotations

import os
from pathlib import Path

import hou
from env_sg import DB_Config

from pipe.db import DB
from pipe.struct.db import Asset

_MATLIB_NAME = "Material_Library"
_MATNAME = "matname"
_NO_TEXTURES = "NO_EXPORTED_TEXTURES"


class MatlibManager:
    _conn: DB

    def __init__(self, node: hou.LopNode | None = None) -> None:
        self._conn = DB.Get(DB_Config)
        if node:
            self._init_hda(node)

    def _init_hda(self, node: hou.LopNode) -> None:
        """Initialize values on the HDA instance.
        Note that self.node does not work before initialization, so
        node is passed in as an arg"""
        self._update_default_mat_var(node=node)
        self._update_default_geo_var(node=node)

    @property
    def _asset(self) -> Asset:
        """Get asset based off of the path of the current hipfile"""
        asset_name = str(hou.contextOption("ASSET"))
        return self._conn.get_asset_by_name(asset_name)

    @property
    def _hip(self) -> Path:
        """Get $HIP variable as a Path object"""
        return Path(hou.hscriptStringExpression("$HIP"))

    @property
    def _hsite(self) -> Path:
        """Get $HSITE variable as a Path object"""
        return Path(hou.hscriptStringExpression("$HSITE"))

    @property
    def matlib(self) -> hou.LopNode:
        """Get Material Library node inside of current node"""
        node = hou.node(f"./{_MATLIB_NAME}")
        assert isinstance(node, hou.LopNode)
        return node

    @property
    def node(self) -> hou.LopNode:
        """Get current node (the HDA)"""
        node = hou.node("./")
        assert isinstance(node, hou.LopNode)
        return node

    @property
    def geo_variant_name(self) -> str:
        geo_var_name = self.node.parm("geo_var")
        assert geo_var_name is not None
        return geo_var_name.unexpandedString()

    @property
    def mat_variant_name(self) -> str:
        mat_var_name = self.node.parm("mat_var")
        assert mat_var_name is not None
        return mat_var_name.unexpandedString()

    def _update_default_geo_var(self, node: hou.LopNode | None = None) -> None:
        # this may be called before initialization, so `self.node` may not work
        if not node:
            node = self.node
        # update geo_variant on the hda
        geo_var = node.parm("geo_var")
        assert geo_var is not None
        geo_var.set(next(iter(self._asset.geometry_variants)))

    def _update_default_mat_var(self, node: hou.LopNode | None = None) -> None:
        # this may be called before initialization, so `self.node` may not work
        if not node:
            node = self.node
        # update mat_variant on the hda
        mat_var = node.parm("mat_var")
        assert mat_var is not None
        mat_var.set(next(iter(self._asset.material_variants), _NO_TEXTURES))

    def create_layered_material(
        self, node: hou.Node, layer_mixer: hou.Node, layer_name: str, offset: float
    ):
        """Creates a PxrTexture, PxrNormalMap, and PxrLayer inside this node."""
        print("HELLO I AM HERE")
        # Create nodes
        roughness = node.createNode(
            "pxrtexture::3.0", f"SpecularRoughness_{layer_name}"
        )
        roughness_remap = node.createNode(
            "pxrremap::3.0", f"RoughnessRemap_{layer_name}"
        )
        color = node.createNode("pxrtexture::3.0", f"BaseColor_{layer_name}")
        normal = node.createNode("pxrnormalmap::3.0", f"Normal_{layer_name}")
        layer = node.createNode("pxrlayer::3.0", f"Layer_{layer_name}")
        metallic_workflow = node.createNode(
            "pxrmetallicworkflow::3.0", f"Met_workflow_{layer_name}"
        )
        metallic = node.createNode("pxrtexture::3.0", f"Mettallic_{layer_name}")

        # Position them
        roughness.setPosition(hou.Vector2(-6, -3 - offset * 7))
        roughness_remap.setPosition(hou.Vector2(-3, -3 - offset * 7))
        color.setPosition(hou.Vector2(-3, 3 - offset * 7))
        normal.setPosition(hou.Vector2(0, -4 - offset * 7))
        layer.setPosition(hou.Vector2(3, 0 - offset * 7))
        metallic.setPosition(hou.Vector2(-3, 0 - offset * 7))
        metallic_workflow.setPosition(hou.Vector2(0, 0 - offset * 7))

        # Connect them
        roughness_remap.setNamedInput("inputRGB", roughness, "resultRGB")
        layer.setNamedInput("diffuseColor", metallic_workflow, "resultDiffuseRGB")
        layer.setNamedInput(
            "specularFaceColor", metallic_workflow, "resultSpecularFaceRGB"
        )
        layer.setNamedInput(
            "specularEdgeColor", metallic_workflow, "resultSpecularEdgeRGB"
        )
        layer.setNamedInput("specularRoughness", roughness_remap, "resultR")
        layer.setNamedInput("bumpNormal", normal, "resultN")
        metallic_workflow.setNamedInput("baseColor", color, "resultRGB")
        metallic_workflow.setNamedInput("metallic", metallic, "resultR")

        if offset != 0:
            layer_mixer.setNamedInput(f"layer{offset}", layer, "pxrMaterialOut")
            layer_mixer.parm(f"layer{offset}Enabled").set(True)  # type: ignore[union-attr]
        else:
            layer_mixer.setNamedInput("baselayer", layer, "pxrMaterialOut")
            layer_mixer.parm("layer1Enabled").set(False)  # type: ignore[union-attr]

        # set parameters
        color_file = color.parm("filename")
        if color_file is not None:
            color_file.set(
                f'$HIP/publish/tex/{self.geo_variant_name}/{self.mat_variant_name}/{layer_name}/`chs("../../textureset")`_BaseColor_ACES - ACEScg.<UDIM>.tex'
            )

        roughness_file = roughness.parm("filename")
        if roughness_file is not None:
            roughness_file.set(
                f'$HIP/publish/tex/{self.geo_variant_name}/{self.mat_variant_name}/{layer_name}/`chs("../../textureset")`_SpecularRoughness_Utility - Raw.<UDIM>.tex'
            )

        normal_file = normal.parm("filename")
        if normal_file is not None:
            normal_file.set(
                f'$HIP/publish/tex/{self.geo_variant_name}/{self.mat_variant_name}/{layer_name}/`chs("../../textureset")`_Normal_Utility - Raw.<UDIM>.tex'
            )

        metallic_file = metallic.parm("filename")
        if metallic_file is not None:
            metallic_file.set(
                f'$HIP/publish/tex/{self.geo_variant_name}/{self.mat_variant_name}/{layer_name}/`chs("../../textureset")`_Metallic_Utility - Raw.<UDIM>.tex'
            )

        color_space = color.parm("filename_colorspace")
        if color_space is not None:
            color_space.set("srgb_texture")

        layer.parm("enableSpecular").set(True)  # type: ignore[union-attr]
        layer.parm("specularGain").set(1.0)  # type: ignore[union-attr]

    def get_geo_variant_list(self) -> list[str]:
        """Gets list of variants in the way that the HDA interface expects:
        [id1, label1, id2, label2, ...]"""
        mvs = list(self._asset.geometry_variants)
        return [s for v in mvs for s in (v, v)]

    def get_mat_variant_list(self) -> list[str]:
        """Gets list of mat variants in the way that the HDA interface
        expects: [id1, label1, id2, label2, ...]"""
        mvs = list(self._asset.material_variants) or [_NO_TEXTURES]
        return [s for v in mvs for s in (v, v)]

    def create_matnet(
        self, houdini_filepath: str, node: hou.LopNode | None = None
    ) -> None:
        if not node:
            node = self.node

        # Make sure we're inside a VOP network
        print("OKOKOKO")
        vopnet = None
        for child in node.children():
            if child.type().name() == "materiallibrary":
                vopnet = child
                break

        if vopnet is None:
            return

        tex_path = f"{houdini_filepath}/publish/tex/{self.geo_variant_name}/{self.mat_variant_name}"
        if not os.path.exists(tex_path):
            print(f"Path does not exist: {tex_path}")
            return

        layers = [
            name
            for name in os.listdir(tex_path)
            if os.path.isdir(os.path.join(tex_path, name))
        ]

        layer_mixer = vopnet.createNode("pxrlayermixer::3.0", "Layer_Mixer")
        if layer_mixer is None:
            return

        layer_mixer.setPosition(hou.Vector2(6, 0))

        layer_surface = vopnet.createNode(
            "pxrlayersurface::3.0", f"Surface_{self.mat_variant_name}"
        )
        if layer_surface is None:
            return

        layer_surface.setPosition(hou.Vector2(9, 0))

        layer_surface.setInput(0, layer_mixer, 0)
        print(layers)

        for i, layer in enumerate(layers):
            self.create_layered_material(vopnet, layer_mixer, layer, i)

    # def export_selected_to_path(
    #     self, path: str, curr_name: str = _MATNAME, new_name: str = _MATNAME
    # ) -> None:
    #     """Export selected items as a cpio file to the path given. For
    #     convenience, rename their suffixes to _MATNAME before exporting,
    #     then change their names back"""
    #     items = hou.selectedItems()
    #     self._rename_matnet(items, new_name, curr_name)
    #     items[0].parent().saveItemsToFile(items, path)
    #     self._rename_matnet(items, curr_name, new_name)


class MatlibErrorChecker:
    @staticmethod
    def CheckFilepathsRelative(matlib: hou.LopNode) -> int:
        """Returns 1 if there are any absolute filepaths in the material
        library, 0 otherwise"""
        for node in matlib.children():
            if (fn := node.parm("filename")) is not None:
                if not fn.unexpandedString().startswith("$"):
                    return 1
        return 0

from __future__ import annotations

IMPORT_CAMERA = "skd::main::import_camera::1.0"
LOAD_LAYERS = "skd::main::load_layers::1.0"
RIG_MATERIAL_CONFIG = "skd::main::rig_material_config::1.0"

RENAMED_FROM: dict[str, str] = {
    IMPORT_CAMERA: "dbclark::Bobo_Import_Camera",
    LOAD_LAYERS: "dbclark::main::Bobo_Load_Layers::1.0",
    RIG_MATERIAL_CONFIG: "graphite::SKD_rig_material_config::1.0",
}

RETIRED_SOPS: tuple[str, ...] = (
    "skd::main::SKD_generate_preview_uvs::1.0",
    "sdm223::main::LnD_Pack_UDIMs::1.0",
)

from pathlib import Path

from core.asset import paths_for_asset
from core.shotgrid import Asset


def get_rig_filepath_from_asset(asset: Asset) -> Path:
    asset_paths = paths_for_asset(asset)
    return (asset_paths.rig_path / asset.name).with_suffix(".mb")

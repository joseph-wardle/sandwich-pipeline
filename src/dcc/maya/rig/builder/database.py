from env_sg import DB_Config
from Qt import QtCore

from core.shotgrid import ShotGrid


class DBWorker(QtCore.QObject):
    # Signals to send data back to the main thread
    rigs_loaded = QtCore.Signal(list, list)

    def __init__(self):
        super().__init__()
        self._conn: ShotGrid | None = None

    def _get_database(self) -> ShotGrid:
        if self._conn is None:
            self._conn = ShotGrid.connect(DB_Config)
        return self._conn

    def get_asset_by_tag(self, tag: str) -> list[tuple[str, str]]:
        assets = self._get_database().find_assets(tags={tag})
        return [(asset.name, asset.display_name) for asset in assets]

    def get_asset_by_type(self, type: str) -> list[tuple[str, str]]:
        assets = self._get_database().find_assets(type=type)
        return [(asset.name, asset.display_name) for asset in assets]

    def get_rig_data(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        characters = self.get_asset_by_type(type="Character")
        props = self.get_asset_by_tag(tag="SKD_02_rigged_asset")
        self.rigs_loaded.emit(characters, props)
        return (characters, props)

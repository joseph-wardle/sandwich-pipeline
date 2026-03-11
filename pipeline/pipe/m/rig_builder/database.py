from env_sg import DB_Config
from Qt import QtCore

from pipe.db import DB
from pipe.db.sgaadb import SGaaDB


class DBWorker(QtCore.QObject):
    # Signals to send data back to the main thread
    rigs_loaded = QtCore.Signal(list, list)

    def __init__(self):
        super().__init__()
        self._conn: SGaaDB | None = None

    def _get_database(self) -> SGaaDB:
        if self._conn is not None:
            return self._conn
        else:
            self._conn = DB.Get(DB_Config)
            return self._conn

    def get_asset_by_tag(self, tag: str) -> list[tuple[str, str]]:
        assets = self._get_database().get_assets_by_tag(tags=tag)
        return [(asset.name, asset.display_name) for asset in assets]

    def get_asset_by_type(self, type: str) -> list[tuple[str, str]]:
        asset_names = self._get_database().get_asset_name_list_by_type([type])
        asset_display_names = self._get_database().get_asset_display_name_list_by_type(
            [type]
        )
        return list(zip(asset_names, asset_display_names))

    def get_rig_data(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        characters = self.get_asset_by_type(type="Character")
        props = self.get_asset_by_tag(tag="SKD_02_rigged_asset")
        self.rigs_loaded.emit(characters, props)
        return (characters, props)

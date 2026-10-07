import os
import re
import time
from functools import partial
from pathlib import Path

import nuke
import skd_read_node
from env_sg import DB_Config
from Qt import QtCore, QtWidgets

from pipe.core import render
from pipe.core.shotgrid import ShotGrid
from pipe.core.util.paths import get_production_path

simple_window = None


def _render_dir(shot: str) -> str:
    return str(get_production_path() / "shot" / shot / "render")


class CascadingComboBox(QtWidgets.QWidget):
    def __init__(self):
        super(CascadingComboBox, self).__init__()

        # Set up the window
        self.setWindowTitle("L&D Import Render Layers!!")
        self.setGeometry(100, 100, 800, 600)

        # Mode tracking: "layers" for showing a shot's render layers,
        # "versions" for showing the versions of one layer.
        self.current_mode = "layers"
        self.current_layer = ""

        # Create a label for the title
        self.title_label = QtWidgets.QLabel(
            "This tool was not written by Scott, believe it or not", self
        )
        self.title_label.setAlignment(QtCore.Qt.AlignCenter)

        # Create a tool button to mimic a cascading combobox
        self.tool_button = QtWidgets.QToolButton(self)
        self.tool_button.setText("Select Shot")
        self.tool_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)

        # Create the main menu
        self.menu = QtWidgets.QMenu(self)
        self.tool_button.setMenu(self.menu)

        # Get and categorize shots
        all_shots = self.get_shots()
        categorized_data = self.categorize_data(all_shots)

        # Populate the cascading menu
        self.populate_cascading_menu(categorized_data)

        # Get the default shot from the current Nuke file name
        self.default_shot = os.path.basename(nuke.Root().name())[:-3]
        if not bool(re.match(r"^[A-Za-z]_", self.default_shot)):
            self.default_shot = "A_010"

        # Create a label to display the current shot
        self.current_shot_label = QtWidgets.QLabel(
            f"Current Shot: {self.default_shot}", self
        )
        self.current_shot_label.setAlignment(QtCore.Qt.AlignLeft)

        # Create a list widget for displaying thumbnails
        self.thumbnail_list = QtWidgets.QListWidget(self)
        self.thumbnail_list.setViewMode(QtWidgets.QListWidget.IconMode)
        self.thumbnail_list.setIconSize(QtCore.QSize(150, 150))
        self.thumbnail_list.setResizeMode(QtWidgets.QListWidget.Adjust)
        # Initially, enforce single selection in "layers" mode.
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )

        # Load render layers for the default shot.
        self.update_layers(self.default_shot)

        # Create the action button.
        # In "layers" mode, its text will be "Select Layer".
        self.action_button = QtWidgets.QPushButton("Select Layer", self)
        self.action_button.clicked.connect(self.on_action_button_clicked)

        # Create the back button (only visible in versions mode).
        self.back_button = QtWidgets.QPushButton("Back", self)
        self.back_button.clicked.connect(self.go_back)
        self.back_button.hide()  # Hide initially

        # Create the cancel button.
        self.cancel_button = QtWidgets.QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.close)

        # Button layout.
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.back_button)
        button_layout.addWidget(self.action_button)
        button_layout.addWidget(self.cancel_button)

        # Main layout.
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(self.title_label)
        main_layout.addWidget(self.tool_button)
        main_layout.addWidget(self.current_shot_label)
        main_layout.addWidget(self.thumbnail_list)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def get_shots(self):
        """Fetch the list of shots from the database."""
        try:
            conn = ShotGrid.connect(DB_Config)
            return [shot.code for shot in conn.find_shots() if shot.code]
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to fetch shots: {e}")
            return []

    def categorize_data(self, all_shots):
        """
        Categorize shots into groups based on their prefixes.
        """
        categorized_data = {"Other": []}
        for item in all_shots:
            if len(item) > 1 and item[0].isalpha() and item[1] == "_":
                category = item.split("_")[0]
                if category not in categorized_data:
                    categorized_data[category] = []
                categorized_data[category].append(item)
            else:
                categorized_data["Other"].append(item)

        # Sort each category alphabetically
        for key in categorized_data:
            categorized_data[key] = sorted(categorized_data[key])

        # Sort categories and ensure "Other" is last
        sorted_categories = {
            k: categorized_data[k] for k in sorted(categorized_data) if k != "Other"
        }
        if "Other" in categorized_data:
            sorted_categories["Other"] = categorized_data["Other"]
        return sorted_categories

    def populate_cascading_menu(self, categorized_data):
        """
        Populate the cascading menu with categorized shots.
        """
        for category, items in categorized_data.items():
            if category != "Other":
                submenu = self.menu.addMenu(f"{category} Sequence")
                for shot in items:
                    action = submenu.addAction(shot)
                    action.triggered.connect(partial(self.on_shot_selected, shot))
            else:
                other_menu = self.menu.addMenu("Other")
                for shot in items:
                    action = other_menu.addAction(shot)
                    action.triggered.connect(partial(self.on_shot_selected, shot))

    def on_shot_selected(self, shot):
        """
        Handle shot selection from the cascading menu.
        """
        self.tool_button.setText(shot)
        self.current_shot_label.setText(f"Current Shot: {shot}")
        self.default_shot = shot

        # Reset mode to layers and update render layers.
        self.current_mode = "layers"
        self.current_layer = ""
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.action_button.setText("Select Layer")
        self.back_button.hide()
        self.update_layers(shot)

    def update_layers(self, shot: str) -> None:
        """List the render layers of a shot."""
        self.thumbnail_list.clear()
        render_dir = _render_dir(shot)
        layers = (
            sorted(
                name
                for name in os.listdir(render_dir)
                if not name.startswith(".")
                and os.path.isdir(os.path.join(render_dir, name))
            )
            if os.path.isdir(render_dir)
            else []
        )
        for name in layers:
            self.thumbnail_list.addItem(name)
        note = "" if layers else " (nothing rendered yet)"
        self.current_shot_label.setText(f"Current Shot: {shot}{note}")

    def load_versions(self) -> None:
        """Show the versions of the selected render layer, newest first."""
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self, "No Selection", "Please select a render layer."
            )
            return
        self.current_layer = selected_items[0].text()
        layer_dir = os.path.join(_render_dir(self.default_shot), self.current_layer)
        versions = render.versions(Path(layer_dir))
        if not versions:
            QtWidgets.QMessageBox.warning(
                self,
                "No Versions",
                f"{self.current_layer} has no versions yet. Send it to Tractor first.",
            )
            return

        self.thumbnail_list.clear()
        # Change selection mode to allow multiple selection for versions.
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection
        )
        for version in versions:
            date = time.strftime("%m-%d-%Y", time.localtime(version.stat().st_mtime))
            item = QtWidgets.QListWidgetItem(f"{version.name}\n{date}")
            item.setData(QtCore.Qt.UserRole, str(version))
            item.setTextAlignment(int(QtCore.Qt.AlignCenter))
            self.thumbnail_list.addItem(item)

        self.current_mode = "versions"
        self.action_button.setText("Import")
        self.back_button.show()

    def on_action_button_clicked(self):
        """
        Action button click handler.
        In "layers" mode, it loads the selected layer's versions.
        In "versions" mode, it imports the selected versions.
        """
        if self.current_mode == "layers":
            self.load_versions()
        elif self.current_mode == "versions":
            self.import_versions()

    def go_back(self):
        """
        Go back from the versions view to the render layers view.
        """
        self.current_mode = "layers"
        self.current_layer = ""
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.action_button.setText("Select Layer")
        self.back_button.hide()
        self.update_layers(self.default_shot)

    def import_versions(self) -> None:
        """Make a Read for every output folder of each selected version."""
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self,
                "No Selection",
                "Please select at least one version to import.",
            )
            return

        for item in selected_items:
            path = Path(item.data(QtCore.Qt.UserRole))
            version = path.name
            sequences = skd_read_node.version_sequences(path)
            if not sequences:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Nothing to Import",
                    f"{self.current_layer} {version} has no frames to read yet. "
                    "It may still be rendering; check its job in Tractor.",
                )
                continue
            for seq in sequences:
                print("Importing from:", seq["pattern"])
                read = nuke.createNode("Read", "on_error black")
                skd_read_node.read_sequence(read, seq)
                read["label"].setValue(
                    f"{self.current_layer} {version} {seq['folder']}"
                )
                skd_read_node.add_reformat(read)
        self.close()


def show_simple_window():
    global simple_window  # Prevent garbage collection
    simple_window = CascadingComboBox()
    simple_window.show()


def run():
    show_simple_window()


# run()

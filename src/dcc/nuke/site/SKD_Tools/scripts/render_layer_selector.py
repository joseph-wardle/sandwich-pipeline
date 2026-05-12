import os
import random
import re
import time
from functools import partial
from typing import cast

import nuke
from env_sg import DB_Config
from Qt import QtCore, QtGui, QtWidgets

from core.shotgrid import ShotGrid
from core.util.util import get_production_path

simple_window = None


class CascadingComboBox(QtWidgets.QWidget):
    def __init__(self):
        super(CascadingComboBox, self).__init__()

        # Set up the window
        self.setWindowTitle("L&D Import Render Layers!!")
        self.setGeometry(100, 100, 800, 600)

        # Mode tracking: "renders" for showing render folders,
        # "layers" for showing render layers inside a render folder.
        self.current_mode = "renders"
        self.current_render = None  # Holds the selected render folder

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
        # Initially, enforce single selection in "renders" mode.
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )

        # Load render folders for the default shot.
        self.update_renders(self.default_shot)

        # Create the action button.
        # In "renders" mode, its text will be "Select Render".
        self.action_button = QtWidgets.QPushButton("Select Render", self)
        self.action_button.clicked.connect(self.on_action_button_clicked)

        # Create the back button (only visible in layers mode).
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

        # Reset mode to renders and update render folders.
        self.current_mode = "renders"
        self.current_render = None
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.action_button.setText("Select Render")
        self.back_button.hide()
        self.update_renders(shot)

    def update_renders(self, shot_num):
        """
        Update the thumbnail list with render folders for the selected shot,
        sorted by creation date (newest first).
        Also, if a 'beauty' layer exists with a thumb image, use that thumbnail.
        Any folders named '.backup' are ignored.
        """
        self.thumbnail_list.clear()
        base_path = str(get_production_path() / "shot")
        shot_path = os.path.join(base_path, shot_num, "render")
        items_list = []  # Will store tuples of (creation_time, list_item)
        default_thumb = ""

        if os.path.exists(shot_path):
            for folder_name in os.listdir(shot_path):
                # Skip any folder named .backup
                if folder_name.lower() == ".backup":
                    continue

                folder_path = os.path.join(shot_path, folder_name)
                if os.path.isdir(folder_path):
                    # Set the thumbnail to the default.
                    thumbnail_path = default_thumb

                    # --- Check for a beauty layer thumbnail first ---
                    # Look for a folder named "beauty" (case insensitive).
                    beauty_folder = None
                    for subfolder in os.listdir(folder_path):
                        if subfolder.lower() == "beauty":
                            beauty_folder = os.path.join(folder_path, subfolder)
                            break
                    if beauty_folder and os.path.isdir(beauty_folder):
                        thumb_folder = os.path.join(beauty_folder, "thumb")
                        if os.path.exists(thumb_folder) and os.path.isdir(thumb_folder):
                            thumbs = [
                                f
                                for f in os.listdir(thumb_folder)
                                if f.lower().endswith((".png", ".jpg", ".jpeg"))
                            ]
                            if thumbs:
                                thumb_image = random.choice(thumbs)
                                thumbnail_path = os.path.join(thumb_folder, thumb_image)
                                print(
                                    "Using beauty layer thumbnail for render:",
                                    thumbnail_path,
                                )
                    else:
                        # --- Fallback: Check the render folder's own thumbnail folder ---
                        render_thumb_folder = os.path.join(folder_path, "thumbnail")
                        if os.path.exists(render_thumb_folder) and os.path.isdir(
                            render_thumb_folder
                        ):
                            thumbs = [
                                f
                                for f in os.listdir(render_thumb_folder)
                                if f.lower().endswith((".png", ".jpg", ".jpeg"))
                            ]
                            if thumbs:
                                thumb_image = random.choice(thumbs)
                                thumbnail_path = os.path.join(
                                    render_thumb_folder, thumb_image
                                )
                                print("Using render folder thumbnail:", thumbnail_path)
                        else:
                            print(
                                "Using default thumbnail for render folder:",
                                thumbnail_path,
                            )

                    # Create list widget item.
                    item = QtWidgets.QListWidgetItem()
                    pixmap = QtGui.QPixmap(thumbnail_path)
                    scaled_pixmap = pixmap.scaled(
                        316,
                        150,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
                    item.setIcon(QtGui.QIcon(scaled_pixmap))

                    # Define paths for images (to get creation time)
                    images_folder_path = os.path.join(folder_path, "images")
                    try:
                        file_times = [
                            os.path.getmtime(os.path.join(images_folder_path, f))
                            for f in os.listdir(images_folder_path)
                            if f.lower().endswith((".png", ".jpg", ".jpeg", ".exr"))
                        ]
                        if file_times:
                            creation_time = min(file_times)
                        else:
                            creation_time = os.path.getmtime(folder_path)
                    except Exception:
                        creation_time = os.path.getmtime(folder_path)

                    creation_date = time.strftime(
                        "%m-%d-%Y", time.localtime(creation_time)
                    )
                    item.setText(f"{folder_name}\n{creation_date}")
                    item.setTextAlignment(int(QtCore.Qt.AlignCenter))
                    items_list.append((creation_time, item))

        # Sort items by creation time descending (newest first).
        sorted_items = sorted(items_list, key=lambda x: x[0], reverse=True)
        for _, item in sorted_items:
            self.thumbnail_list.addItem(item)

    def load_layers(self):
        """
        Load render layers for the selected render folder.
        The view is updated to show subfolders (render layers) within the selected render.
        Folders named '.backup' are ignored.
        """
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self, "No Selection", "Please select a render folder."
            )
            return

        # Enforce single selection for render folder; get the folder name.
        render_folder = selected_items[0].text().split("\n")[0]
        self.current_render = render_folder

        base_path = str(get_production_path() / "shot")
        shot_path = os.path.join(base_path, self.default_shot, "render", render_folder)

        # Now update the thumbnail list with render layers (subfolders).
        self.thumbnail_list.clear()

        if os.path.exists(shot_path):
            layer_items = []
            # Change selection mode to allow multiple selection for layers.
            self.thumbnail_list.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection
            )

            for layer_name in os.listdir(shot_path):
                # Skip folders named .backup
                if layer_name.lower() == ".backup":
                    continue

                layer_path = os.path.join(shot_path, layer_name)
                if os.path.isdir(layer_path):
                    default_thumb = ""
                    thumbnail_path = default_thumb

                    # Check for a thumb folder inside the layer folder.
                    thumb_folder = os.path.join(layer_path, "thumb")
                    if os.path.exists(thumb_folder) and os.path.isdir(thumb_folder):
                        thumbs = [
                            f
                            for f in os.listdir(thumb_folder)
                            if f.lower().endswith((".png", ".jpg", ".jpeg"))
                        ]
                        if thumbs:
                            thumb_image = random.choice(thumbs)
                            thumbnail_path = os.path.join(thumb_folder, thumb_image)
                            print("Layer thumbnail path:", thumbnail_path)
                    else:
                        print("Using default thumbnail for layer:", thumbnail_path)

                    item = QtWidgets.QListWidgetItem()
                    pixmap = QtGui.QPixmap(thumbnail_path)
                    scaled_pixmap = pixmap.scaled(
                        316,
                        150,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
                    item.setIcon(QtGui.QIcon(scaled_pixmap))
                    item.setText(layer_name)
                    item.setTextAlignment(int(QtCore.Qt.AlignCenter))
                    layer_items.append(item)

            for item in layer_items:
                self.thumbnail_list.addItem(item)

            # Switch mode to "layers" and update button text.
            self.current_mode = "layers"
            self.action_button.setText("Import Layers")
            self.back_button.show()
        else:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Folder",
                f"The render folder '{render_folder}' does not exist.",
            )

    def on_action_button_clicked(self):
        """
        Action button click handler.
        In "renders" mode, it loads render layers.
        In "layers" mode, it imports the selected layers.
        """
        if self.current_mode == "renders":
            self.load_layers()
        elif self.current_mode == "layers":
            self.import_layers()

    def go_back(self):
        """
        Go back from the render layers view to the renders view.
        """
        self.current_mode = "renders"
        self.current_render = None
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.action_button.setText("Select Render")
        self.back_button.hide()
        self.update_renders(self.default_shot)

    def import_layers(self):
        """
        Import selected render layers into Nuke.
        It imports the EXR sequences from each selected layer's 'images_dn' folder.
        """
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self,
                "No Selection",
                "Please select at least one render layer to import.",
            )
            return

        base_path = str(get_production_path() / "shot")
        for item in selected_items:
            layer_folder = (
                item.text()
            )  # In layers mode, the text is just the layer name.
            images_dn_path = os.path.join(
                base_path,
                self.default_shot,
                "render",
                cast(str, self.current_render),
                layer_folder,
                "images_dn",
            )
            print("Importing from:", images_dn_path)

            if os.path.exists(images_dn_path):
                exr_files = [
                    f for f in os.listdir(images_dn_path) if f.lower().endswith(".exr")
                ]
                if exr_files:
                    try:
                        exr_files.sort(
                            key=lambda x: int(os.path.splitext(x)[0].split(".")[-1])
                        )
                    except Exception as e:
                        print("Error sorting EXR files:", e)

                    first_frame = int(os.path.splitext(exr_files[0])[0].split(".")[-1])
                    last_frame = int(os.path.splitext(exr_files[-1])[0].split(".")[-1])
                    print("First frame:", first_frame, "Last frame:", last_frame)

                    sequence_path = os.path.join(images_dn_path, "####.exr")
                    read = nuke.createNode("Read", f"file {{{sequence_path}}}")
                    read["first"].setValue(first_frame)
                    read["last"].setValue(last_frame)
                else:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Missing Sequence",
                        f"No EXR files found in {images_dn_path}",
                    )
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Missing Folder",
                    f"The folder '{images_dn_path}' does not exist.",
                )
        self.close()


def show_simple_window():
    global simple_window  # Prevent garbage collection
    simple_window = CascadingComboBox()
    simple_window.show()


def run():
    show_simple_window()


# run()

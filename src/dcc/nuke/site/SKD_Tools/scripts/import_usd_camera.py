import os
import random
import re
import time
from functools import partial
from typing import Any, cast

import nuke
from env_sg import DB_Config
from Qt import QtCore, QtGui, QtWidgets
from core.util.util import get_production_path

from core.shotgrid import ShotGrid

simple_window = None


class CascadingComboBox(QtWidgets.QWidget):
    def __init__(self):
        super(CascadingComboBox, self).__init__()
        self.setWindowTitle("L&D Import Render Layers!!")
        self.setGeometry(100, 100, 800, 600)

        # Mode tracking: "renders" for render folders, "layers" for render layers.
        self.current_mode = "renders"
        self.current_render = None

        # Use a random sentence for the title label.
        random_sentences = [
            "What's the best kind of music to listen to when fishing? Something catchy.",
            "How did the pirate get his ship for so cheap? It was on sail.",
            "Why do dads take an extra pair of socks when they play golf? In case they get a hole in one.",
            "As the dog said when the train ran over his tail --It won't be long now.",
            "What comes once in a minute, twice in a moment but never in a thousand years? The letter M.",
            "What's the best kind of bird to work for a construction company? A crane.",
            "It is awfully hard work doing nothing. I don't mind working hard if I don't have to do anything.",
            "What did the T-Rex use to cut wood? A dino-saw.",
            "A gentleman is someone who can play the accordion, but doesn't.",
            "So what if I can't spell Aarghmageddon, it's not like it's the end of the world.",
            "I only know 25 letters of the alphabet. I don't know y.",
            "What has five toes and isn't your foot? My foot.",
            "Why do bees have sticky hair? Because they use a honeycomb.",
            "I can tolerate algebra, maybe even a little calculus, but geometry is where I draw the line.",
        ]
        self.title_label = QtWidgets.QLabel(random.choice(random_sentences), self)
        self.title_label.setAlignment(QtCore.Qt.AlignCenter)

        self.tool_button = QtWidgets.QToolButton(self)
        self.tool_button.setText("Select Shot")
        self.tool_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.menu = QtWidgets.QMenu(self)
        self.tool_button.setMenu(self.menu)

        all_shots = self.get_shots()
        categorized_data = self.categorize_data(all_shots)
        self.populate_cascading_menu(categorized_data)

        # Derive default shot from the Nuke root name.
        self.default_shot = os.path.basename(nuke.Root().name())[:-3]
        if not re.match(r"^[A-Za-z]_", self.default_shot):
            self.default_shot = "A_010"

        self.current_shot_label = QtWidgets.QLabel(
            f"Selected Shot: {self.default_shot}", self
        )
        self.current_shot_label.setAlignment(QtCore.Qt.AlignLeft)

        self.instructions = QtWidgets.QLabel(
            "Select the render you want the camera for:", self
        )
        instructions_font = QtGui.QFont()
        instructions_font.setPointSize(10)
        self.instructions.setFont(instructions_font)
        self.instructions.setAlignment(QtCore.Qt.AlignCenter)

        self.thumbnail_list = QtWidgets.QListWidget(self)
        self.thumbnail_list.setViewMode(QtWidgets.QListWidget.ListMode)
        self.thumbnail_list.setIconSize(QtCore.QSize(60, 60))
        self.thumbnail_list.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )

        self.update_renders(self.default_shot)

        self.action_button = QtWidgets.QPushButton("Import Camera", self)
        self.action_button.clicked.connect(self.import_camera)

        self.back_button = QtWidgets.QPushButton("Back", self)
        self.back_button.clicked.connect(self.go_back)
        self.back_button.hide()

        self.cancel_button = QtWidgets.QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.close)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.back_button)
        button_layout.addWidget(self.action_button)
        button_layout.addWidget(self.cancel_button)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(self.title_label)
        main_layout.addWidget(self.tool_button)
        main_layout.addWidget(self.current_shot_label)
        main_layout.addWidget(self.instructions)
        main_layout.addWidget(self.thumbnail_list)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def get_shots(self):
        try:
            conn = ShotGrid.connect(DB_Config)
            return [shot.code for shot in conn.find_shots() if shot.code]
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to fetch shots: {e}")
            return []

    def import_camera(self):
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self,
                "No Selection",
                "Please select a render folder to import the camera from.",
            )
            return

        item = selected_items[0]
        widget = self.thumbnail_list.itemWidget(item)
        render_folder = cast(Any, widget).layout().itemAt(0).widget().text()

        base_path = str(get_production_path() / "shot")
        render_dir = os.path.join(base_path, self.default_shot, "render", render_folder)

        camera_path = ""
        # 1. Check for the 'beauty' folder first.
        beauty_path = os.path.join(render_dir, "beauty", "render.usd")
        if os.path.exists(beauty_path):
            camera_path = beauty_path
        else:
            # 2. Check subfolders (ignoring .backup and beauty) for render.usd.
            for subfolder_name in os.listdir(render_dir):
                if subfolder_name.lower() in [".backup", "beauty"]:
                    continue
                subfolder_path = os.path.join(render_dir, subfolder_name)
                if os.path.isdir(subfolder_path):
                    candidate = os.path.join(subfolder_path, "render.usd")
                    if os.path.exists(candidate):
                        camera_path = candidate
                        break

        # 3. If still not found, check if render.usd exists directly in render_dir.
        if not camera_path:
            candidate = os.path.join(render_dir, "render.usd")
            if os.path.exists(candidate):
                camera_path = candidate

        if not camera_path:
            QtWidgets.QMessageBox.warning(
                self,
                "File Not Found",
                f"Could not find a valid render.usd in the expected locations:\n{render_dir}",
            )
            return

        try:
            cam = nuke.createNode("Camera3")
            cam["read_from_file"].setValue(True)
            cam["file"].setValue(camera_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Import Failed", f"Could not import camera:\n{e}"
            )

        self.close()

    def categorize_data(self, all_shots):
        categorized_data = {"Other": []}
        for item in all_shots:
            if len(item) > 1 and item[0].isalpha() and item[1] == "_":
                category = item.split("_")[0]
                categorized_data.setdefault(category, []).append(item)
            else:
                categorized_data["Other"].append(item)
        for key in categorized_data:
            categorized_data[key] = sorted(categorized_data[key])
        sorted_categories = {
            k: categorized_data[k] for k in sorted(categorized_data) if k != "Other"
        }
        if "Other" in categorized_data:
            sorted_categories["Other"] = categorized_data["Other"]
        return sorted_categories

    def populate_cascading_menu(self, categorized_data):
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
        self.tool_button.setText(shot)
        self.current_shot_label.setText(f"Current Shot: {shot}")
        self.default_shot = shot

        self.current_mode = "renders"
        self.current_render = None
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.action_button.setText("Select Render")
        self.back_button.hide()
        self.update_renders(shot)

    def update_renders(self, shot_num):
        self.thumbnail_list.clear()
        base_path = str(get_production_path() / "shot")
        shot_path = os.path.join(base_path, shot_num, "render")
        items_list = []

        if os.path.exists(shot_path):
            for folder_name in os.listdir(shot_path):
                if folder_name.lower() == ".backup":
                    continue
                folder_path = os.path.join(shot_path, folder_name)
                if os.path.isdir(folder_path):
                    images_folder_path = os.path.join(folder_path, "images")
                    try:
                        file_times = [
                            os.path.getmtime(os.path.join(images_folder_path, f))
                            for f in os.listdir(images_folder_path)
                            if f.lower().endswith((".png", ".jpg", ".jpeg", ".exr"))
                        ]
                        creation_time = (
                            min(file_times)
                            if file_times
                            else os.path.getmtime(folder_path)
                        )
                    except Exception:
                        creation_time = os.path.getmtime(folder_path)

                    # Build a custom widget for the list item.
                    item = QtWidgets.QListWidgetItem()
                    widget = QtWidgets.QWidget()
                    layout = QtWidgets.QHBoxLayout()
                    layout.setContentsMargins(10, 4, 10, 4)

                    name_label = QtWidgets.QLabel(folder_name)
                    name_label.setAlignment(
                        QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                    )
                    creation_date = time.strftime(
                        "%m-%d-%Y", time.localtime(creation_time)
                    )
                    date_label = QtWidgets.QLabel(creation_date)
                    date_label.setAlignment(
                        QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                    )

                    layout.addWidget(name_label)
                    layout.addStretch()
                    layout.addWidget(date_label)
                    widget.setLayout(layout)
                    item.setSizeHint(widget.sizeHint())

                    items_list.append((creation_time, item, widget))

        # Add items sorted by creation time (newest first).
        items_list.sort(key=lambda x: x[0], reverse=True)
        for _, item, widget in items_list:
            self.thumbnail_list.addItem(item)
            self.thumbnail_list.setItemWidget(item, widget)

    def load_layers(self):
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self, "No Selection", "Please select a render folder."
            )
            return

        item = selected_items[0]
        widget = self.thumbnail_list.itemWidget(item)
        render_folder = cast(Any, widget).layout().itemAt(0).widget().text()
        self.current_render = render_folder

        base_path = str(get_production_path() / "shot")
        shot_path = os.path.join(base_path, self.default_shot, "render", render_folder)
        self.thumbnail_list.clear()

        if os.path.exists(shot_path):
            layer_items = []
            self.thumbnail_list.setSelectionMode(
                QtWidgets.QAbstractItemView.ExtendedSelection
            )

            for layer_name in os.listdir(shot_path):
                if layer_name.lower() == ".backup":
                    continue

            for item in layer_items:
                self.thumbnail_list.addItem(item)

            self.current_mode = "layers"
            self.action_button.setText("Import Layers")
            self.back_button.show()
        else:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Folder",
                f"The render folder '{render_folder}' does not exist.",
            )

    def go_back(self):
        self.current_mode = "renders"
        self.current_render = None
        self.thumbnail_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection
        )
        self.action_button.setText("Select Render")
        self.back_button.hide()
        self.update_renders(self.default_shot)

    def import_layers(self):
        selected_items = self.thumbnail_list.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(
                self,
                "No Selection",
                "Please select at least one render layer to import.",
            )
            return

        base_path = "/groups/dungeons/production/shot"
        for item in selected_items:
            layer_folder = item.text()
            images_dn_path = os.path.join(
                base_path,
                self.default_shot,
                "render",
                cast(str, self.current_render),
                layer_folder,
                "images_dn",
            )

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

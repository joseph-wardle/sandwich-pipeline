import os

import nuke
from core.util.paths import get_production_path
from Qt import QtCore, QtWidgets
from Qt.QtWidgets import QComboBox

window = None


class MyWindow(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(MyWindow, self).__init__(parent)

        # Initialize shot lists
        self.a_shots = []
        self.b_shots = []
        self.c_shots = []
        self.d_shots = []
        self.e_shots = []
        self.f_shots = []
        self.g_shots = []
        self.z_shots = []

        # Get and parse shots
        shots_all = self.get_shots()
        self.parse_list(shots_all)

        self.setWindowTitle("SKD Open Shot")
        self.setWindowFlags(
            self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint
        )  # Keep on top

        # Create sequence dropdown
        sequence_dropdown_vlayout = QtWidgets.QVBoxLayout()
        label1 = QtWidgets.QLabel("Sequence")
        self.sequence_combobox = QComboBox()
        self.sequence_combobox.addItems(["A", "B", "C", "D", "E", "F", "G", "Z"])
        sequence_dropdown_vlayout.addWidget(label1)
        sequence_dropdown_vlayout.addWidget(self.sequence_combobox)

        # Create shot numbers dropdown
        shotnum_dropdown_vlayout = QtWidgets.QVBoxLayout()
        label2 = QtWidgets.QLabel("Shot Number")
        self.shotnum_combobox = QComboBox()
        shotnum_dropdown_vlayout.addWidget(label2)
        shotnum_dropdown_vlayout.addWidget(self.shotnum_combobox)

        # Initially populate with shots for sequence A
        self.populate_shotnum("A")

        # Both dropdowns horizontal layout
        dropdowns = QtWidgets.QHBoxLayout()
        dropdowns.addLayout(sequence_dropdown_vlayout)
        dropdowns.addLayout(shotnum_dropdown_vlayout)

        # button
        button = QtWidgets.QPushButton("Open Shot")
        # selected_shot = self.shotnum_combobox.currentText()
        button.clicked.connect(
            lambda: (
                self.open_nk_shot(self.shotnum_combobox.currentText()),
                self.close(),
            )
        )

        # Main layout
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addLayout(dropdowns)
        main_layout.addWidget(button)
        self.setLayout(main_layout)

        # Update shotnum_combobox when the sequence changes
        self.sequence_combobox.currentTextChanged.connect(self.populate_shotnum)

    def populate_shotnum(self, sequence):
        """Update shotnum_combobox with shots corresponding to the selected sequence."""
        self.shotnum_combobox.clear()
        if sequence == "A":
            for shot in self.a_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "B":
            for shot in self.b_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "C":
            for shot in self.c_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "D":
            for shot in self.d_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "E":
            for shot in self.e_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "F":
            for shot in self.f_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "G":
            for shot in self.g_shots:
                self.shotnum_combobox.addItem(shot)
        elif sequence == "Z":
            for shot in self.z_shots:
                self.shotnum_combobox.addItem(shot)

    def get_shots(self):
        from env_sg import DB_Config

        from core.shotgrid import ShotGrid

        conn = ShotGrid.connect(DB_Config)
        return [shot.code for shot in conn.find_shots() if shot.code]

    def parse_list(self, shots):
        for shot in shots:
            # if len(shot) < 2 or shot[1] != "_" or not shot[0].isalpha():
            # continue
            if not shot:
                continue
            letter = shot[:2].upper()
            if letter == "A_":
                self.a_shots.append(shot)
            elif letter == "B_":
                self.b_shots.append(shot)
            elif letter == "C_":
                self.c_shots.append(shot)
            elif letter == "D_":
                self.d_shots.append(shot)
            elif letter == "E_":
                self.e_shots.append(shot)
            elif letter == "F_":
                self.f_shots.append(shot)
            elif letter == "G_":
                self.g_shots.append(shot)
            else:
                self.z_shots.append(shot)

        # Sort each list alphabetically
        self.a_shots.sort()
        self.b_shots.sort()
        self.c_shots.sort()
        self.d_shots.sort()
        self.e_shots.sort()
        self.f_shots.sort()
        self.g_shots.sort()
        self.z_shots.sort()

    def check_file_exists(self, shot_num):
        shot_root = get_production_path() / "shot" / shot_num
        file_path_os = str(shot_root / "comp" / f"{shot_num}.nk")
        if os.path.exists(file_path_os):
            print(f"File '{file_path_os}' exists.")
            return
        else:
            print(f"File '{file_path_os}' does not exist. Creating now.")

            comp_folder = shot_root / "comp"
            shot_root.mkdir(exist_ok=True)
            comp_folder.mkdir(exist_ok=True)
            nuke.scriptSaveAs(file_path_os)  # create the .nk file
            return

    def open_nk_shot(self, shot_num):
        self.check_file_exists(shot_num)
        nk_file_path = str(
            get_production_path() / "shot" / shot_num / "comp" / f"{shot_num}.nk"
        )
        try:
            nuke.scriptOpen(nk_file_path)
            print(f"Successfully opened script: {nk_file_path}")
        except RuntimeError as e:
            print(f"Error opening script: {e}")


def run():
    global window  # Prevent garbage collection
    window = MyWindow()
    window.show()


# run()

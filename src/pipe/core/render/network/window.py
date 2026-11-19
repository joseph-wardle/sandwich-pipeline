"""The Sandwich Renderer window: choose a shot, edit its render layers, send them.

Every action runs the headless Houdini worker, which opens the shot's render
hip. Nothing in the window changes the hip until Save or Send.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network.client import WorkerRun
from pipe.core.render.network.log_viewer import LogViewer
from pipe.core.render.network.departments import DepartmentPanel
from pipe.core.render.network.doctor_page import DoctorPage
from pipe.core.render.network.farm_tab import FarmTab
from pipe.core.render.network.overview import OverviewPage
from pipe.core.render.network.form import ParmForm
from pipe.core.render.network.protocol import (
    DESCRIBE,
    LIGHTING_HIP,
    PUBLISH_LIGHTING,
    SAVE,
    SEND,
    LightingChoice,
    Result,
    Edits,
    shot_folder,
)
from pipe.core.render.network.scene_graph import SceneGraphPanel
from pipe.core.render.network import icons, status_icons, theme, widgets
from pipe.core.render.network.shots import ShotList
from pipe.core.render.network.viewer import FrameViewer
from pipe.core.render.lighters import LIGHTING_STEP
from pipe.core.publish import Target
from pipe.core.shotgrid import Shot, ShotGrid, Status
from pipe.core.render.network import background, render_status
from pipe.core.render.network.render_status import RenderBoard, RenderStatus
from pipe.core.ui import prompt_publish
from pipe.core.util.paths import get_repo_root

TITLE = "Sandwich Renderer"
_LOGO = get_repo_root() / "resources/icon/sandwich_renderer.png"
# How far the shot's title and render hip sit in from the edge, in pixels.
_TITLE_INSET = 16
# The window's pages, in the header's order: their icon and name.
_PAGES = (
    ("clapperboard", "Shots", "Edit a shot's render layers and send them"),
    ("activity", "Tractor Overview", "Every unfinished job on the farm"),
    ("stethoscope", "Farm Doctor", "Why tasks failed, and which a retry will fix"),
)
_BUSY_WORDS = {
    DESCRIBE: "Opening {shot} in Houdini…",
    SAVE: "Saving {shot}'s render hip…",
    SEND: "Saving {shot} and sending it to Tractor…",
    PUBLISH_LIGHTING: "Publishing {shot}'s lighting hip…",
}
_FRAME_RANGES = {
    "off": "the hip's current frame only",
    "stage": "the frame range the stage says",
}
# Notes that start with these words say what the window did for the artist,
# rather than warn them.
_INFO_NOTES = ("Added ",)


class RenderNetworkWindow(QtWidgets.QWidget):
    _shot_list: ShotList
    _heading: QtWidgets.QLabel
    _status_pill: QtWidgets.QToolButton
    _unsaved: widgets.Chip
    _hip_icon: QtWidgets.QLabel
    _hip: QtWidgets.QLabel
    _notes: QtWidgets.QWidget
    _notes_layout: QtWidgets.QVBoxLayout
    _banners: list[widgets.Banner]
    _scroll: QtWidgets.QScrollArea
    _scene_graph: SceneGraphPanel
    _departments: DepartmentPanel
    _tabs: QtWidgets.QTabWidget
    _farm: FarmTab
    _overview: OverviewPage
    _doctor: DoctorPage
    _pages: QtWidgets.QStackedWidget
    _nav: QtWidgets.QButtonGroup
    _viewer: FrameViewer
    _busy: QtWidgets.QWidget
    _busy_text: QtWidgets.QLabel
    _log: LogViewer
    _reload: QtWidgets.QPushButton
    _save: QtWidgets.QPushButton
    _send: QtWidgets.QPushButton

    _form: ParmForm | None
    _result: Result | None
    _run: WorkerRun | None
    _dirty: bool
    # Which folders were open, by shot, so a reload keeps the artist's place.
    _form_states: dict[str, dict[str, Any]]
    # The shot whose parms show, or are opening.
    _shown: str | None

    def __init__(
        self,
        shots: Sequence[Shot],
        open_shot: str | None = None,
        lit_shots: set[str] | None = None,
        admin: bool = False,
        render: RenderBoard | None = None,
    ) -> None:
        """`lit_shots` are the codes the artist is assigned to light, which they
        may publish lighting for; None when ShotGrid couldn't say. An `admin`
        may publish any shot's lighting. `render` is each shot's Render
        task status, shown beside it; the same artists may change it."""
        super().__init__()
        self._admin = admin
        self._shots_by_code = {shot.code or "": shot for shot in shots}
        self._lit_shots = lit_shots
        self.setWindowTitle(TITLE)
        # As big as suits the work, but no bigger than the screen it opens on,
        # which over ssh and XQuartz may be a laptop's.
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.resize(
            min(1360, int(screen.width() * 0.95)), min(920, int(screen.height() * 0.9))
        )
        self._form, self._result, self._run = None, None, None
        self._dirty = False
        self._form_states = {}
        self._shown = None
        self._banners = []

        self._shot_list = ShotList(
            shots, lit_shots or set(), render, self._render_status_refusal
        )
        self._shot_list.chosen.connect(self._choose_shot)
        self._shot_list.render_status_chosen.connect(self._change_render_status)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self._shot_list)
        splitter.addWidget(self._build_main())
        # Extra width goes to the main area; the shot list never shrinks for it.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        theme.shade(self._shot_list, theme.SHOTS)
        self._shot_list.setMinimumWidth(190)
        splitter.setSizes([210, 1150])
        # The farm as a whole, beside the shot by shot work.
        self._overview = OverviewPage(
            [shot.code or "" for shot in shots], getpass.getuser()
        )
        self._overview.open_job.connect(self._open_farm_job)
        self._doctor = DoctorPage([shot.code or "" for shot in shots])
        self._doctor.open_job.connect(self._open_farm_job)
        self._pages = QtWidgets.QStackedWidget()
        for page in (splitter, self._overview, self._doctor):
            self._pages.addWidget(page)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header(admin))
        layout.addWidget(self._pages, 1)

        # Qt 6 moved QShortcut from QtWidgets to QtGui.
        shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        save = shortcut(QtGui.QKeySequence(QtGui.QKeySequence.Save), self)
        save.activated.connect(self._on_save)
        self._set_busy(None)
        self._show_empty()
        if open_shot:
            self._shot_list.select(open_shot)
            self._choose_shot(open_shot)

    # ---- layout --------------------------------------------------------

    def _build_header(self, admin: bool) -> QtWidgets.QWidget:
        """The bar across the top: the app's name, its pages, and who's using it."""
        bar = QtWidgets.QWidget()
        theme.shade(bar, theme.HEADER)
        logo = QtWidgets.QLabel()
        image = QtGui.QPixmap(str(_LOGO))
        if not image.isNull():
            ratio = self.devicePixelRatioF()
            image = image.scaled(
                int(28 * ratio),
                int(28 * ratio),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            image.setDevicePixelRatio(ratio)
            logo.setPixmap(image)
        name = QtWidgets.QLabel(TITLE)
        name.setStyleSheet("QLabel { font-size: 12pt; font-weight: 700; }")

        nav = QtWidgets.QHBoxLayout()
        nav.setSpacing(4)
        self._nav = QtWidgets.QButtonGroup(self)
        self._nav.setExclusive(True)
        for index, (icon, label, tip) in enumerate(_PAGES):
            page = QtWidgets.QToolButton()
            page.setText(widgets.GAP + label)
            page.setIcon(icons.get(icon))
            page.setIconSize(widgets.ICON_SIZE)
            page.setToolTip(tip)
            page.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            page.setCheckable(True)
            page.setChecked(index == 0)
            page.setProperty("nav", True)
            page.setCursor(QtCore.Qt.PointingHandCursor)
            self._nav.addButton(page, index)
            nav.addWidget(page)
        self._nav.idClicked.connect(self._go)

        user = getpass.getuser()
        who = widgets.Chip(
            f"{user} · admin" if admin else user,
            theme.ACCENT if admin else theme.DIM_TEXT,
        )
        who.setToolTip(
            "You are a pipeline admin, so you may publish any shot's lighting"
            if admin
            else "Signed in as you; you may publish lighting for the shots you light"
        )

        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)
        layout.addWidget(logo)
        layout.addWidget(name)
        layout.addSpacing(18)
        layout.addLayout(nav)
        layout.addStretch(1)
        layout.addWidget(who)
        return bar

    def _go(self, index: int) -> None:
        """Show the header's page `index`."""
        self._pages.setCurrentIndex(index)
        page = self._nav.button(index)
        if page is not None and not page.isChecked():
            page.setChecked(True)

    def _build_main(self) -> QtWidgets.QWidget:
        self._heading = QtWidgets.QLabel()
        self._heading.setStyleSheet("QLabel { font-size: 20pt; font-weight: 700; }")
        self._status_pill = QtWidgets.QToolButton()
        self._status_pill.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._status_pill.setCursor(QtCore.Qt.PointingHandCursor)
        self._status_pill.setStyleSheet(
            "QToolButton { border: 1px solid #454851; border-radius: 11px;"
            " padding: 2px 10px 2px 6px; background-color: #26282d; }"
            "QToolButton:hover { border-color: #6aa1ff; }"
        )
        self._status_pill.clicked.connect(self._status_menu)
        self._status_pill.hide()
        self._unsaved = widgets.Chip("Unsaved changes", theme.WARNING)
        self._unsaved.setToolTip("Save (Ctrl+S) or Send keeps them in the render hip")
        self._unsaved.hide()
        self._hip_icon = widgets.icon_label("file", theme.DIM_TEXT, 14)
        self._hip = QtWidgets.QLabel()
        self._hip.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._hip.setWordWrap(True)
        self._hip.setStyleSheet(f"QLabel {{ color: {theme.DIM_TEXT}; }}")
        self._notes = QtWidgets.QWidget()
        self._notes_layout = QtWidgets.QVBoxLayout(self._notes)
        self._notes_layout.setContentsMargins(_TITLE_INSET, 2, _TITLE_INSET, 2)
        self._notes_layout.setSpacing(6)

        self._publish = widgets.button(
            "Publish Lighting", "upload", clicked=self._on_publish
        )
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setObjectName(theme.PARMS)
        self._scroll.setWidgetResizable(True)
        self._scene_graph = SceneGraphPanel()
        # Publish Lighting sits under the lighting layer it publishes.
        self._departments = DepartmentPanel(self._publish)
        self._departments.hide()
        self._departments.changed.connect(self._edited)
        stage_side = QtWidgets.QWidget()
        theme.shade(stage_side, theme.STAGE)
        stage_layout = QtWidgets.QVBoxLayout(stage_side)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(self._departments)
        stage_layout.addWidget(self._scene_graph, 1)
        parms_and_stage = QtWidgets.QSplitter()
        parms_and_stage.addWidget(self._scroll)
        parms_and_stage.addWidget(stage_side)
        parms_and_stage.setStretchFactor(0, 3)
        parms_and_stage.setStretchFactor(1, 2)
        parms_and_stage.setSizes([640, 420])

        self._reload = widgets.button(
            "Reload",
            "refresh",
            "Open the render hip in Houdini again and reread its parms, layers and "
            "stage, dropping unsaved edits",
            theme.QUIET,
            self._on_reload,
        )
        self._save = widgets.button(
            "Save",
            "save",
            "Save these settings into the shot's render hip (Ctrl+S)",
            clicked=self._on_save,
        )
        self._send = widgets.button(
            "Send to Tractor",
            "send",
            "Save, then render every layer that is on",
            theme.PRIMARY,
            self._on_send,
        )
        actions = QtWidgets.QWidget()
        theme.shade(actions, theme.HEADER)
        buttons = QtWidgets.QHBoxLayout(actions)
        buttons.setContentsMargins(10, 8, 10, 8)
        buttons.addWidget(self._reload)
        buttons.addWidget(self._save)
        buttons.addStretch(1)
        buttons.addWidget(self._send)
        # The buttons act on the parms, so they live on the parms' tab.
        parms_page = QtWidgets.QWidget()
        parms_layout = QtWidgets.QVBoxLayout(parms_page)
        parms_layout.setContentsMargins(0, 0, 0, 0)
        parms_layout.setSpacing(0)
        parms_layout.addWidget(parms_and_stage, 1)
        parms_layout.addWidget(actions)

        self._busy_text = QtWidgets.QLabel()
        self._busy_text.setStyleSheet("QLabel { font-weight: 600; }")
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedWidth(140)
        cancel = widgets.button("Cancel", "x", "Stop Houdini", clicked=self._cancel)
        self._busy = QtWidgets.QFrame()
        self._busy.setObjectName(theme.CARD)
        busy_layout = QtWidgets.QHBoxLayout(self._busy)
        busy_layout.setContentsMargins(12, 6, 8, 6)
        busy_layout.setSpacing(12)
        busy_layout.addWidget(bar)
        busy_layout.addWidget(self._busy_text, 1)
        busy_layout.addWidget(cancel)

        self._log = LogViewer()

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(_TITLE_INSET, 10, _TITLE_INSET, 0)
        title_row.setSpacing(12)
        title_row.addWidget(self._heading)
        title_row.addWidget(self._status_pill, 0, QtCore.Qt.AlignVCenter)
        title_row.addWidget(self._unsaved, 0, QtCore.Qt.AlignVCenter)
        title_row.addStretch(1)
        hip_row = QtWidgets.QHBoxLayout()
        hip_row.setContentsMargins(_TITLE_INSET, 0, _TITLE_INSET, 6)
        hip_row.setSpacing(6)
        hip_row.addWidget(self._hip_icon, 0, QtCore.Qt.AlignTop)
        hip_row.addWidget(self._hip, 1)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)
        top_layout.addLayout(title_row)
        top_layout.addLayout(hip_row)
        top_layout.addWidget(self._notes)
        # The farm and the frames need no Houdini, so they show while it opens.
        self._farm = FarmTab()
        self._viewer = FrameViewer()
        self._farm.view_frames.connect(self._view_frames)
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setIconSize(widgets.ICON_SIZE)
        self._tabs.addTab(parms_page, icons.get("sliders"), "Parameters")
        self._tabs.addTab(self._farm, icons.get("server"), "Farm")
        self._tabs.addTab(self._viewer, icons.get("image"), "Images")
        self._tabs.setDocumentMode(True)
        top_layout.addWidget(self._tabs, 1)
        busy_row = QtWidgets.QHBoxLayout()
        busy_row.setContentsMargins(8, 0, 8, 6)
        busy_row.addWidget(self._busy)
        top_layout.addLayout(busy_row)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self._log)
        split.setStretchFactor(0, 1)
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        return split

    # ---- choosing a shot -----------------------------------------------

    def _choose_shot(self, code: str) -> None:
        if code == self._shown:
            return
        previous = self._shown
        self._after_unsaved(
            lambda: self._open(code), lambda: self._shot_list.select(previous)
        )

    def _open(self, code: str) -> None:
        self._shown = code
        self._forget_form()
        self._heading.setText(code)
        self._show_status_pill(code)
        self._unsaved.hide()
        self._set_hip("")
        self._set_notes([])
        self._scene_graph.show_prims([])
        self._farm.set_shot(code)
        self._viewer.set_shot(code)
        self._start(DESCRIBE, code, None, self._show)

    def _after_unsaved(
        self, proceed: Callable[[], None], stay: Callable[[], None]
    ) -> None:
        """Run `proceed` once unsaved edits are saved or dropped, else `stay`."""
        if not self._dirty or self._result is None:
            proceed()
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(TITLE)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"Save your changes to {self._result.shot} first?")
        box.setStandardButtons(
            QtWidgets.QMessageBox.Save
            | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel
        )
        answer = box.exec_()
        if answer == QtWidgets.QMessageBox.Cancel:
            stay()
        elif answer == QtWidgets.QMessageBox.Discard:
            self._dirty = False
            proceed()
        else:
            shot = self._result.shot

            def saved(result: Result) -> None:
                self._show(result)
                if result.failure is None:
                    proceed()
                else:
                    stay()

            self._start(SAVE, shot, self._values(), saved)

    # ---- actions -------------------------------------------------------

    def _on_reload(self) -> None:
        if self._result is not None:
            shot = self._result.shot
            self._after_unsaved(
                lambda: self._start(DESCRIBE, shot, None, self._show), lambda: None
            )

    def _on_save(self) -> None:
        if self._result is not None and self._run is None:
            self._start(SAVE, self._result.shot, self._values(), self._saved)

    def _on_send(self) -> None:
        if self._result is None or self._form is None or self._run is not None:
            return
        if self._confirm_send():
            self._start(SEND, self._result.shot, self._values(), self._sent)

    def _confirm_send(self) -> bool:
        assert self._result is not None and self._form is not None
        form = self._form
        chosen = [
            layer.name for layer in self._result.layers if form.value(layer.toggle)
        ]
        if not chosen:
            _tell(
                self,
                "Turn on at least one layer to send.",
                QtWidgets.QMessageBox.Information,
            )
            return False
        lines = [
            f"Layers: {', '.join(chosen)}",
            f"Frames: {self._describe_frames()}",
            f"Priority: {form.value('priority')}",
        ]
        answer = QtWidgets.QMessageBox.question(
            self,
            TITLE,
            f"Send {self._result.shot} to Tractor?\n\n"
            + "\n".join(lines)
            + "\n\nThe render hip is saved with these settings first.",
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _describe_frames(self) -> str:
        assert self._form is not None and self._result is not None
        mode = self._form.value("framerange")
        if mode in _FRAME_RANGES:
            return _FRAME_RANGES[mode]
        frames = self._form.values().get("f", [])
        evaluated = self._result.evaluated.get("f", [])
        shown = [
            evaluated[i] if isinstance(v, dict) and i < len(evaluated) else v
            for i, v in enumerate(frames)
        ]
        if len(shown) < 3 or any(isinstance(v, dict) for v in shown):
            return "as set on the hip"
        start, end, step = (int(v) for v in shown)
        by = f" every {step}" if step != 1 else ""
        return f"{start}–{end}{by}"

    def _enable_publish(self, busy: bool) -> None:
        refusal = self._publish_refusal()
        self._publish.setEnabled(not refusal and not busy)
        lighter = self._lit_shots is not None and self._shown in self._lit_shots
        admin_note = "" if lighter or not self._admin else " (as a pipeline admin)"
        self._publish.setToolTip(
            refusal
            or f"Publish {self._shown}'s lighting hip, as last saved, as its next "
            f"lighting version{admin_note}"
        )

    def _publish_refusal(self) -> str:
        """Why Publish Lighting is off for the shown shot, or "" when it's on."""
        shot = self._shown
        if shot is None:
            return "Choose a shot first."
        if self._admin:
            pass
        elif self._lit_shots is None:
            return "ShotGrid couldn't say which shots you light. Reopen the window."
        elif shot not in self._lit_shots:
            return (
                f"Only {shot}'s lighter can publish its lighting: ShotGrid assigns "
                f"its {LIGHTING_STEP} task to someone else."
            )
        if self._result is None or self._result.lighting_target is None:
            return "Waiting for Houdini to read the shot's lighting publishes."
        return ""

    def _render_status_refusal(self, code: str) -> str:
        """Why the artist can't change shot `code`'s Render status, or ""."""
        if self._admin or (self._lit_shots is not None and code in self._lit_shots):
            return ""
        if self._lit_shots is None:
            return "ShotGrid couldn't say which shots you light. Reopen the window."
        return f"Only {code}'s lighter or a pipeline admin can change it."

    def _change_render_status(self, code: str, status: object) -> None:
        current = self._shot_list.render_status(code)
        if not isinstance(status, Status) or current is None:
            return
        if status.code == current.code:
            return
        if refusal := self._render_status_refusal(code):
            _tell(self, refusal, QtWidgets.QMessageBox.Information)
            return
        self._log_line(f"Setting {code}'s {current.task} to {status.name} in ShotGrid…")

        def change() -> RenderStatus:
            from env_sg import DB_Config

            return render_status.change(ShotGrid.connect(DB_Config), current, status)

        def changed(render: RenderStatus) -> None:
            self._shot_list.set_render_status(code, render)
            if code == self._shown:
                self._show_status_pill(code)
            self._log_line(f"{code}'s {render.task} is now {render.name} in ShotGrid.")

        def failed(exc: Exception) -> None:
            _tell(
                self,
                f"ShotGrid didn't change {code}'s {current.task} to {status.name}.",
                QtWidgets.QMessageBox.Warning,
                str(exc),
            )

        background.run(change, changed, failed)

    def _on_publish(self) -> None:
        code = self._shown
        if code is None or self._run is not None or code not in self._shots_by_code:
            return
        hip = shot_folder(code) / LIGHTING_HIP
        if not hip.is_file():
            _tell(
                self,
                f"{code} has no lighting hip at {hip} yet, so there is nothing to publish.",
                QtWidgets.QMessageBox.Information,
            )
            return
        saved = datetime.fromtimestamp(hip.stat().st_mtime).strftime("%b %d at %H:%M")
        notice = (
            f"Publishes {hip} as it was last saved, {saved}. Changes not saved in an "
            "open Houdini session are left out, so save there first."
        )
        known = self._result.lighting_target if self._result else None
        if known is None:
            return
        target = Target(
            entity=self._shots_by_code[code],
            name=known.name,
            current=Path(known.current),
            version=known.version,
            file_name=known.file_name,
            file_code=known.file_code,
            department=known.department,
            final=known.final,
        )
        choice = prompt_publish(self, target, notice, can_playblast=False)
        if choice is None:
            return
        self._start(
            PUBLISH_LIGHTING,
            code,
            None,
            self._published_lighting,
            LightingChoice(
                note=choice.note, announce=choice.announce, final=choice.final
            ),
        )

    def _published_lighting(self, result: Result) -> None:
        if result.failure is not None:
            _tell(
                self,
                result.failure.message,
                QtWidgets.QMessageBox.Critical,
                result.failure.details,
            )
            return
        _tell(
            self,
            "\n".join(result.published) or "Published.",
            QtWidgets.QMessageBox.Information,
        )
        # The render hip reads lighting's publish, so read it again.
        if self._result is not None and self._result.shot == result.shot:
            self._on_reload()
        elif self._shown == result.shot:
            self._open(result.shot)

    def _open_farm_job(self, shot: str, jid: int) -> None:
        """Show a job the Overview or the Farm Doctor chose, on its shot's Farm tab."""
        self._go(0)
        self._shot_list.select(shot)
        self._choose_shot(shot)
        # The artist may have kept unsaved edits to the shot that was open.
        if self._shown == shot:
            self._tabs.setCurrentWidget(self._farm)
            self._farm.show_job(shot, jid)

    def _view_frames(self, layer: str, folder: object, frame: object) -> None:
        self._viewer.show_frames(
            layer,
            folder if isinstance(folder, Path) else None,
            frame if isinstance(frame, int) else None,
        )
        self._tabs.setCurrentWidget(self._viewer)

    def _saved(self, result: Result) -> None:
        self._show(result)
        if result.failure is None:
            self._log_line(f"Saved {result.hip}")

    def _sent(self, result: Result) -> None:
        self._show(result)
        sent = result.sent
        if result.failure is not None or sent is None:
            return
        folders = "\n".join(sent.folders)
        if sent.job_id is None:
            _tell(
                self,
                "Tractor did not confirm the job, but it may still have been queued. "
                f'Look for "{sent.title}" in Tractor before sending again.\n\n'
                f"Its folders were kept:\n{folders}",
                QtWidgets.QMessageBox.Warning,
                sent.unconfirmed,
            )
            return
        self._farm.refresh()
        _tell(
            self,
            f"Job {sent.job_id} renders into:\n{folders}\n\nFollow it on the Farm tab.",
            QtWidgets.QMessageBox.Information,
        )

    # ---- running the worker --------------------------------------------

    def _start(
        self,
        command: str,
        shot: str,
        edits: Edits | None,
        then: Callable[[Result], None],
        choice: LightingChoice | None = None,
    ) -> None:
        if self._run is not None:
            return
        self._log_line(f"— {command} {shot} —")
        run = WorkerRun(command, shot, edits, self, choice)
        run.output.connect(self._log_line)
        run.finished.connect(lambda result: self._finished(result, then))
        self._run = run
        self._set_busy(_BUSY_WORDS[command].format(shot=shot))
        run.start()

    def _finished(self, result: Result, then: Callable[[Result], None]) -> None:
        self._run = None
        self._set_busy(None)
        then(result)

    def _cancel(self) -> None:
        if self._run is not None:
            self._run.cancel()

    def _show(self, result: Result) -> None:
        if result.failure is not None:
            _tell(
                self,
                result.failure.message,
                QtWidgets.QMessageBox.Critical,
                result.failure.details,
            )
            self._log.expand()
            if self._result is None or self._result.shot != result.shot:
                self._show_empty(
                    f"Could not open {result.shot}. See the Log Viewer below."
                )
            return

        self._forget_form()
        self._result = result
        self._dirty = False
        self._heading.setText(result.shot)
        self._unsaved.hide()
        self._set_hip(_hip_text(result))
        self._set_notes(result.notes)

        # The form keeps its open folders in this dict, so a reload reopens them.
        self._form = ParmForm(
            result.schema,
            result.values,
            result.prims,
            result.evaluated,
            self._form_states.setdefault(result.shot, {}),
        )
        self._form.changed.connect(self._edited)
        self._scroll.setWidget(self._form)
        self._scene_graph.show_prims(result.prims)
        self._departments.show_departments(result.departments)
        self._set_busy(None)

    def _edited(self) -> None:
        self._dirty = True
        if self._result is not None:
            self._unsaved.show()

    def _values(self) -> Edits:
        assert self._form is not None
        return Edits(self._form.values(), self._departments.choices())

    def _forget_form(self) -> None:
        if self._form is not None:
            self._scroll.takeWidget()
            self._form.deleteLater()
            self._form = None

    def _show_empty(self, message: str = "Choose a shot on the left.") -> None:
        self._result = None
        self._forget_form()
        self._heading.setText(message)
        self._status_pill.hide()
        self._unsaved.hide()
        self._set_hip("")
        self._set_notes([])
        self._scene_graph.show_prims([])
        self._departments.hide()
        self._set_busy(None)

    def _set_hip(self, text: str) -> None:
        self._hip.setText(text)
        self._hip.setVisible(bool(text))
        self._hip_icon.setVisible(bool(text))

    def _set_notes(self, notes: Sequence[str]) -> None:
        """Show each of the worker's notes as its own banner."""
        for banner in self._banners:
            # Out of the layout now; deleted once the event loop is free.
            banner.hide()
            banner.deleteLater()
        self._banners = []
        for note in notes:
            kind = widgets.INFO if note.startswith(_INFO_NOTES) else widgets.WARNING
            self._banners.append(widgets.Banner(note, kind))
            self._notes_layout.addWidget(self._banners[-1])
        self._notes.setVisible(bool(notes))

    def _show_status_pill(self, code: str) -> None:
        """Show shot `code`'s Render status beside its name; clicking changes it."""
        render = self._shot_list.render_status(code)
        self._status_pill.setIcon(
            status_icons.icon(render.status if render is not None else None)
        )
        self._status_pill.setText(
            widgets.GAP + (render.name if render is not None else "No Render task")
        )
        task = render.task if render is not None else "Render"
        refusal = self._render_status_refusal(code)
        self._status_pill.setToolTip(
            f"{code}'s {task} status in ShotGrid."
            + (f" {refusal}" if refusal else " Click to change it.")
        )
        self._status_pill.setVisible(True)

    def _status_menu(self) -> None:
        if self._shown is None:
            return
        menu = self._shot_list.status_menu(self._shown)
        if menu is not None:
            menu.exec_(
                self._status_pill.mapToGlobal(
                    QtCore.QPoint(0, self._status_pill.height() + 2)
                )
            )

    def _set_busy(self, text: str | None) -> None:
        busy = text is not None
        self._busy.setVisible(busy)
        self._busy_text.setText(text or "")
        ready = not busy and self._result is not None
        for button in (self._reload, self._save, self._send):
            button.setEnabled(ready)
        self._enable_publish(busy)
        self._shot_list.setEnabled(not busy)
        if self._form is not None:
            self._form.setEnabled(not busy)
        self._departments.setEnabled(not busy)

    def _log_line(self, line: str) -> None:
        self._log.append(line)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt's name)
        if self._run is not None:
            answer = QtWidgets.QMessageBox.question(
                self, TITLE, "Houdini is still working. Stop it and close?"
            )
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self._run.cancel()
        # Saving first finishes later, and closes the window then.
        answered = {"now": True, "close": False}

        def close() -> None:
            if answered["now"]:
                answered["close"] = True
            else:
                self.close()

        self._after_unsaved(close, lambda: None)
        answered["now"] = False
        if answered["close"]:
            event.accept()
        else:
            event.ignore()


def _hip_text(result: Result) -> str:
    if result.hip_existed:
        return f"Render hip: {result.hip}"
    seeded = (
        f" Its settings were copied from {result.seeded_from}."
        if result.seeded_from
        else ""
    )
    return f"Render hip: {result.hip} — not saved yet; Save or Send creates it.{seeded}"


def _tell(
    parent: QtWidgets.QWidget,
    message: str,
    icon: QtWidgets.QMessageBox.Icon,
    details: str = "",
) -> None:
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(TITLE)
    box.setIcon(icon)
    box.setText(message)
    if details:
        box.setDetailedText(details)
    box.exec_()

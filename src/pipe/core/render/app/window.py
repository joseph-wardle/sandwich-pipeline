"""The Sandwich Renderer window: choose a shot, edit its render layers, send them."""

from __future__ import annotations

import getpass
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.publish import Target
from pipe.core.render import render_status
from pipe.core.render.app import background, widgets
from pipe.core.render.app.farm import FarmTab
from pipe.core.render.app.images import FrameViewer
from pipe.core.render.app.overview import OverviewPage
from pipe.core.render.app.parms import ParmsTab
from pipe.core.render.app.shots import ShotList
from pipe.core.render.app.versions_page import VersionsTab
from pipe.core.render.app.widgets import TITLE, LogViewer, tell
from pipe.core.render.lighters import LIGHTING_STEP
from pipe.core.render.protocol import LIGHTING_HIP, shot_folder
from pipe.core.render.render_status import RenderBoard, RenderStatus
from pipe.core.shotgrid import Shot, ShotGrid, Status, is_previs_shot_code
from pipe.core.ui import prompt_publish


class RenderNetworkWindow(QtWidgets.QWidget):
    _shot_list: ShotList
    _parms: ParmsTab
    _publish: QtWidgets.QPushButton
    _tabs: QtWidgets.QTabWidget
    _farm: FarmTab
    _overview: OverviewPage
    _pages: QtWidgets.QTabWidget
    _viewer: FrameViewer
    _versions: VersionsTab
    _log: LogViewer

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
        self.resize(1280, 900)

        self._shot_list = ShotList(
            shots,
            lit_shots or set(),
            render,
            lambda code: self._lighter_refusal(code, "change it"),
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
        widgets.shade(self._shot_list, widgets.SHOTS)
        splitter.setSizes([200, 1080])
        # The farm as a whole, beside the shot by shot work.
        self._overview = OverviewPage(getpass.getuser())
        self._overview.open_job.connect(self._open_farm_job)
        self._pages = QtWidgets.QTabWidget()
        self._pages.addTab(splitter, "Shots")
        self._pages.addTab(self._overview, "Tractor Overview")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._pages)

        # Qt 6 moved QShortcut from QtWidgets to QtGui.
        shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        save = shortcut(QtGui.QKeySequence(QtGui.QKeySequence.Save), self)
        save.activated.connect(self._parms.save)
        self._refresh()
        if open_shot:
            self._shot_list.select(open_shot)
            self._choose_shot(open_shot)

    def _build_main(self) -> QtWidgets.QWidget:
        self._log = LogViewer()
        self._publish = QtWidgets.QPushButton("Publish Lighting")
        self._publish.clicked.connect(self._on_publish)
        # Publish Lighting sits under the lighting layer it publishes.
        self._parms = ParmsTab(self._log, self._publish)
        self._parms.changed.connect(self._refresh)
        # The farm and the frames need no Houdini, so they show while it opens.
        self._farm = FarmTab()
        self._parms.sent.connect(self._farm.refresh)
        self._viewer = FrameViewer()
        self._versions = VersionsTab(
            sorted(c for c in self._lit_shots or () if not is_previs_shot_code(c))
        )
        self._versions.deleted.connect(self._viewer.reread)
        self._farm.view_frames.connect(self._view_frames)
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.addTab(self._parms, "Parameters")
        self._tabs.addTab(self._farm, "Farm")
        self._tabs.addTab(self._viewer, "Images")
        self._tabs.addTab(self._versions, "Versions")

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self._parms.header)
        top_layout.addWidget(self._tabs, 1)
        top_layout.addWidget(self._parms.busy_bar)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self._log)
        split.setStretchFactor(0, 1)
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        return split

    def _choose_shot(self, code: str) -> None:
        if code == self._parms.shown:
            return
        previous = self._parms.shown
        self._parms.after_unsaved(
            lambda: self._open(code), lambda: self._shot_list.select(previous)
        )

    def _open(self, code: str) -> None:
        self._farm.set_shot(code)
        self._viewer.set_shot(code)
        self._versions.set_shot(code, self._lighter_refusal(code, "delete its renders"))
        self._parms.open(code)

    def _refresh(self) -> None:
        busy = self._parms.working
        self._shot_list.setEnabled(not busy)
        refusal = self._publish_refusal()
        self._publish.setEnabled(not refusal and not busy)
        shown = self._parms.shown
        lighter = self._lit_shots is not None and shown in self._lit_shots
        admin_note = "" if lighter or not self._admin else " (as a pipeline admin)"
        self._publish.setToolTip(
            refusal
            or f"Publish {shown}'s lighting hip, as last saved, as its next "
            f"lighting version{admin_note}"
        )

    def _publish_refusal(self) -> str:
        """Why Publish Lighting is off for the shown shot, or "" when it's on."""
        shot = self._parms.shown
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
        if self._parms.lighting_target is None:
            return "Waiting for Houdini to read the shot's lighting publishes."
        return ""

    def _lighter_refusal(self, code: str, action: str) -> str:
        """Why the artist can't `action` in shot `code`, or "" when they light it or are an admin."""
        if self._admin or (self._lit_shots is not None and code in self._lit_shots):
            return ""
        if self._lit_shots is None:
            return "ShotGrid couldn't say which shots you light. Reopen the window."
        return f"Only {code}'s lighter or a pipeline admin can {action}."

    def _change_render_status(self, code: str, status: object) -> None:
        current = self._shot_list.render_status(code)
        if not isinstance(status, Status) or current is None:
            return
        if refusal := self._lighter_refusal(code, "change it"):
            tell(self, refusal, QtWidgets.QMessageBox.Information)
            return
        self._log.append(
            f"Setting {code}'s {current.task} to {status.name} in ShotGrid…"
        )

        def change() -> RenderStatus:
            from env_sg import DB_Config

            return render_status.change(ShotGrid.connect(DB_Config), current, status)

        def changed(render: RenderStatus) -> None:
            self._shot_list.set_render_status(code, render)
            self._log.append(
                f"{code}'s {render.task} is now {render.name} in ShotGrid."
            )

        def failed(exc: Exception) -> None:
            tell(
                self,
                f"ShotGrid didn't change {code}'s {current.task} to {status.name}.",
                QtWidgets.QMessageBox.Warning,
                str(exc),
            )

        background.run(change, changed, failed)

    def _on_publish(self) -> None:
        code = self._parms.shown
        known = self._parms.lighting_target
        if code is None or self._parms.working or code not in self._shots_by_code:
            return
        hip = shot_folder(code) / LIGHTING_HIP
        if not hip.is_file():
            tell(
                self,
                f"{code} has no lighting hip at {hip} yet, so there is nothing to publish.",
                QtWidgets.QMessageBox.Information,
            )
            return
        if known is None:
            return
        saved = datetime.fromtimestamp(hip.stat().st_mtime).strftime("%b %d at %H:%M")
        notice = (
            f"Publishes {hip} as it was last saved, {saved}. Changes not saved in an "
            "open Houdini session are left out, so save there first."
        )
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
        if choice is not None:
            self._parms.publish_lighting(choice)

    def _open_farm_job(self, shot: str, jid: int) -> None:
        """Show a job the Overview chose, on its shot's Farm tab."""
        self._pages.setCurrentIndex(0)
        self._shot_list.select(shot)
        self._choose_shot(shot)
        # The artist may have kept unsaved edits to the shot that was open.
        if self._parms.shown == shot:
            self._tabs.setCurrentWidget(self._farm)
            self._farm.show_job(shot, jid)

    def _view_frames(self, layer: str, folder: object, frame: object) -> None:
        self._viewer.show_frames(
            layer,
            folder if isinstance(folder, Path) else None,
            frame if isinstance(frame, int) else None,
        )
        self._tabs.setCurrentWidget(self._viewer)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt's name)
        if self._parms.working:
            answer = QtWidgets.QMessageBox.question(
                self, TITLE, "Houdini is still working. Stop it and close?"
            )
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self._parms.stop()
        # Saving first finishes later, and closes the window then.
        answered = {"now": True, "close": False}

        def close() -> None:
            if answered["now"]:
                answered["close"] = True
            else:
                self.close()

        self._parms.after_unsaved(close, lambda: None)
        answered["now"] = False
        if answered["close"]:
            self._parms.stop()
            event.accept()
        else:
            event.ignore()

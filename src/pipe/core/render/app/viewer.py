"""The Images tab: a shot's rendered frames, one AOV or LPE at a time, through
the show's OCIO display transform (OpenDRT by default), without Nuke.

Contact Sheet shows every AOV and LPE of the frame at once instead, with a red
X on any that rendered nothing or broke.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

import numpy as np
from Qt import QtCore, QtGui, QtWidgets

from pipe.core.color import DEFAULT_VIEW
from pipe.core.render import versions
from pipe.core.render import frames
from pipe.core.render.app import background
from pipe.core.render.app.canvas import ImageCanvas
from pipe.core.render.app.contact_sheet import THUMB_WIDTH, ContactSheet
from pipe.core.render.app.playback import DEFAULT_FPS, Look, Player
from pipe.core.render.frames import ChannelGroup, Product, Thumbnail

_DATA_ROLE = QtCore.Qt.UserRole
# Frames of float pixels kept, so scrubbing back is instant: about 25 MB each.
_CACHED_FRAMES = 8
# Wait for the slider to settle before reading a 100+ MB frame.
_SCRUB_DELAY_MS = 120

log = logging.getLogger(__name__)


class FrameViewer(QtWidgets.QWidget):
    _layer: QtWidgets.QComboBox
    _version: QtWidgets.QComboBox
    _product: QtWidgets.QComboBox
    _group: QtWidgets.QComboBox
    _view: QtWidgets.QComboBox
    _exposure: QtWidgets.QDoubleSpinBox
    _slider: QtWidgets.QSlider
    _frame: QtWidgets.QSpinBox
    _canvas: ImageCanvas
    _sheet: ContactSheet
    _sheet_button: QtWidgets.QToolButton
    _stack: QtWidgets.QStackedWidget
    _player: Player
    _play: QtWidgets.QToolButton
    _fps: QtWidgets.QDoubleSpinBox
    _zoom: QtWidgets.QLabel
    _status: QtWidgets.QLabel
    _readout: QtWidgets.QLabel

    _shot: str | None
    _frames: list[int]
    _cache: OrderedDict[tuple[Path, str], np.ndarray]
    # What the image shows, in scene linear, for the pixel readout.
    _pixels: np.ndarray | None
    _wanted: tuple[Path, str] | None
    _scrub: QtCore.QTimer
    # The frame to show once the version loads, when another tab asked for one.
    _pending_frame: int | None
    # The contact sheet's frame and its thumbnails, in scene linear.
    _thumbs: tuple[Path, list[Thumbnail]] | None
    _wanted_sheet: Path | None

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._shot, self._frames, self._pixels, self._wanted = None, [], None, None
        self._pending_frame = None
        self._thumbs, self._wanted_sheet = None, None
        self._cache = OrderedDict()

        self._layer = _combo("The render layer")
        self._version = _combo("Newest first. 'rendering' frames are still in tmp/.")
        self._product = _combo("The render product: a sequence of multi-channel EXRs")
        self._group = _combo("The AOV or LPE to show. Data passes show as Raw.")
        self._view = _combo("The show's OCIO view on sRGB - Display")
        self._view.addItems(_safe_views())
        self._exposure = QtWidgets.QDoubleSpinBox()
        self._exposure.setRange(-12.0, 12.0)
        self._exposure.setSingleStep(0.5)
        self._exposure.setDecimals(1)
        self._exposure.setSuffix(" stops")
        self._exposure.setToolTip("Exposure in scene linear, before the view")

        self._layer.currentIndexChanged.connect(self._layer_changed)
        self._version.currentIndexChanged.connect(self._version_changed)
        self._product.currentIndexChanged.connect(self._product_changed)
        self._group.currentIndexChanged.connect(self._group_changed)
        self._view.currentIndexChanged.connect(lambda _: self._look_changed())
        self._exposure.valueChanged.connect(lambda _: self._look_changed())

        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._frame = QtWidgets.QSpinBox()
        self._frame.setKeyboardTracking(False)
        self._slider.valueChanged.connect(self._slid)
        self._frame.valueChanged.connect(self._typed_frame)
        self._scrub = QtCore.QTimer(self)
        self._scrub.setSingleShot(True)
        self._scrub.setInterval(_SCRUB_DELAY_MS)
        self._scrub.timeout.connect(self._load)

        self._canvas = ImageCanvas()
        self._canvas.hovered.connect(self._hovered)
        self._sheet = ContactSheet()
        self._sheet.chosen.connect(self._sheet_chosen)
        self._stack = QtWidgets.QStackedWidget()
        self._stack.addWidget(self._canvas)
        self._stack.addWidget(self._sheet)
        self._sheet_button = QtWidgets.QToolButton()
        self._sheet_button.setText("Contact Sheet")
        self._sheet_button.setCheckable(True)
        self._sheet_button.setToolTip(
            "Every AOV and LPE of this frame side by side; a red X marks one "
            "that rendered nothing or broke"
        )
        self._sheet_button.toggled.connect(self._sheet_toggled)
        self._zoom = QtWidgets.QLabel()
        self._zoom.setToolTip(self._canvas.toolTip())
        self._canvas.zoomed.connect(lambda scale: self._zoom.setText(f"{scale:.0%}"))
        fit = QtWidgets.QToolButton()
        fit.setText("Fit")
        fit.setToolTip("Fit the image to the view (F)")
        fit.clicked.connect(self._canvas.fit)
        actual = QtWidgets.QToolButton()
        actual.setText("100%")
        actual.setToolTip("One image pixel per screen pixel")
        actual.clicked.connect(self._canvas.actual_size)
        self._status = _ElidedLabel()
        self._readout = QtWidgets.QLabel()
        self._readout.setFont(
            QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        )
        # As wide as the widest reading, so moving the mouse never resizes the
        # tab, and with it the shot list beside it.
        self._readout.setFixedWidth(
            self._readout.fontMetrics().horizontalAdvance(
                _readout_text(99999, 99999, (-0.00001,) * 4)
            )
            + 8
        )

        pick = QtWidgets.QHBoxLayout()
        for label, widget in (
            ("Layer", self._layer),
            ("Version", self._version),
            ("Product", self._product),
            ("AOV / LPE", self._group),
            ("View", self._view),
        ):
            pick.addWidget(QtWidgets.QLabel(label))
            pick.addWidget(widget, 1)
        pick.addWidget(self._exposure)
        pick.addWidget(self._sheet_button)
        self._player = Player(self)
        self._player.shown.connect(self._played)
        self._player.status.connect(self._status.setText)
        self._play = QtWidgets.QToolButton()
        self._play.setToolTip("Play or pause (Space). ← and → step a frame.")
        self._play.clicked.connect(self.toggle_play)
        self._player.playing.connect(self._show_playing)
        self._show_playing(False)
        self._fps = QtWidgets.QDoubleSpinBox()
        self._fps.setRange(1.0, 60.0)
        self._fps.setDecimals(2)
        self._fps.setValue(DEFAULT_FPS)
        self._fps.setSuffix(" fps")
        self._fps.setToolTip("Playback rate")
        self._fps.valueChanged.connect(self._player.set_fps)
        for keys, action in (
            ("Space", self.toggle_play),
            ("Left", lambda: self._step(-1)),
            ("Right", lambda: self._step(1)),
        ):
            shortcut = _shortcut(QtGui.QKeySequence(keys), self)
            # Anywhere in the Images tab, and nowhere else in the window.
            shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        scrub = QtWidgets.QHBoxLayout()
        scrub.addWidget(self._play)
        scrub.addWidget(QtWidgets.QLabel("Frame"))
        scrub.addWidget(self._slider, 1)
        scrub.addWidget(self._frame)
        scrub.addWidget(self._fps)
        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(self._status, 1)
        footer.addWidget(self._readout)
        footer.addWidget(self._zoom)
        footer.addWidget(fit)
        footer.addWidget(actual)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(pick)
        layout.addWidget(self._stack, 1)
        layout.addLayout(scrub)
        layout.addLayout(footer)
        self._clear("Choose a shot.")

    # ---- what to show --------------------------------------------------

    def set_shot(self, shot_code: str) -> None:
        if shot_code == self._shot:
            return
        self._shot = shot_code
        self._cache.clear()
        _refill(self._layer, [(p.name, p) for p in frames.layers(shot_code)])
        if self._layer.count() == 0:
            self._clear(f"{shot_code} has no renders yet.")

    def show_frames(self, layer: str, folder: Path | None, frame: int | None) -> None:
        """Show `layer`'s `folder`, or its newest version, at `frame`."""
        index = self._layer.findText(layer)
        if index < 0:
            self._status.setText(
                f"{self._shot} has no render layer {layer} on disk yet."
            )
            return
        self._pending_frame = frame
        self._layer.setCurrentIndex(index)
        self._layer_changed()
        if folder is not None and (found := self._version.findData(folder)) >= 0:
            self._version.setCurrentIndex(found)

    def _layer_changed(self, *_: object) -> None:
        layer = self._layer.currentData()
        found = versions(layer) if layer is not None else []
        _refill(self._version, [(_version_label(v), v) for v in found])

    def _version_changed(self, *_: object) -> None:
        version = self._version.currentData()
        found = frames.products(version) if version is not None else []
        _refill(
            self._product,
            [(f"{p.name}  ({p.summary})" if p.summary else p.name, p) for p in found],
        )
        if not found:
            self._clear("This version has no frames yet.")

    def _product_changed(self, *_: object) -> None:
        product: Product | None = self._product.currentData()
        self._player.pause()
        if product is None:
            return
        self._frames = list(product.frames)
        frame = self._pending_frame if self._pending_frame in product.frames else None
        self._pending_frame = None
        for widget in (self._slider, self._frame):
            widget.blockSignals(True)
            widget.setRange(self._frames[0], self._frames[-1])
            widget.setValue(frame if frame is not None else self._frames[0])
            widget.blockSignals(False)
        first = product.frames[self._frames[0]]
        background.run(
            lambda: frames.channel_groups(first),
            self._groups_read,
            lambda exc: self._failed(first, exc),
        )

    def _groups_read(self, groups: list[ChannelGroup]) -> None:
        previous = self._group.currentText()
        _refill(self._group, [(g.name, g) for g in groups])
        # Keep the AOV the artist was looking at, across versions and layers.
        if (index := self._group.findText(previous)) >= 0:
            self._group.setCurrentIndex(index)

    def _group_changed(self, *_: object) -> None:
        group: ChannelGroup | None = self._group.currentData()
        if group is None:
            return
        view = frames.default_view(group)
        if group.is_data or self._view.currentText() == frames.RAW_VIEW:
            self._view.blockSignals(True)
            self._view.setCurrentText(view)
            self._view.blockSignals(False)
        if self._player.is_playing():
            self._sync_player()
        else:
            self._load()

    # ---- playing -------------------------------------------------------

    def toggle_play(self) -> None:
        if self._sheet_button.isChecked():
            return
        if self._player.is_playing():
            self._player.pause()
            # The full-precision frame, for the pixel readout.
            self._load()
            return
        if not self._sync_player():
            return
        # Playing shows display images; their values aren't scene linear.
        self._pixels = None
        self._readout.setText("")
        self._player.play(self._frame.value())

    def _sync_player(self) -> bool:
        """Hand the player the product and look on screen; False when there is none."""
        product: Product | None = self._product.currentData()
        group: ChannelGroup | None = self._group.currentData()
        if product is None or group is None:
            return False
        self._player.set_frames(product.frames)
        self._player.set_look(
            Look(group, self._view.currentText(), self._exposure.value())
        )
        return True

    def _look_changed(self) -> None:
        if self._sheet_button.isChecked():
            self._draw_sheet()
        elif self._player.is_playing():
            self._sync_player()
        else:
            self._redraw()

    def _played(self, frame: int, image: QtGui.QImage) -> None:
        for widget in (self._slider, self._frame):
            widget.blockSignals(True)
            widget.setValue(frame)
            widget.blockSignals(False)
        self._canvas.set_image(image)

    def _show_playing(self, playing: bool) -> None:
        self._play.setText("⏸" if playing else "▶")

    def _step(self, by: int) -> None:
        """To the next or previous rendered frame, as Nuke's arrow keys do."""
        self._player.pause()
        if not self._frames:
            return
        current = self._frame.value()
        later = [f for f in self._frames if f > current]
        earlier = [f for f in self._frames if f < current]
        target = (
            (later[0] if later else current)
            if by > 0
            else (earlier[-1] if earlier else current)
        )
        self._frame.setValue(target)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 (Qt's name)
        # Nothing to watch on another tab.
        self._player.pause()
        super().hideEvent(event)

    # ---- frames --------------------------------------------------------

    def _slid(self, frame: int) -> None:
        # Grabbing the playhead stops playback, as in Nuke.
        self._player.pause()
        self._frame.blockSignals(True)
        self._frame.setValue(frame)
        self._frame.blockSignals(False)
        self._scrub.start()

    def _typed_frame(self, frame: int) -> None:
        self._player.pause()
        self._slider.blockSignals(True)
        self._slider.setValue(frame)
        self._slider.blockSignals(False)
        self._load()

    def _frame_path(self) -> Path | None:
        product: Product | None = self._product.currentData()
        if product is None:
            return None
        # A frame missing mid-range, as in a job still rendering, shows the one before it.
        frame = max((f for f in self._frames if f <= self._frame.value()), default=None)
        return product.frames[frame] if frame is not None else None

    def _load(self) -> None:
        if self._sheet_button.isChecked():
            self._load_sheet()
            return
        group: ChannelGroup | None = self._group.currentData()
        path = self._frame_path()
        if group is None or path is None:
            return
        key = (path, group.name)
        self._wanted = key
        if key in self._cache:
            self._cache.move_to_end(key)
            self._shown(key, self._cache[key])
            return
        self._status.setText(f"Reading {path.name}…")
        background.run(
            lambda: frames.read(path, group),
            lambda pixels: self._read(key, pixels),
            lambda exc: self._failed(path, exc),
        )

    def _read(self, key: tuple[Path, str], pixels: np.ndarray) -> None:
        self._cache[key] = pixels
        while len(self._cache) > _CACHED_FRAMES:
            self._cache.popitem(last=False)
        # The artist may have scrubbed on while it read.
        if key == self._wanted:
            self._shown(key, pixels)

    def _shown(self, key: tuple[Path, str], pixels: np.ndarray) -> None:
        self._pixels = pixels
        product: Product | None = self._product.currentData()
        parts = [str(key[0]), key[1]]
        if product is not None:
            frame = next(
                (f for f, path in product.frames.items() if path == key[0]), None
            )
            if (kind := product.kinds.get(frame)) is not None:
                parts.append(kind)
            if product.rendering:
                parts.append("still rendering")
        self._status.setText("  —  ".join(parts))
        self._redraw()

    def _redraw(self) -> None:
        if self._pixels is None:
            return
        shown = frames.display(
            self._pixels, self._view.currentText(), self._exposure.value()
        )
        height, width, _ = shown.shape
        image = QtGui.QImage(
            shown.tobytes(), width, height, 3 * width, QtGui.QImage.Format_RGB888
        )
        # QImage borrows the bytes; copy so it outlives them.
        self._canvas.set_image(image.copy())

    def _hovered(self, x: int, y: int) -> None:
        if self._pixels is None or not (
            0 <= y < self._pixels.shape[0] and 0 <= x < self._pixels.shape[1]
        ):
            self._readout.setText("")
            return
        values = tuple(float(v) for v in self._pixels[y, x])
        self._readout.setText(_readout_text(x, y, values))

    # ---- contact sheet -------------------------------------------------

    def _sheet_toggled(self, on: bool) -> None:
        self._player.pause()
        self._play.setEnabled(not on)
        self._stack.setCurrentWidget(self._sheet if on else self._canvas)
        if on:
            self._readout.setText("")
        self._load()

    def _sheet_chosen(self, name: str) -> None:
        index = self._group.findText(name)
        self._sheet_button.setChecked(False)
        if index >= 0:
            self._group.setCurrentIndex(index)

    def _groups(self) -> list[ChannelGroup]:
        return [self._group.itemData(i) for i in range(self._group.count())]

    def _load_sheet(self) -> None:
        path = self._frame_path()
        groups = self._groups()
        if path is None or not groups:
            self._sheet.show_message("No frames to show.")
            return
        if self._thumbs is not None and self._thumbs[0] == path:
            self._draw_sheet()
            return
        self._wanted_sheet = path
        self._sheet.show_message(f"Reading every AOV and LPE of {path.name}…")
        self._status.setText(f"Reading {path.name}…")
        background.run(
            lambda: frames.thumbnails(path, groups, THUMB_WIDTH),
            lambda thumbs: self._sheet_read(path, thumbs),
            lambda exc: self._sheet_failed(path, exc),
        )

    def _sheet_read(self, path: Path, thumbs: list[Thumbnail]) -> None:
        if path != self._wanted_sheet:
            return
        self._thumbs = (path, thumbs)
        broken = [t.group.name for t in thumbs if t.problem]
        self._status.setText(
            f"{path}  —  {len(thumbs)} AOVs and LPEs"
            + (
                f", {len(broken)} empty or broken: {', '.join(broken)}"
                if broken
                else ""
            )
        )
        self._draw_sheet()

    def _sheet_failed(self, path: Path, exc: Exception) -> None:
        self._failed(path, exc)
        self._sheet.show_message(f"Could not read {path.name}: {exc}")

    def _draw_sheet(self) -> None:
        if self._thumbs is None or not self._sheet_button.isChecked():
            return
        view, exposure = self._view.currentText(), self._exposure.value()

        def to_screen(thumb: Thumbnail) -> np.ndarray:
            assert thumb.pixels is not None
            # Data passes as they are, as when shown on their own.
            shown = frames.RAW_VIEW if thumb.group.is_data else view
            return frames.display(thumb.pixels, shown, exposure)

        self._sheet.show_thumbnails(
            self._thumbs[1], to_screen, self._group.currentText()
        )

    def _failed(self, path: Path, exc: Exception) -> None:
        log.warning("Could not read %s", path, exc_info=exc)
        self._status.setText(f"Could not read {path.name}: {exc}")

    def _clear(self, message: str) -> None:
        self._thumbs = None
        self._sheet.show_message(message)
        self._player.clear()
        self._pixels = None
        self._canvas.clear()
        self._status.setText(message)
        self._readout.setText("")


class _ElidedLabel(QtWidgets.QLabel):
    """Shortens its text with "…" to fit, rather than widening its parent;
    the whole text is in its tooltip."""

    _full: str

    def __init__(self) -> None:
        super().__init__()
        self._full = ""
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred
        )

    # Qt names the parameter arg__1, which an override must match.
    def setText(self, arg__1: str) -> None:  # noqa: N802 (Qt's name)
        self._full = arg__1
        self.setToolTip(arg__1)
        self._elide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 (Qt's name)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        elided = self.fontMetrics().elidedText(
            self._full, QtCore.Qt.ElideMiddle, max(self.width(), 1)
        )
        super().setText(elided)


def _readout_text(x: int, y: int, values: tuple[float, ...]) -> str:
    """The pixel's scene-linear red, green, blue and alpha; "—" for no alpha."""
    shown = [f"{v:8.4f}" if v == v else "       —" for v in values]  # v != v: NaN
    return f"{x:4d},{y:4d}  R{shown[0]} G{shown[1]} B{shown[2]} A{shown[3]}"


def _combo(tip: str) -> QtWidgets.QComboBox:
    combo = QtWidgets.QComboBox()
    combo.setToolTip(tip)
    # A fixed width: one sized to its items would resize the tab with each shot.
    combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(10)
    return combo


def _refill(combo: QtWidgets.QComboBox, items: list[tuple[str, object]]) -> None:
    """Replace the items, then announce the first once, as a fresh choice."""
    combo.blockSignals(True)
    combo.clear()
    for text, data in items:
        combo.addItem(text, data)
    combo.blockSignals(False)
    combo.currentIndexChanged.emit(combo.currentIndex())


def _version_label(version: Path) -> str:
    found = frames.products(version)
    if any(p.rendering for p in found):
        return f"{version.name}  (rendering)"
    if not found:
        return f"{version.name}  (no frames)"
    return version.name


def _safe_views() -> list[str]:
    try:
        return frames.views()
    except Exception:
        log.exception("Could not read the OCIO config's views")
        return [DEFAULT_VIEW]


# Qt 6 moved QShortcut from QtWidgets to QtGui.
_shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut

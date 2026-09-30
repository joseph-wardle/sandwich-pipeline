from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import typing

    RT = typing.TypeVar("RT")  # return type

from pipe.core import telemetry
from pipe.core.util import silent_startupinfo
from pipe.dcc.substance_painter.util.progress import (
    PublishProgressCallback,
    PublishProgressUpdate,
    PublishStage,
)
from env import Executables

log = logging.getLogger(__name__)

_COLOR_SOURCE_COLORSPACE = "sRGB - Texture"
_RENDERING_COLORSPACE = "ACEScg"


def _process_qt_events() -> None:
    """Flush pending Qt events so progress dialogs can repaint.

    Safe to call when no QApplication exists (headless / batch mode).
    """
    try:
        from Qt import QtWidgets

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


class TexConversionError(ChildProcessError):
    """Raised when one of the tex conversion subprocesses fails."""

    error_code = "TEXTURE_CONVERSION_FAILED"


class TexConverter:
    tex_path: Path
    imgs_by_tex_set: list[list[str]]
    asset_name: str | None
    geo_variant: str | None
    material_variant: str | None
    renderman_variant: str | None
    batch_size: int

    def __init__(
        self,
        tex_path: Path,
        imgs_by_tex_set: typing.Iterable[list[str]],
        *,
        asset_name: str | None = None,
        geo_variant: str | None = None,
        material_variant: str | None = None,
        renderman_variant: str | None = None,
        batch_size: int = 18,
        progress_callback: PublishProgressCallback | None = None,
    ) -> None:
        self.tex_path = tex_path
        self.imgs_by_tex_set = [list(imgs) for imgs in imgs_by_tex_set]
        self.asset_name = asset_name
        self.geo_variant = geo_variant
        self.material_variant = material_variant
        self.renderman_variant = renderman_variant
        self.batch_size = max(1, int(batch_size))
        self.progress_callback = progress_callback

    def _source_count(self) -> int:
        return sum(len(imgs) for imgs in self.imgs_by_tex_set)

    def convert_all(self) -> list[Path]:
        payload: dict[str, object] = {
            "source_count": self._source_count(),
            "converted_tex_count": 0,
            "batch_size": self.batch_size,
        }
        if self.geo_variant:
            payload["geo_variant"] = str(self.geo_variant)
        if self.material_variant:
            payload["material_variant"] = str(self.material_variant)
        if self.renderman_variant:
            payload["renderman_variant"] = str(self.renderman_variant)

        converted_tex: list[Path] = []
        with telemetry.record(
            telemetry.EVENT_TEXTURE_CONVERT_TEX,
            payload=payload,
            asset=self.asset_name,
        ) as telemetry_event:
            try:
                converted_tex = self.convert_tex()
            finally:
                telemetry_event.update(converted_tex_count=len(converted_tex))

        return converted_tex

    def convert_tex(self) -> list[Path]:
        """Convert all .png textures in the most recent export to .tex"""

        assert self.tex_path is not None

        # Remove any corrupted tex files from a previous export
        for file in self.tex_path.iterdir():
            if file.name.endswith(".temp.tex"):
                file.unlink()

        @self._debug_out
        def tex_cmd(img: str, is_color: bool) -> list[str]:
            # fmt: off
            return [
                str(Executables.rman_oiiotool),
                img,
                *(
                    [
                        "--colorconvert",
                        _COLOR_SOURCE_COLORSPACE, _RENDERING_COLORSPACE,
                        "-d", "half",
                    ] if is_color else []
                ),
                "--compression", "zip" if is_color else "lossless",
                "--planarconfig", "separate",
                "-otex:fileformatname=tx:wrap=clamp:resize=1:prman_options=1",
                f"{str(self.tex_path / Path(img).stem)}.tex",
            ]
            # fmt: on

        cmdlines: list[list[str]] = []
        for imgs in self.imgs_by_tex_set:
            log.debug(imgs)
            for img in imgs:
                log.debug(f"        {img}")
                cmdlines.append(
                    tex_cmd(img, is_color=("Color" in img or "Emissive" in img))
                )

        total_tex = len(cmdlines)
        if total_tex <= 0:
            self._report_progress(
                PublishStage.CONVERTING_TEX,
                "No TEX conversions were required for this publish.",
                current=1,
                total=1,
            )
            return []

        self._report_progress(
            PublishStage.CONVERTING_TEX,
            f"Converting source textures to TEX ({total_tex} file(s)).",
            current=0,
            total=total_tex,
        )

        finished_imgs = self._wait_and_check_cmds(
            cmdlines,
            batch_size=self.batch_size,
            stage=PublishStage.CONVERTING_TEX,
            message="Converting source textures to TEX.",
        )

        if len(finished_imgs) != len(cmdlines):
            raise TexConversionError("Not all png textures were converted")

        return finished_imgs

    def _report_progress(
        self,
        stage: PublishStage,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            PublishProgressUpdate(
                stage=stage,
                message=message,
                current=current,
                total=total,
            )
        )

    def _wait_and_check_cmds(
        self,
        cmds: typing.Sequence[list[str]],
        batch_size: int = 18,
        stage: PublishStage | None = None,
        message: str | None = None,
    ) -> list[Path]:
        """Wait for list of processes to finish and print them to the debug log"""

        batched_cmds = (
            cmds[i : i + batch_size] for i in range(0, len(cmds), batch_size)
        )

        finished_imgs: list[Path] = []
        total_cmds = len(cmds)

        while batch := next(batched_cmds, None):
            start_time = time.time()

            procs = [
                subprocess.Popen(
                    cmd,
                    env=os.environ,
                    startupinfo=silent_startupinfo(),
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                for cmd in batch
            ]

            for p in procs:
                p.wait()
                if log.isEnabledFor(logging.DEBUG):
                    if p.stdout and (stdout := p.stdout.read().decode("utf-8")):
                        log.debug(stdout)
                    if p.stderr and (stderr := p.stderr.read().decode("utf-8")):
                        log.debug(stderr)

                _process_qt_events()

                img = Path(cast(str, p.args[-1]))  # type: ignore

                # check file has been touched recently
                if start_time < img.stat().st_mtime:
                    log.debug(f"Successfully converted {img}")
                    finished_imgs.append(img)
                    if stage is not None and message is not None:
                        self._report_progress(
                            stage,
                            message,
                            current=len(finished_imgs),
                            total=total_cmds,
                        )

        return finished_imgs

    def _debug_out(self, func: typing.Callable[..., RT]) -> typing.Callable[..., RT]:
        """Decorator to debug print the output of the function"""

        def inner(self: TexConverter, *args, **kwargs) -> RT:
            ret = func(self, *args, **kwargs)
            log.debug(ret)
            return ret

        return inner

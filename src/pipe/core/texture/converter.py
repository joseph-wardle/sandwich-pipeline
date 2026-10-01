from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typing

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
_DATA_COLORSPACE = "Raw"
_RENDERING_COLORSPACE = "ACEScg"
_UDIM_SUFFIX = re.compile(r"\.\d{4}$")
_MAX_REPORTED_FAILURES = 3


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


def _failure_reason(returncode: int, stderr: str) -> str:
    """oiiotool's own error line, or its exit code when it printed nothing."""
    if returncode == 0:
        return "the converter reported success but wrote no file"
    return stderr.strip().partition("\n")[0] or f"exit code {returncode}"


def _failure_summary(failures: list[str]) -> str:
    shown = failures[:_MAX_REPORTED_FAILURES]
    lines = [f"{len(failures)} texture(s) could not be converted to TEX:", *shown]
    if len(failures) > len(shown):
        lines.append(f"... and {len(failures) - len(shown)} more, listed in the log.")
    return "\n".join(lines)


def _is_color_map(img: str) -> bool:
    """Whether a Painter export needs colour conversion, judged by its map name."""
    name = _UDIM_SUFFIX.sub("", Path(img).stem)
    if name.endswith(f"_{_DATA_COLORSPACE}"):
        return False
    map_name = name.removesuffix(f"_{_COLOR_SOURCE_COLORSPACE}").rpartition("_")[2]
    return "Color" in map_name or "Emissive" in map_name


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

        # Remove any corrupted tex files from a previous export
        for file in self.tex_path.iterdir():
            if file.name.endswith(".temp.tex"):
                file.unlink()

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
                cmd = tex_cmd(img, is_color=_is_color_map(img))
                log.debug(cmd)
                cmdlines.append(cmd)

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

        return self._wait_and_check_cmds(cmdlines)

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

    def _wait_and_check_cmds(self, cmds: typing.Sequence[list[str]]) -> list[Path]:
        """Run the conversions in batches; raise unless every one writes its `.tex`."""
        finished_imgs: list[Path] = []

        for batch_start in range(0, len(cmds), self.batch_size):
            batch = cmds[batch_start : batch_start + self.batch_size]
            try:
                procs = [
                    subprocess.Popen(
                        cmd,
                        env=os.environ,
                        startupinfo=silent_startupinfo(),
                        stderr=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        encoding="utf-8",
                        errors="replace",
                    )
                    for cmd in batch
                ]
            except OSError as exc:
                raise TexConversionError(
                    f"Could not start {Executables.rman_oiiotool}: {exc}"
                ) from exc

            failures: list[str] = []
            for cmd, proc in zip(batch, procs):
                stdout, stderr = proc.communicate()
                _process_qt_events()

                img = Path(cmd[-1])
                if proc.returncode == 0 and img.exists():
                    log.debug(f"Successfully converted {img}\n{stdout}{stderr}")
                    finished_imgs.append(img)
                    self._report_progress(
                        PublishStage.CONVERTING_TEX,
                        "Converting source textures to TEX.",
                        current=len(finished_imgs),
                        total=len(cmds),
                    )
                else:
                    log.error(
                        f"TEX conversion of {img} failed with exit code "
                        f"{proc.returncode}\n{stdout}{stderr}"
                    )
                    failures.append(
                        f"{img.name}: {_failure_reason(proc.returncode, stderr)}"
                    )

            # Raised only once the whole batch has exited, so no converter is
            # left running against the publish folder.
            if failures:
                raise TexConversionError(_failure_summary(failures))

        return finished_imgs

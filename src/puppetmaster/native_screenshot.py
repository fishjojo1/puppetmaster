from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import PuppetError

NATIVE_SCREENSHOT_MODES = {"native", "native-window", "native-screen", "window", "screen"}


def normalize_native_screenshot_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized in {"native", "native-window", "window"}:
        return "window"
    if normalized in {"native-screen", "screen"}:
        return "screen"
    raise PuppetError(
        "invalid_screenshot_mode",
        f"invalid screenshot mode: {mode}",
        "Use terminal, native-window, or native-screen.",
    )


def _run_screenshot_command(args: list[str], output_path: Path) -> bytes | None:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        return None
    return output_path.read_bytes()


def _niri_screenshot(scope: str, output_path: Path) -> bytes | None:
    if not shutil.which("niri"):
        return None
    action = "screenshot-screen" if scope == "screen" else "screenshot-window"
    return _run_screenshot_command(
        [
            "niri",
            "msg",
            "action",
            action,
            "--show-pointer",
            "false",
            "--path",
            str(output_path),
        ],
        output_path,
    )


def _gnome_screenshot(scope: str, output_path: Path) -> bytes | None:
    if not shutil.which("gnome-screenshot"):
        return None
    args = ["gnome-screenshot", "-f", str(output_path)]
    if scope == "window":
        args.insert(1, "-w")
    return _run_screenshot_command(args, output_path)


def _scrot_screenshot(scope: str, output_path: Path) -> bytes | None:
    if not shutil.which("scrot"):
        return None
    args = ["scrot", str(output_path)]
    if scope == "window":
        args.insert(1, "-u")
    return _run_screenshot_command(args, output_path)


def _imagemagick_import_screenshot(scope: str, output_path: Path) -> bytes | None:
    if not shutil.which("import") or not os.environ.get("DISPLAY"):
        return None
    window = "root"
    if scope == "window":
        if not shutil.which("xdotool"):
            return None
        proc = subprocess.run(["xdotool", "getactivewindow"], text=True, capture_output=True, timeout=5)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        window = proc.stdout.strip()
    return _run_screenshot_command(["import", "-window", window, str(output_path)], output_path)


def capture_native_screenshot(mode: str) -> tuple[str, bytes]:
    scope = normalize_native_screenshot_mode(mode)
    with tempfile.TemporaryDirectory(prefix="puppet-native-screenshot-") as tmp_dir:
        output_path = Path(tmp_dir) / "screenshot.png"
        for capture in (
            _niri_screenshot,
            _gnome_screenshot,
            _scrot_screenshot,
            _imagemagick_import_screenshot,
        ):
            png = capture(scope, output_path)
            if png is not None:
                return scope, png
            output_path.unlink(missing_ok=True)
    raise PuppetError(
        "native_screenshot_unavailable",
        "native screenshot capture is not available in this environment",
        "Install a supported screenshot tool such as niri screenshot support, gnome-screenshot, scrot, or ImageMagick import with X11.",
    )

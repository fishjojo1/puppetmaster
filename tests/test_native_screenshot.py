from __future__ import annotations

from pathlib import Path

import pytest

from puppetmaster.errors import PuppetError
import puppetmaster.native_screenshot as native_screenshot


def test_normalize_native_screenshot_mode_accepts_aliases():
    assert native_screenshot.normalize_native_screenshot_mode("native") == "window"
    assert native_screenshot.normalize_native_screenshot_mode("native-window") == "window"
    assert native_screenshot.normalize_native_screenshot_mode("native-screen") == "screen"


def test_normalize_native_screenshot_mode_rejects_invalid_mode():
    with pytest.raises(PuppetError) as exc:
        native_screenshot.normalize_native_screenshot_mode("browser")

    assert exc.value.code == "invalid_screenshot_mode"


def test_capture_native_screenshot_uses_niri_when_available(monkeypatch):
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **_kwargs):
        commands.append(args)
        output_path = Path(args[-1])
        output_path.write_bytes(b"\x89PNG\r\n\x1a\nnative")
        return Result()

    monkeypatch.setattr(native_screenshot.shutil, "which", lambda command: "/usr/bin/niri" if command == "niri" else None)
    monkeypatch.setattr(native_screenshot.subprocess, "run", fake_run)

    scope, png = native_screenshot.capture_native_screenshot("native-screen")

    assert scope == "screen"
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert commands[0][:5] == ["niri", "msg", "action", "screenshot-screen", "--show-pointer"]

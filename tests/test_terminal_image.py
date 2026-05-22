from __future__ import annotations

from puppetmaster.terminal_image import _normalize_terminal_runs, _text_width, render_terminal_png


def test_terminal_image_preserves_ansi_foreground_and_background_colors():
    lines = _normalize_terminal_runs("\x1b[31mred\x1b[0m \x1b[48;5;22mgreen-bg\x1b[0m")

    assert len(lines) == 1
    assert [run.text for run in lines[0]] == ["red", " ", "green-bg"]
    assert lines[0][0].foreground == (205, 49, 49)
    assert lines[0][1].foreground == (229, 231, 235)
    assert lines[0][2].background == (0, 95, 0)


def test_terminal_image_renders_ansi_png_bytes():
    png = render_terminal_png("\x1b[38;2;255;128;0mhello\x1b[0m")

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_terminal_image_counts_unicode_terminal_cells():
    assert _text_width("abc") == 3
    assert _text_width("界") == 2
    assert _text_width("🙂") == 2
    assert _text_width("👩\u200d💻") == 2
    assert _text_width("e\u0301") == 1
    assert _text_width("✔\ufe0f") == 1


def test_terminal_image_renders_wide_unicode_png_bytes():
    png = render_terminal_png("box ┌─┐\nwide 界🙂\nicons \uf120 ✔\ufe0f")

    assert png.startswith(b"\x89PNG\r\n\x1a\n")

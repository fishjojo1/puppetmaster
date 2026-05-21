from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_COLUMNS = 240
MAX_ROWS = 100
PADDING_X = 14
PADDING_Y = 12
BACKGROUND = (17, 24, 39)
FOREGROUND = (229, 231, 235)
EMPTY_TEXT = "(empty terminal)"


def _load_font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _normalize_terminal_text(text: str) -> list[str]:
    cleaned = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines = cleaned.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        lines = [EMPTY_TEXT]
    return [line[:MAX_COLUMNS] for line in lines[:MAX_ROWS]]


def render_terminal_png(text: str) -> bytes:
    font = _load_font()
    probe = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), "M", font=font)
    char_width = max(1, bbox[2] - bbox[0])
    line_height = max(1, bbox[3] - bbox[1] + 5)

    lines = _normalize_terminal_text(text)
    columns = max(1, max(len(line) for line in lines))
    width = PADDING_X * 2 + columns * char_width
    height = PADDING_Y * 2 + len(lines) * line_height

    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    y = PADDING_Y
    for line in lines:
        draw.text((PADDING_X, y), line, fill=FOREGROUND, font=font)
        y += line_height

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()

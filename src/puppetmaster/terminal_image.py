from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
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
ANSI_16_COLORS = {
    30: (0, 0, 0),
    31: (205, 49, 49),
    32: (13, 188, 121),
    33: (229, 229, 16),
    34: (36, 114, 200),
    35: (188, 63, 188),
    36: (17, 168, 205),
    37: (229, 229, 229),
    90: (102, 102, 102),
    91: (241, 76, 76),
    92: (35, 209, 139),
    93: (245, 245, 67),
    94: (59, 142, 234),
    95: (214, 112, 214),
    96: (41, 184, 219),
    97: (255, 255, 255),
}
ANSI_BACKGROUND_OFFSET = 10
ANSI_BRIGHT_BACKGROUND_OFFSET = 10


@dataclass(frozen=True)
class TextRun:
    text: str
    foreground: tuple[int, int, int]
    background: tuple[int, int, int] | None = None


def _load_font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/TTF/MesloLGSNerdFontMono-Regular.ttf",
        "/usr/share/fonts/TTF/MesloLGMNerdFontMono-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/Adwaita/AdwaitaMono-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _cell_width(char: str) -> int:
    codepoint = ord(char)
    if char == "\0":
        return 0
    if unicodedata.category(char) in {"Mn", "Me", "Cf"}:
        return 0
    if 0xFE00 <= codepoint <= 0xFE0F:
        return 0
    if 0x1F000 <= codepoint <= 0x1FAFF:
        return 2
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _is_emoji_codepoint(char: str) -> bool:
    codepoint = ord(char)
    return 0x1F000 <= codepoint <= 0x1FAFF


def _cluster_width(cluster: str, fallback_width: int) -> int:
    if "\u200d" in cluster and any(_is_emoji_codepoint(char) for char in cluster):
        return 2
    return max(1, fallback_width)


def _text_width(text: str) -> int:
    return sum(width for _cluster, width in _text_cells(text))


def _truncate_cells(text: str, max_cells: int) -> str:
    if max_cells <= 0:
        return ""
    width = 0
    output: list[str] = []
    for char in text:
        char_width = _cell_width(char)
        if width + char_width > max_cells:
            break
        output.append(char)
        width += char_width
    return "".join(output)


def _text_cells(text: str) -> list[tuple[str, int]]:
    cells: list[tuple[str, int]] = []
    cluster = ""
    cluster_width = 0
    join_next = False
    for char in text:
        char_width = _cell_width(char)
        codepoint = ord(char)
        combines_with_previous = char_width == 0 or join_next or 0xFE00 <= codepoint <= 0xFE0F
        if cluster and not combines_with_previous:
            cells.append((cluster, _cluster_width(cluster, cluster_width)))
            cluster = ""
            cluster_width = 0
        cluster += char
        cluster_width += char_width
        join_next = char == "\u200d"
    if cluster:
        cells.append((cluster, _cluster_width(cluster, cluster_width)))
    return cells


def _xterm_256_color(index: int) -> tuple[int, int, int]:
    if index < 0:
        return FOREGROUND
    base = [
        (0, 0, 0),
        (205, 49, 49),
        (13, 188, 121),
        (229, 229, 16),
        (36, 114, 200),
        (188, 63, 188),
        (17, 168, 205),
        (229, 229, 229),
        (102, 102, 102),
        (241, 76, 76),
        (35, 209, 139),
        (245, 245, 67),
        (59, 142, 234),
        (214, 112, 214),
        (41, 184, 219),
        (255, 255, 255),
    ]
    if index < len(base):
        return base[index]
    if 16 <= index <= 231:
        value = index - 16
        levels = [0, 95, 135, 175, 215, 255]
        return (levels[value // 36], levels[(value // 6) % 6], levels[value % 6])
    if 232 <= index <= 255:
        level = 8 + (index - 232) * 10
        return (level, level, level)
    return FOREGROUND


def _parse_sgr_color(codes: list[int], index: int) -> tuple[tuple[int, int, int] | None, int]:
    mode = codes[index + 1] if index + 1 < len(codes) else None
    if mode == 5 and index + 2 < len(codes):
        return _xterm_256_color(codes[index + 2]), index + 3
    if mode == 2 and index + 4 < len(codes):
        return (
            (
                max(0, min(255, codes[index + 2])),
                max(0, min(255, codes[index + 3])),
                max(0, min(255, codes[index + 4])),
            ),
            index + 5,
        )
    return None, index + 1


def _apply_sgr(
    sequence: str,
    foreground: tuple[int, int, int],
    background: tuple[int, int, int] | None,
) -> tuple[tuple[int, int, int], tuple[int, int, int] | None]:
    if not sequence.endswith("m"):
        return foreground, background
    body = sequence[2:-1]
    codes = [0] if body == "" else [int(part) if part else 0 for part in body.split(";") if part.isdigit() or part == ""]
    index = 0
    while index < len(codes):
        code = codes[index]
        if code == 0:
            foreground = FOREGROUND
            background = None
        elif code == 39:
            foreground = FOREGROUND
        elif code == 49:
            background = None
        elif code in ANSI_16_COLORS:
            foreground = ANSI_16_COLORS[code]
        elif 40 <= code <= 47:
            background = ANSI_16_COLORS.get(code - ANSI_BACKGROUND_OFFSET, background)
        elif 100 <= code <= 107:
            background = ANSI_16_COLORS.get(code - ANSI_BRIGHT_BACKGROUND_OFFSET, background)
        elif code == 38:
            parsed, index = _parse_sgr_color(codes, index)
            if parsed is not None:
                foreground = parsed
            continue
        elif code == 48:
            parsed, index = _parse_sgr_color(codes, index)
            if parsed is not None:
                background = parsed
            continue
        index += 1
    return foreground, background


def _append_run(line: list[TextRun], text: str, foreground: tuple[int, int, int], background: tuple[int, int, int] | None) -> None:
    if not text:
        return
    if line and line[-1].foreground == foreground and line[-1].background == background:
        line[-1] = TextRun(line[-1].text + text, foreground, background)
        return
    line.append(TextRun(text, foreground, background))


def _line_length(line: list[TextRun]) -> int:
    return sum(_text_width(run.text) for run in line)


def _normalize_terminal_text(text: str) -> list[str]:
    cleaned = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines = cleaned.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        lines = [EMPTY_TEXT]
    return [_truncate_cells(line, MAX_COLUMNS) for line in lines[:MAX_ROWS]]


def _normalize_terminal_runs(text: str) -> list[list[TextRun]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines: list[list[TextRun]] = [[]]
    foreground = FOREGROUND
    background: tuple[int, int, int] | None = None
    cursor = 0
    for match in ANSI_RE.finditer(text):
        chunk = text[cursor : match.start()]
        for index, part in enumerate(chunk.split("\n")):
            if index:
                if len(lines) >= MAX_ROWS:
                    return lines
                lines.append([])
            available = MAX_COLUMNS - _line_length(lines[-1])
            if available > 0:
                _append_run(lines[-1], _truncate_cells(part, available), foreground, background)
        foreground, background = _apply_sgr(match.group(0), foreground, background)
        cursor = match.end()

    chunk = text[cursor:]
    for index, part in enumerate(chunk.split("\n")):
        if index:
            if len(lines) >= MAX_ROWS:
                break
            lines.append([])
        available = MAX_COLUMNS - _line_length(lines[-1])
        if available > 0:
            _append_run(lines[-1], _truncate_cells(part, available), foreground, background)

    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        lines = [[TextRun(EMPTY_TEXT, FOREGROUND)]]
    return lines[:MAX_ROWS]


def render_terminal_png(text: str) -> bytes:
    font = _load_font()
    probe = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), "M", font=font)
    char_width = max(1, bbox[2] - bbox[0])
    line_height = max(1, bbox[3] - bbox[1] + 5)

    lines = _normalize_terminal_runs(text)
    columns = max(1, max(_line_length(line) for line in lines))
    width = PADDING_X * 2 + columns * char_width
    height = PADDING_Y * 2 + len(lines) * line_height

    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    y = PADDING_Y
    for line in lines:
        x = PADDING_X
        for run in line:
            run_width = _text_width(run.text) * char_width
            if run.background is not None:
                draw.rectangle((x, y, x + run_width, y + line_height), fill=run.background)
            for cell_text, cell_count in _text_cells(run.text):
                draw.text((x, y), cell_text, fill=run.foreground, font=font)
                x += cell_count * char_width
        y += line_height

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()

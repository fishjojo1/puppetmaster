from __future__ import annotations

import curses
import re
import time
from pathlib import Path
from typing import Any

from .config import Config
from .errors import PuppetError
from .registry import Registry
from .services import read_agent
from .tmux import Tmux


PREVIEW_DEBOUNCE_SECONDS = 0.15
INPUT_POLL_SECONDS = 0.1


def short_id(agent_id: str) -> str:
    return agent_id.split("_", 1)[-1][:8]


def build_tree_rows(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for agent in agents:
        by_parent.setdefault(agent["parent_id"], []).append(agent)

    rows: list[dict[str, Any]] = []

    def walk(parent_id: str | None, depth: int) -> None:
        for agent in by_parent.get(parent_id, []):
            rows.append(
                {
                    "agent": agent,
                    "depth": depth,
                    "has_children": bool(by_parent.get(agent["id"])),
                }
            )
            walk(agent["id"], depth + 1)

    walk(None, 0)
    return rows


def summarize_agent_relationships(agents: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    by_parent: dict[str | None, list[str]] = {}
    agent_ids = {agent["id"] for agent in agents}
    for agent in agents:
        by_parent.setdefault(agent["parent_id"], []).append(agent["id"])

    child_counts = {agent_id: len(by_parent.get(agent_id, [])) for agent_id in agent_ids}
    descendant_counts: dict[str, int] = {}

    def count_descendants(agent_id: str) -> int:
        if agent_id in descendant_counts:
            return descendant_counts[agent_id]
        total = 0
        for child_id in by_parent.get(agent_id, []):
            total += 1 + count_descendants(child_id)
        descendant_counts[agent_id] = total
        return total

    for agent_id in agent_ids:
        count_descendants(agent_id)
    return child_counts, descendant_counts


def format_tree_row(row: dict[str, Any], live: bool) -> str:
    agent = row["agent"]
    indent = "  " * int(row["depth"])
    marker = "+" if row["has_children"] else "-"
    source = "live" if live else "log"
    label = agent.get("name") or agent["role"]
    return f"{indent}{marker} {short_id(agent['id'])} {agent['status']} {source} {label}"


def parse_context_left(text: str) -> str | None:
    clean = strip_ansi(text)
    patterns = [
        r"\bcontext\s+(?:left|remaining)\W*([0-9]+(?:\.[0-9]+)?\s*%)",
        r"\b([0-9]+(?:\.[0-9]+)?\s*%)\W*context\s+(?:left|remaining)\b",
        r"\bcontext\W*([0-9][0-9,._kKmM]*\s*/\s*[0-9][0-9,._kKmM]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).replace("_", "").split())
    return None


ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[P^_X][\s\S]*?(?:\x1b\\|$)"
    r"|\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[0-9=>]"
    r"|\x1b[ -/]*[@-~]"
    r"|\x9d[^\x07\x9c]*(?:\x07|\x9c)?"
    r"|[\x90\x98\x9e\x9f][\s\S]*?(?:\x9c|$)"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_ansi(text: str) -> str:
    clean = ANSI_RE.sub("", text)
    return CONTROL_RE.sub("", clean)


def count_log_lines(path: str) -> int:
    log_path = Path(path)
    if not log_path.exists():
        return 0
    with log_path.open("rb") as fh:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1024 * 1024), b""))


def run_tui(config: Config, registry: Registry, tmux: Tmux, *, root_id: str | None, refresh: float, lines: int) -> int:
    app = TuiApp(config, registry, tmux, root_id=root_id, refresh=refresh, lines=lines)
    curses.wrapper(app.run)
    return 0


class TuiApp:
    def __init__(self, config: Config, registry: Registry, tmux: Tmux, *, root_id: str | None, refresh: float, lines: int):
        self.config = config
        self.registry = registry
        self.tmux = tmux
        self.root_id = root_id
        self.refresh = max(refresh, 0.2)
        self.lines = max(lines, 1)
        self.rows: list[dict[str, Any]] = []
        self.live_sessions: set[str] = set()
        self.selected = 0
        self.scroll = 0
        self.preview_scroll = 0
        self.preview = ""
        self.preview_source = "-"
        self.tree_stats: dict[str, Any] = {}
        self.agent_stats: dict[str, Any] = {}
        self.child_counts: dict[str, int] = {}
        self.descendant_counts: dict[str, int] = {}
        self.message = ""
        self.last_refresh = 0.0
        self.preview_refresh_due_at: float | None = None
        self.preview_reset_pending = False

    def run(self, stdscr: Any) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        stdscr.timeout(self.input_timeout_ms())
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        if curses.has_colors():
            try:
                curses.use_default_colors()
                curses.init_pair(2, curses.COLOR_GREEN, -1)
                curses.init_pair(3, curses.COLOR_YELLOW, -1)
                curses.init_pair(4, curses.COLOR_RED, -1)
            except curses.error:
                pass

        self.reload(force_preview=True)
        while True:
            current_time = time.monotonic()
            if current_time - self.last_refresh >= self.refresh:
                self.reload(force_preview=self.preview_refresh_due_at is None)
                current_time = time.monotonic()
            self.maybe_refresh_debounced_preview(current_time)
            self.draw(stdscr)
            key = stdscr.getch()
            if key == -1:
                continue
            if self.handle_key(key, stdscr):
                return

    def reload(self, *, force_preview: bool = False) -> None:
        try:
            self.message = ""
            agents = self.registry.list_agents(root_id=self.root_id)
            sessions = self.tmux.list_sessions(self.config.tmux_session_prefix)
            self.live_sessions = {item["session"] for item in sessions}
            self.rows = build_tree_rows(agents)
            self.tree_stats = summarize_tree(agents, self.live_sessions)
            self.child_counts, self.descendant_counts = summarize_agent_relationships(agents)
            if self.selected >= len(self.rows):
                self.selected = max(0, len(self.rows) - 1)
            if force_preview:
                self.refresh_preview()
            self.last_refresh = time.monotonic()
        except PuppetError as exc:
            self.message = f"error[{exc.code}]: {exc.message}"
            self.rows = []
            self.preview = self.message
            self.preview_source = "error"
            self.tree_stats = {}
            self.agent_stats = {}
            self.child_counts = {}
            self.descendant_counts = {}

    def input_timeout_ms(self) -> int:
        return max(10, int(min(self.refresh, INPUT_POLL_SECONDS) * 1000))

    def schedule_preview_refresh(self, *, reset_scroll: bool = False) -> None:
        self.preview_refresh_due_at = time.monotonic() + PREVIEW_DEBOUNCE_SECONDS
        self.preview_reset_pending = self.preview_reset_pending or reset_scroll

    def maybe_refresh_debounced_preview(self, current_time: float | None = None) -> bool:
        if self.preview_refresh_due_at is None:
            return False
        if (current_time if current_time is not None else time.monotonic()) < self.preview_refresh_due_at:
            return False
        reset_scroll = self.preview_reset_pending
        self.preview_refresh_due_at = None
        self.preview_reset_pending = False
        self.refresh_preview(reset_scroll=reset_scroll)
        return True

    def refresh_preview(self, *, reset_scroll: bool = False) -> None:
        self.preview_refresh_due_at = None
        self.preview_reset_pending = False
        agent = self.current_agent()
        if not agent:
            self.preview = "No agents found."
            self.preview_source = "-"
            self.agent_stats = {}
            self.preview_scroll = 0
            return
        live = agent["tmux_session"] in self.live_sessions
        self.preview_source = "tmux" if live else "log"
        try:
            self.preview = strip_ansi(read_agent(self.config, self.registry, self.tmux, agent["id"], self.lines, self.preview_source))
        except PuppetError as exc:
            self.preview = f"error[{exc.code}]: {exc.message}"
            self.preview_source = "error"
        self.agent_stats = self.collect_agent_stats(agent)
        if reset_scroll:
            self.preview_scroll = 0

    def collect_agent_stats(self, agent: dict[str, Any]) -> dict[str, Any]:
        log_path = Path(agent["log_path"])
        context_left = parse_context_left(self.preview)
        return {
            "children": self.child_counts.get(agent["id"], 0),
            "descendants": self.descendant_counts.get(agent["id"], 0),
            "pending": self.registry.count_pending_deliveries(agent["id"]),
            "events": self.registry.count_events(agent["id"]),
            "log_bytes": log_path.stat().st_size if log_path.exists() else 0,
            "log_lines": count_log_lines(agent["log_path"]),
            "context_left": context_left or "unknown",
            "created": agent["created_at"],
            "updated": agent["updated_at"],
            "last_turn": agent.get("last_turn_stopped_at") or "-",
        }

    def current_agent(self) -> dict[str, Any] | None:
        if not self.rows:
            return None
        return self.rows[self.selected]["agent"]

    def handle_key(self, key: int, stdscr: Any) -> bool:
        if key in {ord("q"), 27}:
            return True
        if key in {curses.KEY_UP, ord("k")}:
            self.move_selection(-1, stdscr)
        elif key in {curses.KEY_DOWN, ord("j")}:
            self.move_selection(1, stdscr)
        elif key == curses.KEY_PPAGE:
            self.scroll_preview(self.preview_page_size(stdscr), stdscr)
        elif key == curses.KEY_NPAGE:
            self.scroll_preview(-self.preview_page_size(stdscr), stdscr)
        elif key == curses.KEY_HOME:
            self.scroll_preview_to_top(stdscr)
        elif key == curses.KEY_END:
            self.preview_scroll = 0
        elif key == curses.KEY_MOUSE:
            self.handle_mouse(stdscr)
        elif key == ord("g"):
            previous = self.selected
            self.selected = 0
            if self.selected != previous:
                self.schedule_preview_refresh(reset_scroll=True)
        elif key == ord("G"):
            previous = self.selected
            self.selected = max(0, len(self.rows) - 1)
            if self.selected != previous:
                self.schedule_preview_refresh(reset_scroll=True)
        elif key == ord("r"):
            self.reload(force_preview=True)
        elif key in {10, 13, curses.KEY_ENTER, ord("a")}:
            self.attach_selected(stdscr)
        return False

    def move_selection(self, delta: int, stdscr: Any) -> None:
        if not self.rows:
            return
        previous = self.selected
        self.selected = min(max(self.selected + delta, 0), len(self.rows) - 1)
        self.ensure_selected_visible(stdscr)
        if self.selected != previous:
            self.schedule_preview_refresh(reset_scroll=True)

    def preview_page_size(self, stdscr: Any) -> int:
        height, _width = stdscr.getmaxyx()
        return max(1, height - 18)

    def preview_lines(self) -> list[str]:
        return self.preview.splitlines()

    def max_preview_scroll(self, stdscr: Any) -> int:
        return max(0, len(self.preview_lines()) - self.preview_page_size(stdscr))

    def scroll_preview(self, delta: int, stdscr: Any) -> None:
        self.preview_scroll = min(max(self.preview_scroll + delta, 0), self.max_preview_scroll(stdscr))

    def scroll_preview_to_top(self, stdscr: Any) -> None:
        self.preview_scroll = self.max_preview_scroll(stdscr)

    def handle_mouse(self, stdscr: Any) -> None:
        try:
            _mouse_id, x, y, _z, bstate = curses.getmouse()
        except curses.error:
            return
        self.handle_mouse_event(x, y, bstate, stdscr)

    def handle_mouse_event(self, x: int, y: int, bstate: int, stdscr: Any) -> None:
        height, width = stdscr.getmaxyx()
        if y <= 0 or y >= height - 1:
            return
        delta = self.mouse_wheel_delta(bstate)
        if delta == 0:
            return
        if x >= self.right_x(width):
            self.scroll_preview(delta, stdscr)
        else:
            self.move_selection(-1 if delta > 0 else 1, stdscr)

    @staticmethod
    def mouse_wheel_delta(bstate: int) -> int:
        if mouse_state(bstate, "BUTTON4_PRESSED") or mouse_state(bstate, "BUTTON4_CLICKED"):
            return 3
        if mouse_state(bstate, "BUTTON5_PRESSED") or mouse_state(bstate, "BUTTON5_CLICKED"):
            return -3
        return 0

    def ensure_selected_visible(self, stdscr: Any) -> None:
        height, _width = stdscr.getmaxyx()
        tree_height = max(1, height - 3)
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + tree_height:
            self.scroll = self.selected - tree_height + 1

    def attach_selected(self, stdscr: Any) -> None:
        agent = self.current_agent()
        if not agent:
            return
        if agent["tmux_session"] not in self.live_sessions:
            self.message = "Selected agent has no live tmux session."
            return
        curses.endwin()
        try:
            self.tmux.attach(agent["tmux_session"])
        except PuppetError as exc:
            self.message = f"error[{exc.code}]: {exc.message}"
        finally:
            stdscr.clear()
            stdscr.keypad(True)
            stdscr.timeout(self.input_timeout_ms())
            self.reload(force_preview=True)

    def draw(self, stdscr: Any) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 10 or width < 50:
            addstr(stdscr, 0, 0, "Terminal too small for Puppetmaster TUI.", width)
            return

        left_width = self.left_width(width)
        right_x = self.right_x(width)
        right_width = width - right_x
        content_height = height - 2

        title = "Puppetmaster TUI"
        if self.root_id:
            title += f" root={self.root_id}"
        addstr(stdscr, 0, 0, title, width, curses.A_BOLD)
        addstr(
            stdscr,
            height - 1,
            0,
            "q quit | up/down select | pgup/pgdn scroll preview | home/end preview | enter attach | r refresh",
            width,
            curses.A_DIM,
        )

        for y in range(1, height - 1):
            addstr(stdscr, y, left_width, "|", 1, curses.A_DIM)

        self.draw_tree(stdscr, 1, 0, content_height, left_width)
        self.draw_details(stdscr, 1, right_x, min(7, content_height), right_width)
        self.draw_stats(stdscr, 9, right_x, min(7, height - 11), right_width)
        self.draw_preview(stdscr, 17, right_x, height - 18, right_width)
        stdscr.refresh()

    def draw_tree(self, stdscr: Any, y: int, x: int, height: int, width: int) -> None:
        self.scroll = min(self.scroll, max(0, len(self.rows) - height))
        visible = self.rows[self.scroll : self.scroll + height]
        for offset, row in enumerate(visible):
            index = self.scroll + offset
            agent = row["agent"]
            line = format_tree_row(row, agent["tmux_session"] in self.live_sessions)
            attr = curses.A_REVERSE if index == self.selected else curses.A_NORMAL
            if agent["status"] in {"failed", "blocked", "dead", "killed"}:
                attr |= color_attr(4)
            elif agent["status"] in {"idle", "awaiting_input"}:
                attr |= color_attr(3)
            elif agent["tmux_session"] in self.live_sessions:
                attr |= color_attr(2)
            addstr(stdscr, y + offset, x, line, width, attr)

    def draw_details(self, stdscr: Any, y: int, x: int, height: int, width: int) -> None:
        agent = self.current_agent()
        if not agent:
            addstr(stdscr, y, x, "No agents.", width)
            return
        live = agent["tmux_session"] in self.live_sessions
        details = [
            f"{agent['id']} ({'live' if live else 'not live'})",
            f"name: {agent.get('name') or '-'}",
            f"role/status: {agent['role']} / {agent['status']}",
            f"cwd: {agent['cwd']}",
            f"parent: {agent.get('parent_id') or '-'}",
            f"tmux: {agent['tmux_session']}",
        ]
        if self.message:
            details.append(self.message)
        for offset, line in enumerate(details[:height]):
            attr = curses.A_BOLD if offset == 0 else curses.A_NORMAL
            addstr(stdscr, y + offset, x, line, width, attr)

    def draw_stats(self, stdscr: Any, y: int, x: int, height: int, width: int) -> None:
        if height <= 0:
            return
        addstr(stdscr, y - 1, x, "Stats", width, curses.A_BOLD)
        tree = self.tree_stats
        agent = self.agent_stats
        lines = [
            f"tree: {tree.get('total', 0)} agents, {tree.get('live', 0)} live, depth {tree.get('max_depth', 0)}",
            f"statuses: {tree.get('statuses', '-')}",
            f"context left: {agent.get('context_left', 'unknown')}",
            f"selected: {agent.get('children', 0)} children, {agent.get('descendants', 0)} descendants",
            f"queues/events: {agent.get('pending', 0)} pending, {agent.get('events', 0)} events",
            f"log: {format_bytes(agent.get('log_bytes', 0))}, {agent.get('log_lines', 0)} lines, preview {self.preview_source}",
            f"updated: {agent.get('updated', '-')} | last turn: {agent.get('last_turn', '-')}",
        ]
        for offset, line in enumerate(lines[:height]):
            addstr(stdscr, y + offset, x, line, width)

    def draw_preview(self, stdscr: Any, y: int, x: int, height: int, width: int) -> None:
        if height <= 0:
            return
        lines = self.preview_lines()
        max_scroll = max(0, len(lines) - height)
        self.preview_scroll = min(self.preview_scroll, max_scroll)
        title = "Preview"
        if max_scroll:
            current = max_scroll - self.preview_scroll + 1
            title = f"Preview {current}-{min(current + height - 1, len(lines))}/{len(lines)}"
        addstr(stdscr, y - 1, x, title, width, curses.A_BOLD)
        start = max(0, len(lines) - height - self.preview_scroll)
        lines = lines[start : start + height]
        for offset, line in enumerate(lines):
            addstr(stdscr, y + offset, x, line, width)

    @staticmethod
    def left_width(width: int) -> int:
        return min(max(34, width // 3), width - 24)

    @classmethod
    def right_x(cls, width: int) -> int:
        return cls.left_width(width) + 1


def color_attr(pair: int) -> int:
    if curses.has_colors():
        return curses.color_pair(pair)
    return curses.A_NORMAL


def mouse_state(bstate: int, name: str) -> bool:
    return bool(bstate & getattr(curses, name, 0))


def summarize_tree(agents: list[dict[str, Any]], live_sessions: set[str]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for agent in agents:
        statuses[agent["status"]] = statuses.get(agent["status"], 0) + 1
    status_text = ", ".join(f"{status}:{count}" for status, count in sorted(statuses.items())) or "-"
    return {
        "total": len(agents),
        "live": sum(1 for agent in agents if agent["tmux_session"] in live_sessions),
        "max_depth": max((int(agent["depth"]) for agent in agents), default=0),
        "statuses": status_text,
    }


def format_bytes(value: int) -> str:
    amount = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or suffix == "GiB":
            return f"{amount:.1f} {suffix}" if suffix != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{int(value)} B"


def addstr(stdscr: Any, y: int, x: int, value: str, width: int, attr: int = curses.A_NORMAL) -> None:
    if width <= 0:
        return
    text = value.replace("\t", "    ")
    if len(text) > width:
        text = text[: max(0, width - 1)]
    try:
        stdscr.addstr(y, x, text.ljust(width), attr)
    except curses.error:
        pass

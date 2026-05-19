from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config
from .errors import PuppetError

PROMPT_SUBMIT_CONFIRM_DELAY_SECONDS = 0.15


class Tmux:
    def __init__(self, config: Config):
        self.config = config

    def require_tmux(self) -> str:
        path = shutil.which("tmux")
        if not path:
            raise PuppetError("tmux_missing", "tmux executable not found", "Install tmux and retry.")
        return path

    def session_exists(self, session: str) -> bool:
        self.require_tmux()
        proc = subprocess.run(["tmux", "has-session", "-t", session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0

    def create_session(self, session: str, cwd: str, command: str | list[str]) -> None:
        self.require_tmux()
        if self.session_exists(session):
            raise PuppetError("tmux_session_exists", f"tmux session already exists: {session}")
        cmd = command if isinstance(command, str) else shlex.join(command)
        proc = subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", cwd, cmd], text=True, capture_output=True)
        if proc.returncode != 0:
            raise PuppetError("tmux_launch_failed", f"tmux failed to launch {session}: {proc.stderr.strip()}")

    def pipe_pane(self, session: str, log_path: str) -> None:
        self.require_tmux()
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        quoted = shlex.quote(log_path)
        proc = subprocess.run(
            ["tmux", "pipe-pane", "-t", session, "-o", f"cat >> {quoted}"],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise PuppetError("log_capture_failed", f"tmux pipe-pane failed: {proc.stderr.strip()}")

    def capture_pane(self, session: str, lines: int) -> str:
        self.require_tmux()
        proc = subprocess.run(
            ["tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", session],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise PuppetError("tmux_capture_failed", f"tmux capture-pane failed: {proc.stderr.strip()}")
        return proc.stdout

    def send_prompt(self, session: str, prompt: str) -> None:
        self.require_tmux()
        buffer_name = f"puppet_prompt_{os.getpid()}"
        subprocess.run(["tmux", "set-buffer", "-b", buffer_name, prompt], check=True)
        try:
            subprocess.run(["tmux", "paste-buffer", "-b", buffer_name, "-t", session], check=True)
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True)
            time.sleep(PROMPT_SUBMIT_CONFIRM_DELAY_SECONDS)
            subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True)
        finally:
            subprocess.run(["tmux", "delete-buffer", "-b", buffer_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop_session(self, session: str) -> None:
        if not self.session_exists(session):
            return
        subprocess.run(["tmux", "send-keys", "-t", session, "C-c"], check=False)

    def kill_session(self, session: str) -> None:
        if not self.session_exists(session):
            return
        subprocess.run(["tmux", "kill-session", "-t", session], check=False)

    def attach_command(self, session: str) -> str:
        return f"tmux attach -t {shlex.quote(session)}"

    def attach(self, session: str) -> int:
        if not self.session_exists(session):
            raise PuppetError("tmux_missing_session", f"tmux session is not live: {session}", "Use agent read to inspect logs.")
        return subprocess.call(["tmux", "attach", "-t", session])

    def list_sessions(self, prefix: str | None = None) -> list[dict[str, str]]:
        self.require_tmux()
        fmt = "#{session_name}\t#{session_created}\t#{session_attached}\t#{pane_current_command}"
        proc = subprocess.run(["tmux", "list-sessions", "-F", fmt], text=True, capture_output=True)
        if proc.returncode != 0:
            return []
        sessions = []
        for line in proc.stdout.splitlines():
            name, created, attached, pane_command = (line.split("\t") + ["", "", "", ""])[:4]
            if prefix and not name.startswith(prefix):
                continue
            sessions.append(
                {"session": name, "created": created, "attached": attached, "pane_command": pane_command}
            )
        return sessions

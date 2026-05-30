from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import load_config, puppetmaster_subprocess_env, write_init_config
from .errors import PuppetError
from .registry import Registry
from .services import (
    DEAD_CLEANUP_STATUSES,
    cleanup_completed_agents,
    complete_agent,
    create_codex_agent,
    create_raw_agent,
    drain_events,
    doctor,
    fire_due_wakeups,
    fire_wakeup,
    handle_stop_hook,
    inspect_agent,
    kill_agent,
    kill_root_tree,
    notify_agent_state_change,
    pause_agent,
    prompt_agent,
    read_agent,
    reconcile,
    reset_agents,
    resume_agent,
    sleep_and_fire_wakeup,
    start_orchestrator,
    stop_agent,
)
from .tmux import Tmux

DISCORD_PID_FILE = "discord-bot.pid"
DISCORD_LOG_FILE = "discord-bot.log"
DISCORD_LOCK_FILE = "discord-bot.lock"


def build_context():
    cfg = load_config()
    reg = Registry(cfg)
    tmux = Tmux(cfg)
    return cfg, reg, tmux


def emit(data: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(render(data))


def render(data: Any) -> str:
    if isinstance(data, list):
        return "\n".join(render(item) for item in data)
    if isinstance(data, dict) and "agent" in data and "recent_events" in data:
        agent = data["agent"]
        events = "\n".join(f"- {e['type']}: {e['summary']}" for e in data["recent_events"]) or "-"
        children = ", ".join(child["id"] for child in data["children"]) or "-"
        output = data.get("recent_output") or ""
        return f"""Agent: {agent['id']}
Name: {agent.get('name') or '-'}
Description: {agent['description']}
Role: {agent['role']}
Status: {agent['status']}
Completion: {agent.get('completion_status') or '-'}
Cwd: {agent['cwd']}
Parent: {agent.get('parent_id') or '-'}
Root: {agent['root_id']}
Children: {children}
Tmux: {agent['tmux_session']} ({'live' if data['tmux_exists'] else 'missing'})
Attach: {data['attach_command']}
Log: {agent['log_path']}
Initial prompt: {agent.get('initial_prompt_path') or '-'}
Last turn stopped: {agent.get('last_turn_stopped_at') or '-'}
Completed at: {agent.get('completed_at') or '-'}

Recent events:
{events}

Recent output:
{output}
"""
    if isinstance(data, dict) and {"id", "status", "role", "cwd"}.issubset(data):
        return f"{data['id']} {data['status']} {data['role']} {data.get('name') or '-'} {data['cwd']}"
    if isinstance(data, dict) and "checks" in data:
        rows = [f"{'OK' if check['ok'] else 'FAIL'} {check['name']} {check.get('detail') or ''}" for check in data["checks"]]
        return "\n".join(rows)
    if isinstance(data, dict) and "changes" in data:
        rows = [f"{c['agent_id']}: {c['from']} -> {c['to']} ({c['reason']})" for c in data["changes"]]
        if data.get("unmanaged_tmux"):
            rows.append("Unmanaged tmux: " + ", ".join(data["unmanaged_tmux"]))
        return "\n".join(rows) or "No reconciliation changes."
    if isinstance(data, dict):
        return json.dumps(data, indent=2, sort_keys=True)
    return str(data)


def read_prompt(args: argparse.Namespace) -> str:
    value = getattr(args, "prompt", None)
    file_value = getattr(args, "prompt_file", None)
    if value is not None:
        return value
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    raise PuppetError("prompt_required", "prompt is required", "Pass --prompt or --prompt-file.")


def parse_codex_home_args(values: list[str] | None) -> list[str]:
    homes: list[str] = []
    for value in values or []:
        parts = str(value).split(",")
        for part in parts:
            text = part.strip()
            if not text:
                raise PuppetError("invalid_config", "--codex-home entries must be non-empty paths.")
            homes.append(text)
    return homes


def add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Render machine-readable JSON.")


def _input_or_none(prompt: str) -> str | None:
    value = input(prompt).strip()
    return value if value else None


def _yes(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def _init_result(cfg, *, started_discord: bool, discord_status: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state_dir": str(cfg.state_dir),
        "config_path": str(cfg.state_dir / "config.toml"),
        "discord_token_configured": bool(cfg.discord.token),
        "discord_guild_id": cfg.discord.guild_id,
        "started_discord": started_discord,
    }
    if discord_status:
        result["discord"] = discord_status
    return result


def _render_init_result(result: dict[str, Any]) -> str:
    token_status = "configured" if result["discord_token_configured"] else "not configured"
    guild = result["discord_guild_id"]
    guild_status = str(guild) if guild is not None else "not configured"
    rows = [
        f"Puppetmaster home: {result['state_dir']}",
        f"Config: {result['config_path']}",
        f"Discord token: {token_status}",
        f"Discord guild: {guild_status}",
    ]
    discord_status = result.get("discord")
    if discord_status:
        if discord_status.get("already_running"):
            rows.append(f"Discord bot: already running pid {discord_status['pid']}")
        elif result["started_discord"]:
            rows.append(f"Discord bot: started pid {discord_status['pid']}")
        rows.append(f"Discord log: {discord_status['log_path']}")
    elif not result["started_discord"]:
        rows.append("Discord bot: not started")
    rows.append("")
    rows.append("Next: puppet discord serve --background")
    return "\n".join(rows)


def cmd_init(args: argparse.Namespace) -> int:
    existing_cfg = load_config()
    discord_token = args.discord_token
    discord_guild_id = args.discord_guild_id
    start_discord = args.start_discord

    if not args.no_input:
        print(f"Puppetmaster home: {existing_cfg.state_dir}")
        print()
        if discord_token is None:
            token_prompt = "Discord bot token [hidden, leave blank to keep existing]: "
            discord_token = getpass.getpass(token_prompt) or None
        if discord_guild_id is None:
            discord_guild_id = _input_or_none("Discord guild ID [leave blank to keep existing]: ")
        if not start_discord:
            start_discord = _yes("Start Discord bot in background now? [y/N]: ")

    cfg = write_init_config(discord_token=discord_token, discord_guild_id=discord_guild_id)
    discord_status = None
    started_discord = False
    if start_discord:
        status = discord_background_status(cfg)
        if status["running"]:
            discord_status = {**status, "already_running": True}
        else:
            if status.get("stale_pid_file"):
                _discord_pid_path(cfg).unlink(missing_ok=True)
            discord_status = start_discord_background_process(cfg)
            started_discord = True

    result = _init_result(cfg, started_discord=started_discord, discord_status=discord_status)
    if args.json:
        emit(result, True)
    else:
        print(_render_init_result(result))
    return 0


def cmd_agent_create(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    parent_id = args.parent
    if parent_id is None:
        roots = [a for a in reg.list_agents() if a["role"] == "orchestrator" and a["status"] in {"running", "idle", "awaiting_input"}]
        if len(roots) == 1:
            parent_id = roots[0]["id"]
        elif len(roots) > 1:
            raise PuppetError("parent_required", "multiple active orchestrators exist", "Pass --parent explicitly.")
        else:
            raise PuppetError("parent_required", "agent create requires an active orchestrator or --parent in v1")
    agent = create_codex_agent(
        cfg,
        reg,
        tmux,
        cwd=args.cwd,
        description=args.description,
        prompt=read_prompt(args),
        parent_id=parent_id,
        name=args.name,
    )
    emit({"agent": agent, "attach_command": tmux.attach_command(agent["tmux_session"])}, args.json)
    return 0


def cmd_debug_create_raw(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    agent = create_raw_agent(cfg, reg, tmux, cwd=args.cwd, description=args.description, command=args.command, parent_id=args.parent, name=args.name)
    emit({"agent": agent, "attach_command": tmux.attach_command(agent["tmux_session"])}, args.json)
    return 0


def cmd_agent_list(args: argparse.Namespace) -> int:
    cfg, reg, _tmux = build_context()
    agents = reg.list_agents(root_id=args.root, parent_id=args.parent, status=args.status, include_dead=args.include_dead)
    if args.json:
        emit(agents, True)
    else:
        print("ID STATUS ROLE NAME CWD")
        for agent in agents:
            print(f"{agent['id']} {agent['status']} {agent['role']} {agent.get('name') or '-'} {agent['cwd']}")
    return 0


def cmd_agent_tree(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    agents = reg.list_agents(root_id=args.root)
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for agent in agents:
        by_parent.setdefault(agent["parent_id"], []).append(agent)

    def walk(parent: str | None, depth: int) -> list[str]:
        rows = []
        for agent in by_parent.get(parent, []):
            rows.append("  " * depth + f"{agent['id']} {agent['role']} {agent['status']} {agent.get('name') or '-'} {agent['cwd']}")
            rows.extend(walk(agent["id"], depth + 1))
        return rows

    print("\n".join(walk(None, 0)))
    return 0


def cmd_agent_inspect(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(inspect_agent(cfg, reg, tmux, args.agent_id), args.json)
    return 0


def cmd_orchestrator_inspect(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    roots = [a for a in reg.list_agents() if a["role"] == "orchestrator"]
    if not roots:
        raise PuppetError("not_found", "no orchestrator exists")
    emit(inspect_agent(cfg, reg, tmux, roots[-1]["id"]), args.json)
    return 0


def cmd_agent_read(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    print(read_agent(cfg, reg, tmux, args.agent_id, args.lines, args.source))
    return 0


def cmd_agent_stop(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(stop_agent(reg, tmux, args.agent_id, config=cfg), args.json)
    return 0


def cmd_agent_kill(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(kill_agent(reg, tmux, args.agent_id, config=cfg), args.json)
    return 0


def cmd_agent_kill_tree(args: argparse.Namespace) -> int:
    _cfg, reg, tmux = build_context()
    emit(kill_root_tree(reg, tmux, args.root_agent_id, dry_run=args.dry_run), args.json)
    return 0


def cmd_agent_reset(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(reset_agents(cfg, reg, tmux, dry_run=args.dry_run), args.json)
    return 0


def cmd_agent_prompt(args: argparse.Namespace) -> int:
    _cfg, reg, tmux = build_context()
    emit(prompt_agent(reg, tmux, args.agent_id, read_prompt(args)), args.json)
    return 0


def cmd_agent_attach(args: argparse.Namespace) -> int:
    _cfg, reg, tmux = build_context()
    agent = reg.get_agent(args.agent_id)
    command = tmux.attach_command(agent["tmux_session"])
    if args.print or args.json:
        emit({"attach_command": command}, args.json)
        return 0
    return tmux.attach(agent["tmux_session"])


def cmd_agent_complete(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(complete_agent(reg, args.agent_id, status=args.status, summary=args.summary, source="human_cli", config=cfg, tmux=tmux), args.json)
    return 0


def cmd_agent_mark_status(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    updated = reg.update_agent(args.agent_id, status=args.status, termination_reason=args.reason)
    notify_agent_state_change(
        reg,
        args.agent_id,
        "agent.status_overridden",
        args.reason,
        {"status": args.status},
        severity="warning",
        source="human_cli",
        coalesce=True,
        config=cfg,
        tmux=tmux,
    )
    emit(updated, args.json)
    return 0


def cmd_agent_pause(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(pause_agent(reg, args.agent_id, source="human_cli", config=cfg, tmux=tmux), args.json)
    return 0


def cmd_agent_resume(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(resume_agent(reg, args.agent_id, source="human_cli", config=cfg, tmux=tmux), args.json)
    return 0


def cmd_agent_cleanup_dead(args: argparse.Namespace) -> int:
    _cfg, reg, tmux = build_context()
    emit(
        cleanup_completed_agents(
            reg,
            tmux,
            root_id=args.root,
            dry_run=args.dry_run,
            kill_stale=args.kill_stale,
            include_roots=args.include_roots,
            cleanup_statuses=DEAD_CLEANUP_STATUSES,
        ),
        args.json,
    )
    return 0


def cmd_agent_cleanup_completed(args: argparse.Namespace) -> int:
    _cfg, reg, tmux = build_context()
    emit(
        cleanup_completed_agents(
            reg,
            tmux,
            root_id=args.root,
            dry_run=args.dry_run,
            kill_stale=args.kill_stale,
            include_roots=args.include_roots,
        ),
        args.json,
    )
    return 0


def cmd_orchestrator_start(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    codex_homes = parse_codex_home_args(getattr(args, "codex_home", None))
    codex_home_pool = None
    if len(codex_homes) == 1:
        cfg = cfg.with_codex_home(codex_homes[0])
    elif len(codex_homes) > 1:
        cfg = cfg.with_codex_home_pool(codex_homes)
        codex_home_pool = codex_homes
    cwd = args.cwd or str(Path.cwd())
    agent = start_orchestrator(
        cfg,
        reg,
        tmux,
        cwd=cwd,
        prompt=read_prompt(args),
        name=args.name,
        new_root=args.new_root,
        agent_id=args.agent_id,
        goal=args.goal,
        codex_home_pool=codex_home_pool,
    )
    emit({"agent": agent, "attach_command": tmux.attach_command(agent["tmux_session"])}, args.json)
    return 0


def cmd_events_list(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    emit(reg.list_events(args.agent, args.limit), args.json)
    return 0


def cmd_events_pending(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    emit(reg.pending_deliveries(args.agent_id, args.limit), args.json)
    return 0


def cmd_events_ack(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    reg.acknowledge_delivery(args.delivery_id)
    emit({"acknowledged": args.delivery_id}, args.json)
    return 0


def cmd_hook_stop(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    handle_stop_hook(reg, args.agent_id, sys.stdin.read())
    return 0


def cmd_hook_drain(args: argparse.Namespace) -> int:
    cfg, reg, _tmux = build_context()
    hook_input = sys.stdin.read()
    if hook_input:
        handle_stop_hook(reg, args.agent_id, hook_input)
    result = drain_events(cfg, reg, args.agent_id)
    if result.get("decision"):
        print(json.dumps(result))
    return 0


def cmd_wakeup_fire_due(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(fire_due_wakeups(cfg, reg, agent_id=args.agent, tmux=tmux), args.json)
    return 0


def cmd_wakeup_fire(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(fire_wakeup(cfg, reg, args.wakeup_id, tmux=tmux), args.json)
    return 0


def cmd_wakeup_sleep_and_fire(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    sleep_and_fire_wakeup(cfg, reg, tmux, args.wakeup_id)
    return 0


def cmd_wakeup_list(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    emit(reg.list_wakeups(agent_id=args.agent, status=args.status), args.json)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    result = doctor(cfg, reg, tmux, args.deep)
    emit(result, args.json)
    return 0 if result["ok"] else 1


def cmd_reconcile(args: argparse.Namespace) -> int:
    cfg, reg, tmux = build_context()
    emit(reconcile(cfg, reg, tmux, agent_id=args.agent, root_id=args.root, dry_run=args.dry_run), args.json)
    return 0


def cmd_debug_tmux(args: argparse.Namespace) -> int:
    cfg, _reg, tmux = build_context()
    emit(tmux.list_sessions(cfg.tmux_session_prefix), args.json)
    return 0


def cmd_debug_registry(args: argparse.Namespace) -> int:
    _cfg, reg, _tmux = build_context()
    emit({"agents": reg.list_agents(), "events": reg.list_events(limit=100), "deliveries": reg.all_deliveries(limit=100)}, args.json)
    return 0


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    from .mcp_server import run

    run()
    return 0


def _discord_pid_path(cfg) -> Path:
    return cfg.state_dir / DISCORD_PID_FILE


def _discord_log_path(cfg) -> Path:
    return cfg.state_dir / DISCORD_LOG_FILE


def _discord_lock_path(cfg) -> Path:
    return cfg.state_dir / DISCORD_LOCK_FILE


@contextlib.contextmanager
def discord_serve_lock(cfg):
    lock_path = _discord_lock_path(cfg)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = lock_path.open("w", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            status = discord_background_status(cfg)
            pid = status.get("pid")
            detail = f" with pid {pid}" if pid else ""
            raise PuppetError(
                "discord_bot_already_running",
                f"discord bot is already running{detail}",
                "Use `puppet discord status` or stop the existing discord serve process.",
            ) from exc
        _discord_pid_path(cfg).write_text(f"{os.getpid()}\n", encoding="utf-8")
        lock_fh.seek(0)
        lock_fh.truncate()
        lock_fh.write(f"{os.getpid()}\n")
        lock_fh.flush()
        yield
    finally:
        current_pid = _read_pid(_discord_pid_path(cfg))
        if current_pid == os.getpid():
            _discord_pid_path(cfg).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_matches_discord_process(pid: int) -> bool:
    cmdline_path = Path("/proc") / str(pid) / "cmdline"
    if not cmdline_path.exists():
        return True
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        return False
    parts = [part.decode(errors="ignore") for part in raw.split(b"\0") if part]
    if not parts:
        return False
    joined = " ".join(parts)
    return "puppetmaster.cli" in joined and "discord" in parts and "serve" in parts


def discord_background_status(cfg) -> dict[str, Any]:
    pid_path = _discord_pid_path(cfg)
    log_path = _discord_log_path(cfg)
    pid = _read_pid(pid_path)
    if pid is None:
        return {"running": False, "pid": None, "pid_file": str(pid_path), "log_path": str(log_path)}

    running = _pid_is_running(pid) and _pid_matches_discord_process(pid)
    return {
        "running": running,
        "pid": pid,
        "pid_file": str(pid_path),
        "log_path": str(log_path),
        "stale_pid_file": not running,
    }


def start_discord_background_process(cfg) -> dict[str, Any]:
    status = discord_background_status(cfg)
    if status["running"]:
        raise PuppetError(
            "discord_bot_already_running",
            f"discord bot is already running with pid {status['pid']}",
            "Use `puppet discord status` or `puppet discord stop`.",
        )
    if status.get("stale_pid_file"):
        _discord_pid_path(cfg).unlink(missing_ok=True)

    from .discord_bot import validate_discord_config

    validate_discord_config(cfg)

    log_path = _discord_log_path(cfg)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "puppetmaster.cli", "discord", "serve"],
            env=puppetmaster_subprocess_env(cfg),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_fh.close()

    result = {"running": True, "pid": proc.pid, "pid_file": str(_discord_pid_path(cfg)), "log_path": str(log_path)}
    _discord_pid_path(cfg).write_text(f"{proc.pid}\n", encoding="utf-8")
    return result


def start_discord_background(args: argparse.Namespace) -> int:
    cfg = load_config()
    emit(start_discord_background_process(cfg), args.json)
    return 0


def cmd_discord_serve(args: argparse.Namespace) -> int:
    if args.background:
        return start_discord_background(args)
    cfg = load_config()

    try:
        from .discord_bot import run_discord_bot
    except ImportError as exc:
        raise PuppetError(
            "discord_dependency_missing",
            "discord.py is not installed.",
            "Install project dependencies with `uv sync` and retry.",
        ) from exc

    with discord_serve_lock(cfg):
        return run_discord_bot()


def cmd_discord_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    emit(discord_background_status(cfg), args.json)
    return 0


def cmd_discord_stop(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.timeout <= 0:
        raise PuppetError(
            "invalid_timeout",
            "stop timeout must be positive.",
            "Pass --timeout with a positive number of seconds.",
        )
    status = discord_background_status(cfg)
    pid = status["pid"]
    if not status["running"] or pid is None:
        _discord_pid_path(cfg).unlink(missing_ok=True)
        emit({**status, "stopped": False}, args.json)
        return 0

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if not _pid_is_running(pid) or not _pid_matches_discord_process(pid):
            _discord_pid_path(cfg).unlink(missing_ok=True)
            emit({**status, "running": False, "stopped": True}, args.json)
            return 0
        time.sleep(0.1)

    raise PuppetError(
        "discord_bot_stop_timeout",
        f"discord bot pid {pid} did not stop within {args.timeout:.1f}s",
        "Stop it manually or retry after checking the log.",
    )


def cmd_tui(args: argparse.Namespace) -> int:
    from .tui import run_tui

    cfg, reg, tmux = build_context()
    return run_tui(cfg, reg, tmux, root_id=args.root, refresh=args.refresh, lines=args.lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puppet", description="Puppetmaster local Codex agent supervisor.")
    sub = parser.add_subparsers(required=True)

    init_p = sub.add_parser("init", help="Create or update the Puppetmaster global config.")
    init_p.add_argument("--discord-token", help="Set or replace the Discord bot token.")
    init_p.add_argument("--discord-guild-id", help="Set or replace the Discord guild id.")
    init_p.add_argument("--start-discord", action="store_true", help="Start the Discord bot in the background after writing config.")
    init_p.add_argument("--no-input", action="store_true", help="Do not prompt; preserve existing values unless flags replace them.")
    add_json(init_p)
    init_p.set_defaults(func=cmd_init)

    orch = sub.add_parser("orchestrator", help="Manage the root orchestrator.")
    orch_sub = orch.add_subparsers(required=True)
    start = orch_sub.add_parser("start", help="Start a managed Codex root orchestrator.")
    start.add_argument("--cwd", help="Workspace directory for the orchestrator. Defaults to the current directory.")
    start.add_argument("--prompt")
    start.add_argument("--prompt-file")
    start.add_argument("--name", default="root")
    start.add_argument("--agent-id", help="Use this exact safe id for the new root orchestrator.")
    start.add_argument(
        "--codex-home",
        action="append",
        help=(
            "Source CODEX_HOME for Codex config/auth. Pass once for a fixed root tree home, "
            "or pass multiple times/comma-delimited paths to set a root-scoped account pool."
        ),
    )
    start.add_argument("--goal", action="store_true", help="Start the orchestrator in Codex goal mode by prepending /goal to the initial prompt.")
    start.add_argument("--new-root", action="store_true", help="Deprecated compatibility flag; start always creates a root.")
    add_json(start)
    start.set_defaults(func=cmd_orchestrator_start)
    inspect = orch_sub.add_parser("inspect", help="Inspect the most recent orchestrator.")
    add_json(inspect)
    inspect.set_defaults(func=cmd_orchestrator_inspect)

    agent = sub.add_parser("agent", help="Manage agents.")
    a = agent.add_subparsers(required=True)
    create = a.add_parser("create", help="Create a managed Codex child agent.")
    create.add_argument("--cwd", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--prompt")
    create.add_argument("--prompt-file")
    create.add_argument("--name")
    create.add_argument("--parent")
    add_json(create)
    create.set_defaults(func=cmd_agent_create)
    create_codex = a.add_parser("create-codex", help="Compatibility alias for agent create.")
    create_codex.add_argument("--cwd", required=True)
    create_codex.add_argument("--description", required=True)
    create_codex.add_argument("--prompt")
    create_codex.add_argument("--prompt-file")
    create_codex.add_argument("--name")
    create_codex.add_argument("--parent")
    add_json(create_codex)
    create_codex.set_defaults(func=cmd_agent_create)
    create_raw = a.add_parser("create-raw", help="Compatibility alias for debug create-raw.")
    create_raw.add_argument("--cwd", required=True)
    create_raw.add_argument("--description", required=True)
    create_raw.add_argument("--command", required=True)
    create_raw.add_argument("--parent")
    create_raw.add_argument("--name")
    add_json(create_raw)
    create_raw.set_defaults(func=cmd_debug_create_raw)
    list_p = a.add_parser("list", help="List agents.")
    list_p.add_argument("--root")
    list_p.add_argument("--parent")
    list_p.add_argument("--status")
    list_p.add_argument("--include-dead", action="store_true", default=True)
    add_json(list_p)
    list_p.set_defaults(func=cmd_agent_list)
    tree = a.add_parser("tree", help="Show parent-child relationships.")
    tree.add_argument("--root")
    tree.set_defaults(func=cmd_agent_tree)
    inspect_a = a.add_parser("inspect", help="Inspect an agent.")
    inspect_a.add_argument("agent_id")
    add_json(inspect_a)
    inspect_a.set_defaults(func=cmd_agent_inspect)
    read = a.add_parser("read", help="Read recent terminal output.")
    read.add_argument("agent_id")
    read.add_argument("--lines", type=int)
    read.add_argument("--source", choices=["auto", "log", "tmux"], default="auto")
    read.set_defaults(func=cmd_agent_read)
    prompt = a.add_parser("prompt", help="Send a prompt to a live agent.")
    prompt.add_argument("agent_id")
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    add_json(prompt)
    prompt.set_defaults(func=cmd_agent_prompt)
    attach = a.add_parser("attach", help="Attach to an agent tmux session.")
    attach.add_argument("agent_id")
    attach.add_argument("--print", action="store_true")
    add_json(attach)
    attach.set_defaults(func=cmd_agent_attach)
    stop = a.add_parser("stop", help="Gracefully stop an agent.")
    stop.add_argument("agent_id")
    add_json(stop)
    stop.set_defaults(func=cmd_agent_stop)
    kill = a.add_parser("kill", help="Kill an agent tmux session.")
    kill.add_argument("agent_id")
    add_json(kill)
    kill.set_defaults(func=cmd_agent_kill)
    kill_tree = a.add_parser("kill-tree", help="Kill all live tmux sessions for one root orchestrator tree.")
    kill_tree.add_argument("root_agent_id")
    kill_tree.add_argument("--dry-run", action="store_true", help="Preview matching live sessions without killing them.")
    add_json(kill_tree)
    kill_tree.set_defaults(func=cmd_agent_kill_tree)
    reset = a.add_parser("reset", help="Kill all Puppetmaster tmux sessions and clear all registered agents, including root orchestrators.")
    reset.add_argument("--dry-run", action="store_true", help="Preview agents and tmux sessions without killing or clearing them.")
    add_json(reset)
    reset.set_defaults(func=cmd_agent_reset)
    complete = a.add_parser("complete", help="Mark an agent complete, failed, blocked, or cancelled.")
    complete.add_argument("agent_id")
    complete.add_argument("--status", required=True, choices=["success", "failed", "blocked", "cancelled"])
    complete.add_argument("--summary", required=True)
    add_json(complete)
    complete.set_defaults(func=cmd_agent_complete)
    pause = a.add_parser("pause", help="Mark an agent awaiting input.")
    pause.add_argument("agent_id")
    add_json(pause)
    pause.set_defaults(func=cmd_agent_pause)
    resume = a.add_parser("resume", help="Mark an agent running.")
    resume.add_argument("agent_id")
    add_json(resume)
    resume.set_defaults(func=cmd_agent_resume)
    mark = a.add_parser("mark-status", help="Human status override with an audit reason.")
    mark.add_argument("agent_id")
    mark.add_argument("--status", required=True)
    mark.add_argument("--reason", required=True)
    add_json(mark)
    mark.set_defaults(func=cmd_agent_mark_status)
    cleanup = a.add_parser("cleanup-dead", help="Prune dead, killed, or stopped agents from the registry tree while preserving logs.")
    cleanup.add_argument("--root", help="Limit cleanup to one root_id.")
    cleanup.add_argument("--dry-run", action="store_true", help="Preview cleanup without changing registry or tmux.")
    cleanup.add_argument("--kill-stale", action="store_true")
    cleanup.add_argument("--include-roots", action="store_true", help="Allow pruning dead, killed, or stopped root orchestrators.")
    add_json(cleanup)
    cleanup.set_defaults(func=cmd_agent_cleanup_dead)
    cleanup_completed = a.add_parser("cleanup-completed", help="Prune completed, stopped, killed, or dead agents from the registry tree while preserving logs.")
    cleanup_completed.add_argument("--root", help="Limit cleanup to one root_id.")
    cleanup_completed.add_argument("--dry-run", action="store_true", help="Preview cleanup without changing registry or tmux.")
    cleanup_completed.add_argument("--kill-stale", action="store_true", help="Kill live tmux sessions for pruned completed, stopped, killed, or dead agents.")
    cleanup_completed.add_argument("--include-roots", action="store_true", help="Allow pruning completed, stopped, killed, or dead root orchestrators.")
    add_json(cleanup_completed)
    cleanup_completed.set_defaults(func=cmd_agent_cleanup_completed)

    events = sub.add_parser("events", help="Inspect event queues.")
    e = events.add_subparsers(required=True)
    ev_list = e.add_parser("list")
    ev_list.add_argument("--agent")
    ev_list.add_argument("--limit", type=int, default=50)
    add_json(ev_list)
    ev_list.set_defaults(func=cmd_events_list)
    pending = e.add_parser("pending")
    pending.add_argument("agent_id")
    pending.add_argument("--limit", type=int)
    add_json(pending)
    pending.set_defaults(func=cmd_events_pending)
    ack = e.add_parser("ack")
    ack.add_argument("delivery_id")
    add_json(ack)
    ack.set_defaults(func=cmd_events_ack)

    wakeup = sub.add_parser("wakeup", help="Manage scheduled agent wakeups.")
    w = wakeup.add_subparsers(required=True)
    fire_due = w.add_parser("fire-due", help="Fire due scheduled wakeups.")
    fire_due.add_argument("--agent")
    add_json(fire_due)
    fire_due.set_defaults(func=cmd_wakeup_fire_due)
    fire = w.add_parser("fire", help="Fire one scheduled wakeup.")
    fire.add_argument("wakeup_id")
    add_json(fire)
    fire.set_defaults(func=cmd_wakeup_fire)
    list_w = w.add_parser("list", help="List scheduled wakeups.")
    list_w.add_argument("--agent")
    list_w.add_argument("--status", choices=["scheduled", "fired", "cancelled"])
    add_json(list_w)
    list_w.set_defaults(func=cmd_wakeup_list)
    sleep_fire = w.add_parser("sleep-and-fire", help="Internal helper used by scheduled wakeups.")
    sleep_fire.add_argument("--wakeup-id", required=True)
    sleep_fire.set_defaults(func=cmd_wakeup_sleep_and_fire)

    hook = sub.add_parser("hook", help="Commands invoked by generated Codex hooks.")
    h = hook.add_subparsers(required=True)
    stop_h = h.add_parser("stop")
    stop_h.add_argument("--agent-id", required=True)
    stop_h.set_defaults(func=cmd_hook_stop)
    drain_h = h.add_parser("drain-events")
    drain_h.add_argument("--agent-id", required=True)
    drain_h.set_defaults(func=cmd_hook_drain)

    mcp = sub.add_parser("mcp", help="MCP server commands.")
    m = mcp.add_subparsers(required=True)
    serve = m.add_parser("serve")
    serve.set_defaults(func=cmd_mcp_serve)

    discord_p = sub.add_parser("discord", help="Discord bot commands.")
    discord_sub = discord_p.add_subparsers(required=True)
    discord_serve = discord_sub.add_parser("serve", help="Run the Discord bot.")
    discord_serve.add_argument(
        "--background",
        action="store_true",
        help="Run the Discord bot as a detached background process.",
    )
    add_json(discord_serve)
    discord_serve.set_defaults(func=cmd_discord_serve)
    discord_status = discord_sub.add_parser("status", help="Show background Discord bot status.")
    add_json(discord_status)
    discord_status.set_defaults(func=cmd_discord_status)
    discord_stop = discord_sub.add_parser("stop", help="Stop the background Discord bot.")
    discord_stop.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for graceful shutdown.")
    add_json(discord_stop)
    discord_stop.set_defaults(func=cmd_discord_stop)

    tui = sub.add_parser("tui", help="Navigate agents and preview live output.")
    tui.add_argument("--root", help="Limit the view to one root_id.")
    tui.add_argument("--refresh", type=float, default=1.0, help="Refresh interval in seconds.")
    tui.add_argument("--lines", type=int, default=120, help="Terminal preview lines to capture.")
    tui.set_defaults(func=cmd_tui)

    doc = sub.add_parser("doctor", help="Check local dependencies and state.")
    doc.add_argument("--deep", action="store_true")
    add_json(doc)
    doc.set_defaults(func=cmd_doctor)

    rec = sub.add_parser("reconcile", help="Reconcile registry status with tmux state.")
    rec.add_argument("--agent")
    rec.add_argument("--root")
    rec.add_argument("--dry-run", action="store_true")
    add_json(rec)
    rec.set_defaults(func=cmd_reconcile)

    debug = sub.add_parser("debug", help="Debug and development commands.")
    d = debug.add_subparsers(required=True)
    raw = d.add_parser("create-raw", help="Create a raw tmux-backed command agent.")
    raw.add_argument("--cwd", required=True)
    raw.add_argument("--description", required=True)
    raw.add_argument("--command", required=True)
    raw.add_argument("--parent")
    raw.add_argument("--name")
    add_json(raw)
    raw.set_defaults(func=cmd_debug_create_raw)
    dbg_tmux = d.add_parser("tmux")
    add_json(dbg_tmux)
    dbg_tmux.set_defaults(func=cmd_debug_tmux)
    dbg_reg = d.add_parser("registry")
    add_json(dbg_reg)
    dbg_reg.set_defaults(func=cmd_debug_registry)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PuppetError as exc:
        if getattr(args, "json", False):
            print(json.dumps(exc.as_dict(), indent=2, sort_keys=True), file=sys.stderr)
        else:
            print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

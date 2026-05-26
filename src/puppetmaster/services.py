from __future__ import annotations

import json
import os
import hashlib
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config, inherited_pythonpath, puppetmaster_subprocess_env
from .errors import PuppetError, require
from .logging import log
from .model import COMPLETION_STATUSES, TERMINAL_STATUSES, json_dumps, new_id, now, validate_cwd
from .registry import Registry
from .tmux import Tmux


WAKEUP_REASON_MAX_LENGTH = 240
INITIAL_PROMPT_SEND_DELAY_SECONDS = 10.0
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
AGENT_ID_FORMAT_HINT = "Agent ids must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63} and must not contain '..'."
COMPLETED_CLEANUP_STATUSES = {"completed", "stopped", "killed", "dead"}
DEAD_CLEANUP_STATUSES = {"stopped", "killed", "dead"}
DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES = 10 * 1024 * 1024
DISCORD_ATTACHMENT_FILENAME_MAX_LENGTH = 1024


def agent_dir(config: Config, agent_id: str) -> Path:
    return config.agents_dir / agent_id


def make_tmux_session(config: Config, agent_id: str) -> str:
    return f"{config.tmux_session_prefix}{agent_id}"


def validate_agent_id(agent_id: str) -> str:
    if not isinstance(agent_id, str) or not AGENT_ID_PATTERN.fullmatch(agent_id) or ".." in agent_id:
        raise PuppetError("invalid_agent_id", f"invalid agent id: {agent_id!r}", AGENT_ID_FORMAT_HINT)
    return agent_id


def ensure_agent_id_available(config: Config, registry: Registry, tmux: Tmux, agent_id: str) -> None:
    validate_agent_id(agent_id)
    if registry.maybe_agent(agent_id):
        raise PuppetError("duplicate_agent_id", f"agent id already exists: {agent_id}", "Choose a different --agent-id.")
    directory = agent_dir(config, agent_id)
    if directory.exists():
        raise PuppetError(
            "duplicate_agent_id",
            f"agent directory already exists for id: {agent_id}",
            f"Remove or inspect {directory} before reusing this id.",
        )
    session = make_tmux_session(config, agent_id)
    if tmux.session_exists(session):
        raise PuppetError(
            "duplicate_agent_id",
            f"tmux session already exists for id {agent_id}: {session}",
            "Stop or rename the existing tmux session before reusing this id.",
        )


def create_agent_record(
    config: Config,
    registry: Registry,
    *,
    cwd: str,
    description: str,
    role: str = "subagent",
    parent_id: str | None = None,
    root_id: str | None = None,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    cwd = validate_cwd(cwd)
    agent_id = validate_agent_id(agent_id) if agent_id is not None else new_id("agt")
    if registry.maybe_agent(agent_id):
        raise PuppetError("duplicate_agent_id", f"agent id already exists: {agent_id}", "Choose a different agent id.")
    parent = registry.maybe_agent(parent_id) if parent_id else None
    depth = (parent["depth"] + 1) if parent else 0
    root_id = root_id or (parent["root_id"] if parent else agent_id)
    directory = agent_dir(config, agent_id)
    if directory.exists():
        raise PuppetError(
            "duplicate_agent_id",
            f"agent directory already exists for id: {agent_id}",
            f"Remove or inspect {directory} before reusing this id.",
        )
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "terminal.log"
    events_path = directory / "events.jsonl"
    return registry.create_agent(
        {
            "id": agent_id,
            "parent_id": parent_id,
            "root_id": root_id,
            "role": role,
            "name": name,
            "description": description,
            "cwd": cwd,
            "tmux_session": make_tmux_session(config, agent_id),
            "status": "starting",
            "depth": depth,
            "log_path": str(log_path),
            "events_path": str(events_path),
            "metadata": metadata or {},
        }
    )


def enforce_create_limits(config: Config, registry: Registry, parent: dict[str, Any] | None) -> None:
    total = registry.count_agents(parent["root_id"] if parent else None)
    require(
        total < config.limits.max_total_agents,
        "limit_exceeded",
        f"Cannot create agent: max_total_agents={config.limits.max_total_agents} is already reached.",
    )
    if parent:
        next_depth = int(parent["depth"]) + 1
        require(
            next_depth <= config.limits.max_depth,
            "limit_exceeded",
            f"Cannot create child agent: max_depth={config.limits.max_depth} would be exceeded by parent {parent['id']}.",
        )
        concurrent_children = sum(1 for child in registry.children(parent["id"]) if child["status"] not in TERMINAL_STATUSES)
        require(
            concurrent_children < config.limits.max_concurrent_children_per_agent,
            "limit_exceeded",
            f"Cannot create child agent: parent {parent['id']} already has max_concurrent_children_per_agent={config.limits.max_concurrent_children_per_agent}.",
        )


def create_raw_agent(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    cwd: str,
    description: str,
    command: str,
    parent_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    parent = registry.maybe_agent(parent_id) if parent_id else None
    enforce_create_limits(config, registry, parent)
    log(config, "info", "agent.create.requested", "Creating raw agent", parent_id=parent_id)
    agent = create_agent_record(
        config,
        registry,
        cwd=cwd,
        description=description,
        role="subagent" if parent else "orchestrator",
        parent_id=parent_id,
        name=name,
        metadata={"runtime": "raw", "command": command},
    )
    try:
        tmux.create_session(agent["tmux_session"], agent["cwd"], command)
        tmux.pipe_pane(agent["tmux_session"], agent["log_path"])
        agent = registry.update_agent(agent["id"], status="running", started_at=now())
        registry.append_event(agent["id"], "agent.started", "Raw agent started.", {"command": command})
        return agent
    except PuppetError as exc:
        registry.update_agent(agent["id"], status="failed", termination_reason=exc.message)
        registry.append_event(agent["id"], "agent.failed", exc.message, exc.as_dict(), severity="error")
        raise


def read_log_file(path: str, lines: int) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    if lines <= 0:
        return ""
    chunks: list[bytes] = []
    newline_count = 0
    chunk_size = 64 * 1024
    with p.open("rb") as fh:
        fh.seek(0, 2)
        remaining = fh.tell()
        while remaining > 0 and newline_count <= lines:
            size = min(chunk_size, remaining)
            remaining -= size
            fh.seek(remaining)
            chunk = fh.read(size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    data = b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def read_agent(config: Config, registry: Registry, tmux: Tmux, agent_id: str, lines: int | None, source: str = "auto") -> str:
    agent = registry.get_agent(agent_id)
    line_count = min(int(lines or config.limits.default_log_lines), config.limits.max_log_read_lines)
    if source not in {"auto", "log", "tmux"}:
        raise PuppetError("invalid_source", f"invalid source: {source}", "Use auto, log, or tmux.")
    if source in {"auto", "tmux"} and tmux.session_exists(agent["tmux_session"]):
        try:
            return tmux.capture_pane(agent["tmux_session"], line_count)
        except PuppetError:
            if source == "tmux":
                raise
    return read_log_file(agent["log_path"], line_count)


def inspect_agent(config: Config, registry: Registry, tmux: Tmux, agent_id: str) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    children = registry.children(agent_id)
    events = registry.list_events(agent_id, limit=10)
    pending = registry.pending_deliveries(agent_id, limit=10)
    tmux_exists = tmux.session_exists(agent["tmux_session"])
    return {
        "agent": agent,
        "tmux_exists": tmux_exists,
        "attach_command": tmux.attach_command(agent["tmux_session"]),
        "children": children,
        "recent_events": events,
        "pending_deliveries": pending,
        "recent_output": read_agent(config, registry, tmux, agent_id, config.limits.default_log_lines, "auto"),
    }


def stop_agent(
    registry: Registry,
    tmux: Tmux,
    agent_id: str,
    source: str = "human_cli",
    config: Config | None = None,
) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    tmux.stop_session(agent["tmux_session"])
    updated = registry.update_agent(agent_id, status="stopped", stopped_at=now(), termination_reason="stopped")
    notify_agent_state_change(
        registry,
        agent_id,
        "agent.stopped",
        "Agent stopped.",
        severity="warning",
        source=source,
        config=config,
        tmux=tmux,
    )
    return updated


def kill_agent(
    registry: Registry,
    tmux: Tmux,
    agent_id: str,
    source: str = "human_cli",
    config: Config | None = None,
) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    tmux.kill_session(agent["tmux_session"])
    updated = registry.update_agent(agent_id, status="killed", stopped_at=now(), termination_reason="killed")
    notify_agent_state_change(
        registry,
        agent_id,
        "agent.killed",
        "Agent killed.",
        severity="warning",
        source=source,
        config=config,
        tmux=tmux,
    )
    return updated


def kill_root_tree(
    registry: Registry,
    tmux: Tmux,
    root_agent_id: str,
    *,
    source: str = "human_cli",
    dry_run: bool = False,
) -> dict[str, Any]:
    root = registry.get_agent(root_agent_id)
    if root["role"] != "orchestrator" or root["id"] != root["root_id"]:
        raise PuppetError(
            "invalid_agent",
            f"agent is not a root orchestrator: {root_agent_id}",
            "Pass the root orchestrator id whose tmux tree should be killed.",
        )

    agents = sorted(registry.list_agents(root_id=root_agent_id, include_dead=True), key=lambda item: int(item["depth"]), reverse=True)
    killed: list[str] = []
    not_live: list[str] = []
    for agent in agents:
        if not tmux.session_exists(agent["tmux_session"]):
            not_live.append(agent["id"])
            continue
        if not dry_run:
            tmux.kill_session(agent["tmux_session"])
            registry.update_agent(agent["id"], status="killed", stopped_at=now(), termination_reason="killed")
            registry.append_event(
                agent["id"],
                "agent.killed",
                "Agent killed by root tree command.",
                {"root_agent_id": root_agent_id, "tmux_session": agent["tmux_session"]},
                severity="warning",
                source=source,
            )
        killed.append(agent["id"])

    return {
        "root_agent_id": root_agent_id,
        "dry_run": dry_run,
        "candidates": agents,
        "killed": killed if not dry_run else [],
        "would_kill": killed if dry_run else [],
        "not_live": not_live,
    }


def reset_agents(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    source: str = "human_cli",
    dry_run: bool = False,
) -> dict[str, Any]:
    agents = sorted(registry.list_agents(include_dead=True), key=lambda item: int(item["depth"]), reverse=True)
    registered_sessions = {agent["tmux_session"] for agent in agents}
    live_sessions = {item["session"] for item in tmux.list_sessions(config.tmux_session_prefix)}
    live_registered = [agent for agent in agents if agent["tmux_session"] in live_sessions]
    unmanaged_sessions = sorted(live_sessions - registered_sessions)
    sessions_to_kill = [agent["tmux_session"] for agent in live_registered] + unmanaged_sessions

    if not dry_run:
        for session in sessions_to_kill:
            tmux.kill_session(session)
        counts = registry.clear_agent_state()
    else:
        counts = {
            "scheduled_wakeups": 0,
            "event_deliveries": 0,
            "events": 0,
            "outbound_human_messages": 0,
            "discord_channel_bindings": 0,
            "agents": 0,
        }

    return {
        "dry_run": dry_run,
        "source": source,
        "agents": agents,
        "would_clear": [agent["id"] for agent in agents] if dry_run else [],
        "cleared": [agent["id"] for agent in agents] if not dry_run else [],
        "would_kill": sessions_to_kill if dry_run else [],
        "killed": sessions_to_kill if not dry_run else [],
        "unmanaged_tmux": unmanaged_sessions,
        "counts": counts,
        "logs_preserved": True,
        "skills_preserved": True,
    }


def cleanup_completed_agents(
    registry: Registry,
    tmux: Tmux,
    *,
    root_id: str | None = None,
    dry_run: bool = True,
    kill_stale: bool = False,
    include_roots: bool = False,
    cleanup_statuses: set[str] | None = None,
) -> dict[str, Any]:
    agents = registry.list_agents(root_id=root_id)
    by_id = {agent["id"]: agent for agent in agents}
    cleanup_statuses = cleanup_statuses or COMPLETED_CLEANUP_STATUSES
    cleanup_ids = {
        agent["id"]
        for agent in agents
        if agent["status"] in cleanup_statuses and (include_roots or agent["parent_id"] is not None)
    }
    prunable = {
        agent_id
        for agent_id in cleanup_ids
        if all(descendant_id in cleanup_ids for descendant_id in registry.descendants(agent_id) if descendant_id in by_id)
    }
    pruned = sorted(prunable, key=lambda agent_id: int(by_id[agent_id]["depth"]), reverse=True)
    skipped = [
        {
            "agent_id": agent_id,
            "reason": "has non-cleanable descendants",
        }
        for agent_id in sorted(cleanup_ids - prunable)
    ]
    killed = []
    for agent_id in pruned:
        agent = by_id[agent_id]
        if kill_stale and tmux.session_exists(agent["tmux_session"]):
            if not dry_run:
                tmux.kill_session(agent["tmux_session"])
            killed.append(agent_id)
    if not dry_run:
        registry.delete_agents(pruned)
    return {
        "dry_run": dry_run,
        "root_id": root_id,
        "candidates": [by_id[agent_id] for agent_id in pruned],
        "pruned": pruned if not dry_run else [],
        "would_prune": pruned if dry_run else [],
        "killed": killed if not dry_run else [],
        "would_kill": killed if dry_run else [],
        "skipped": skipped,
        "logs_preserved": True,
    }


def prompt_agent(registry: Registry, tmux: Tmux, agent_id: str, prompt: str, source: str = "human_cli") -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    if not tmux.session_exists(agent["tmux_session"]):
        raise PuppetError("invalid_state", f"agent session is not live: {agent_id}", "Use agent read to inspect logs.")
    tmux.send_prompt(agent["tmux_session"], prompt)
    event = registry.append_event(agent_id, "agent.prompted", "Prompt delivered.", {"prompt_length": len(prompt)}, source=source)
    if agent["status"] in {"idle", "awaiting_input", "unknown"}:
        registry.update_agent(agent_id, status="running")
    return event


def _human_message_attachment(agent: dict[str, Any], file_path: str | None, filename: str | None) -> dict[str, Any] | None:
    raw_path = (file_path or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(agent["cwd"]) / path
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise PuppetError("file_not_found", f"attachment file not found: {raw_path}", "Pass a path to an existing regular file.") from None
    if not resolved.is_file():
        raise PuppetError("not_a_file", f"attachment path is not a file: {raw_path}", "Pass a path to a regular file, not a directory.")
    size = resolved.stat().st_size
    if size > DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES:
        limit_mib = DISCORD_DEFAULT_ATTACHMENT_LIMIT_BYTES // (1024 * 1024)
        raise PuppetError(
            "file_too_large",
            f"attachment is {size} bytes, above Discord's default {limit_mib} MiB upload limit.",
            "Send a smaller file, compress it, or share a link instead.",
        )
    display_name = (filename or resolved.name).strip()
    if not display_name:
        raise PuppetError("filename_required", "attachment filename must be non-empty.")
    if "/" in display_name or "\\" in display_name or Path(display_name).name != display_name:
        raise PuppetError("invalid_filename", "attachment filename must not contain path separators.", "Pass only a display filename like report.png.")
    if len(display_name) > DISCORD_ATTACHMENT_FILENAME_MAX_LENGTH:
        raise PuppetError(
            "filename_too_long",
            f"attachment filename is longer than Discord's {DISCORD_ATTACHMENT_FILENAME_MAX_LENGTH} character limit.",
        )
    return {"path": str(resolved), "filename": display_name, "size": size}


def send_human_message(
    registry: Registry,
    agent_id: str,
    message: str,
    *,
    file_path: str | None = None,
    filename: str | None = None,
    source: str = "mcp_tool",
) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    root_id = agent["root_id"]
    normalized = (message or "").strip()
    attachment = _human_message_attachment(agent, file_path, filename)
    if not normalized and attachment is None:
        raise PuppetError("message_required", "send_human_message requires a non-empty message or file_path.", "Pass human-facing reply text or a file to attach.")
    binding = registry.discord_binding_for_root(root_id)
    if not binding:
        raise PuppetError(
            "no_human_channel",
            "No Discord channel is bound for this root orchestrator.",
            "Bind a Discord channel to this root orchestrator before sending human messages.",
        )
    outbound = registry.enqueue_outbound_human_message(
        root_id,
        agent_id,
        "discord",
        binding["channel_id"],
        normalized,
        attachment_path=attachment["path"] if attachment else None,
        attachment_filename=attachment["filename"] if attachment else None,
        attachment_size=attachment["size"] if attachment else None,
    )
    payload = {
        "message_id": outbound["id"],
        "transport": outbound["transport"],
        "channel_id": outbound["channel_id"],
        "message_length": len(normalized),
    }
    if attachment is not None:
        payload["attachment_filename"] = attachment["filename"]
        payload["attachment_size"] = attachment["size"]
    registry.append_event(
        agent_id,
        "human.message.queued",
        "Human message queued.",
        payload,
        source=source,
    )
    result = {
        "queued": True,
        "id": outbound["id"],
        "transport": outbound["transport"],
        "channel_id": outbound["channel_id"],
    }
    if attachment is not None:
        result["attachment"] = {
            "filename": attachment["filename"],
            "size": attachment["size"],
        }
    return result


def notify_agent_state_change(
    registry: Registry,
    agent_id: str,
    event_type: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    *,
    severity: str = "info",
    source: str = "supervisor",
    coalesce: bool = False,
    config: Config | None = None,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    event = registry.append_event(agent_id, event_type, summary, payload, severity=severity, source=source)
    recipients = []
    if agent.get("parent_id"):
        recipients.append(agent["parent_id"])
    if agent["root_id"] not in recipients and agent["root_id"] != agent_id:
        recipients.append(agent["root_id"])
    for recipient in recipients:
        registry.queue_delivery(event["id"], recipient, coalesce=coalesce)
    injected = False
    if config and tmux:
        for recipient in recipients:
            injected = inject_pending_prompt(config, registry, tmux, recipient) or injected
    return {"event": event, "deliveries": recipients, "injected": injected}


def pause_agent(
    registry: Registry,
    agent_id: str,
    *,
    source: str = "human_cli",
    config: Config | None = None,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    updated = registry.update_agent(agent_id, status="awaiting_input")
    notify_agent_state_change(
        registry,
        agent_id,
        "agent.paused",
        "Agent marked awaiting input.",
        source=source,
        config=config,
        tmux=tmux,
    )
    return updated


def resume_agent(
    registry: Registry,
    agent_id: str,
    *,
    source: str = "human_cli",
    config: Config | None = None,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    updated = registry.update_agent(agent_id, status="running")
    notify_agent_state_change(
        registry,
        agent_id,
        "agent.resumed",
        "Agent marked running.",
        source=source,
        config=config,
        tmux=tmux,
    )
    return updated


def complete_agent(
    registry: Registry,
    agent_id: str,
    *,
    status: str,
    summary: str,
    result: str | None = None,
    files_changed: list[str] | None = None,
    next_steps: list[str] | None = None,
    source: str = "mcp_tool",
    config: Config | None = None,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    if status not in COMPLETION_STATUSES:
        raise PuppetError("invalid_status", f"invalid completion status: {status}", "Use success, failed, blocked, or cancelled.")
    agent_status = {"success": "completed", "failed": "failed", "blocked": "blocked", "cancelled": "stopped"}[status]
    severity = {"success": "info", "failed": "error", "blocked": "warning", "cancelled": "warning"}[status]
    event_type = {"success": "agent.completed", "failed": "agent.failed", "blocked": "agent.blocked", "cancelled": "agent.cancelled"}[status]
    payload = {
        "completion_status": status,
        "result": result,
        "files_changed": files_changed or [],
        "next_steps": next_steps or [],
    }
    updated = registry.update_agent(
        agent_id,
        status=agent_status,
        completion_status=status,
        completed_at=now(),
        metadata_json=json_dumps({**agent_metadata(registry.get_agent(agent_id)), "completion": {"summary": summary, **payload}}),
    )
    notification = notify_agent_state_change(
        registry,
        agent_id,
        event_type,
        summary,
        payload,
        severity=severity,
        source=source,
        config=config,
        tmux=tmux,
    )
    return {"agent": updated, **notification}


def agent_metadata(agent: dict[str, Any]) -> dict[str, Any]:
    return dict(agent.get("metadata") or {})


def utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_timestamp(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def normalize_wait_reason(reason: str | None) -> str:
    return (reason or "").strip()[:WAKEUP_REASON_MAX_LENGTH]


def schedule_wakeup(
    config: Config,
    registry: Registry,
    agent_id: str,
    seconds: int,
    reason: str | None = None,
    *,
    spawn_helper: bool = True,
) -> dict[str, Any]:
    if seconds <= 0:
        raise PuppetError("invalid_wait", "wait seconds must be positive.")
    if seconds > config.limits.max_wait_seconds:
        raise PuppetError(
            "invalid_wait",
            f"wait seconds must be <= max_wait_seconds={config.limits.max_wait_seconds}.",
        )
    wakeup = registry.create_wakeup(
        agent_id,
        utc_after(seconds),
        normalize_wait_reason(reason),
        {"seconds": seconds},
    )
    if spawn_helper:
        spawn_wakeup_helper(config, wakeup["id"])
    return wakeup


def spawn_wakeup_helper(config: Config, wakeup_id: str) -> None:
    subprocess.Popen(
        [sys.executable, "-m", "puppetmaster.cli", "wakeup", "sleep-and-fire", "--wakeup-id", wakeup_id],
        env=puppetmaster_subprocess_env(config),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def fire_wakeup(
    config: Config,
    registry: Registry,
    wakeup_id: str,
    *,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    wakeup = registry.mark_wakeup_fired(wakeup_id)
    if not wakeup:
        return {"fired": False, "wakeup": registry.get_wakeup(wakeup_id)}
    payload = {
        "wakeup_id": wakeup["id"],
        "seconds": wakeup.get("payload", {}).get("seconds"),
        "reason": wakeup["reason"],
    }
    event = registry.append_event(wakeup["agent_id"], "agent.wait_over", "Wait over.", payload, source="wakeup")
    registry.queue_delivery(event["id"], wakeup["agent_id"])
    injected = inject_pending_prompt(config, registry, tmux, wakeup["agent_id"]) if tmux else False
    return {"fired": True, "wakeup": wakeup, "event": event, "deliveries": [wakeup["agent_id"]], "injected": injected}


def fire_due_wakeups(
    config: Config,
    registry: Registry,
    *,
    agent_id: str | None = None,
    tmux: Tmux | None = None,
) -> dict[str, Any]:
    due = registry.list_wakeups(agent_id=agent_id, status="scheduled", due_at=now())
    fired = [fire_wakeup(config, registry, wakeup["id"], tmux=tmux) for wakeup in due]
    return {"fired": fired, "count": sum(1 for item in fired if item.get("fired"))}


def sleep_and_fire_wakeup(config: Config, registry: Registry, tmux: Tmux, wakeup_id: str) -> dict[str, Any]:
    wakeup = registry.get_wakeup(wakeup_id)
    if wakeup["status"] == "scheduled":
        time.sleep(max(0.0, utc_timestamp(wakeup["wake_at"]) - time.time()))
    return fire_wakeup(config, registry, wakeup_id, tmux=tmux)


def handle_stop_hook(registry: Registry, agent_id: str, hook_input: str, source: str = "codex_hook") -> dict[str, Any]:
    try:
        payload = json.loads(hook_input) if hook_input.strip() else {}
    except json.JSONDecodeError as exc:
        event = registry.append_event(
            agent_id,
            "hook.failed",
            f"Stop hook received invalid JSON: {exc}",
            {"raw": hook_input[:4000]},
            severity="warning",
            source=source,
        )
        return {"event": event, "ok": False}
    agent = registry.get_agent(agent_id)
    if agent["status"] in TERMINAL_STATUSES:
        event = registry.append_event(agent_id, "agent.turn_stopped", "Agent turn stopped.", payload, source=source)
        return {"event": event, "ok": True}
    registry.update_agent(agent_id, status="idle", last_turn_stopped_at=now())
    notification = notify_agent_state_change(
        registry,
        agent_id,
        "agent.turn_stopped",
        "Agent turn stopped.",
        payload,
        source=source,
        coalesce=True,
    )
    return {"event": notification["event"], "ok": True}


def format_event_prompt(registry: Registry, deliveries: list[dict[str, Any]], remaining: int) -> str:
    if len(deliveries) == 1 and deliveries[0]["type"] == "agent.wait_over":
        header = "PUPPETMASTER WAIT OVER"
    else:
        header = "PUPPETMASTER EVENT" if len(deliveries) == 1 else "PUPPETMASTER EVENTS"
    parts = [header]
    for index, delivery in enumerate(deliveries, 1):
        agent = registry.get_agent(delivery["agent_id"])
        payload = delivery.get("payload") or {}
        if delivery["type"] == "agent.wait_over":
            lines = [
                f"Wait over for agent {agent['id']}.",
                f"Reason: {payload.get('reason') or '-'}",
                f"Elapsed seconds: {payload.get('seconds') or '-'}",
            ]
            if len(deliveries) > 1:
                lines[0] = f"{index}. {lines[0]}"
            parts.append("\n".join(lines))
            continue
        summary = delivery.get("summary", "")
        if len(summary) > 600:
            summary = summary[:597] + "..."
        parts.append(
            "\n".join(
                [
                    f"{index}. {agent['id']} has new state {agent['status']}.",
                    f"Name: {agent.get('name') or '-'}",
                    f"Status: {agent['status']}",
                    f"Cwd: {agent['cwd']}",
                    f"Summary: {summary}",
                    "Available actions:",
                    f'- inspect_agent({{"agent_id":"{agent["id"]}"}})',
                    f'- read_agent({{"agent_id":"{agent["id"]}","lines":120}})',
                    f'- prompt_agent({{"agent_id":"{agent["id"]}","prompt":"..."}})',
                    f'- stop_agent({{"agent_id":"{agent["id"]}"}})',
                    f'- kill_agent({{"agent_id":"{agent["id"]}"}}) after final output is consumed and the agent is no longer useful',
                ]
            )
        )
    if remaining:
        parts.append(f"{remaining} more events remain queued. Call list_agents or inspect_agent if needed.")
    return "\n\n".join(parts)


def drain_events(config: Config, registry: Registry, agent_id: str) -> dict[str, Any]:
    fire_due_wakeups(config, registry, agent_id=agent_id)
    deliveries = registry.pending_deliveries(agent_id, limit=config.limits.max_event_prompt_events)
    if not deliveries:
        return {"continue": True}
    all_pending = registry.pending_deliveries(agent_id, limit=None)
    remaining = max(0, len(all_pending) - len(deliveries))
    prompt = format_event_prompt(registry, deliveries, remaining)
    registry.mark_delivered([delivery["id"] for delivery in deliveries])
    return {"decision": "block", "reason": prompt}


def maybe_inject_event_prompt(config: Config, registry: Registry, tmux: Tmux, root_agent_id: str) -> bool:
    return inject_pending_prompt(config, registry, tmux, root_agent_id)


def inject_pending_prompt(config: Config, registry: Registry, tmux: Tmux, recipient_agent_id: str) -> bool:
    fire_due_wakeups(config, registry, agent_id=recipient_agent_id)
    recipient = registry.maybe_agent(recipient_agent_id)
    if not recipient:
        return False
    if not tmux.session_exists(recipient["tmux_session"]):
        return False
    deliveries = registry.pending_deliveries(recipient_agent_id, limit=config.limits.max_event_prompt_events)
    if not deliveries:
        return False
    all_pending = registry.pending_deliveries(recipient_agent_id, limit=None)
    prompt = format_event_prompt(registry, deliveries, max(0, len(all_pending) - len(deliveries)))
    tmux.send_prompt(recipient["tmux_session"], prompt)
    registry.mark_delivered([delivery["id"] for delivery in deliveries])
    if recipient["status"] not in TERMINAL_STATUSES:
        registry.update_agent(recipient_agent_id, status="running")
    registry.append_event(
        recipient_agent_id,
        "orchestrator.event_prompt_injected",
        "Pending Puppetmaster events were injected into the agent tmux session.",
        {"delivery_ids": [delivery["id"] for delivery in deliveries]},
        source="supervisor",
    )
    return True


def discover_codex() -> dict[str, Any]:
    path = shutil.which("codex")
    if not path:
        raise PuppetError("codex_missing", "codex executable not found", "Install Codex CLI or add it to PATH.")
    version = subprocess.run([path, "--version"], text=True, capture_output=True)
    help_out = subprocess.run([path, "--help"], text=True, capture_output=True)
    supported = help_out.stdout + help_out.stderr
    missing = [flag for flag in ("--no-alt-screen", "--dangerously-bypass-approvals-and-sandbox", "--cd") if flag not in supported]
    if missing:
        raise PuppetError("codex_flag_missing", f"codex is missing required flags: {', '.join(missing)}")
    return {"path": path, "version": version.stdout.strip() or version.stderr.strip(), "required_flags": "ok"}


def prompt_text(agent: dict[str, Any], user_prompt: str) -> str:
    orchestration = ""
    human_message_tools = ""
    if agent["role"] == "orchestrator":
        human_message_tools = """
- Always use send_human_message for every human-facing message, including answers, status updates, readiness notices, and blockers.
- To attach one local file to a human-facing Discord reply, pass file_path and optional filename to send_human_message. Relative paths resolve from this agent's workspace. Files over Discord's default 10 MiB upload limit are rejected.
- Regularly update the human with send_human_message during longer work, after meaningful progress, and before waiting on child agents or external events.
"""
        orchestration = """
Orchestrator event loop:
- Your primary role is orchestration: break down work, delegate execution, monitor progress, integrate results, and keep the human informed.
- Do small, low-risk tasks yourself when delegation would add more overhead than value.
- Delegate larger, multi-step, research-heavy, test-heavy, or parallelizable tasks to Puppetmaster child agents with create_agent().
- Use list_subagent_skills() to discover built-in subagent skill templates, and pass create_agent(skill="subagent-...") when a listed skill matches the delegated role.
- Do not take on the main implementation or investigation yourself when a task is large enough for child-agent execution.
- Do not use Codex's default spawn_agent tool for Puppetmaster child-agent delegation; those agents are outside the Puppetmaster event loop.
- When a child completes, blocks, fails, stops, is killed, or finishes a turn, Puppetmaster queues a state-change event for you.
- When a child is complete, failed, cancelled, or otherwise no longer useful, inspect/read any final output you need, then call kill_agent(child_id) to close its tmux session and prevent stale Codex processes from accumulating.
- Do not kill a child you still expect to prompt, resume, or inspect for more work.
- If you are only waiting for child-agent progress, do not call wait(). Simply end your turn. Puppetmaster will send you a fresh PUPPETMASTER EVENT message when a subagent changes state.
- Call wait(seconds, reason) only when you need a time-based wakeup, such as polling after a backoff or checking something again at a specific interval.
- The wait tool does not sleep inside the tool call. It schedules a durable wakeup and returns immediately; after calling it, end your turn.
- When a wait expires, Puppetmaster sends you a PUPPETMASTER WAIT OVER message.
- After any PUPPETMASTER EVENT or PUPPETMASTER WAIT OVER message, inspect or read the relevant agent if you need more context before deciding the next action.
- When you receive a DISCORD MESSAGE RECEIVED prompt, always answer the human by calling send_human_message. Do not include Discord channel ids or transport details.
"""
    return f"""You are a Puppetmaster-managed Codex agent.

Task:
{user_prompt}

Agent:
- id: {agent['id']}
- parent_id: {agent.get('parent_id') or ''}
- root_id: {agent['root_id']}
- cwd: {agent['cwd']}
- description: {agent['description']}

Puppetmaster tools:
- Use create_agent to delegate work to child agents when that helps the task.
- Use list_subagent_skills to discover role-specific subagent prompt templates for create_agent(skill=...).
- Use inspect_agent and read_agent to understand child state and recent output.
- Use prompt_agent to send follow-up instructions to a live child agent.
- Child agents do not have direct human-message tools. Report results through complete_agent, blockers through complete_agent with status blocked, or ask your parent/root agent to contact the human.
{human_message_tools.rstrip()}
- Use kill_agent after consuming final child output when a child is no longer useful; this closes the tmux session and Codex process. Use stop_agent when you want a gentler interrupt.
- Use wait(seconds, reason) only for a time-based wakeup. End your turn after calling wait.
{orchestration}
Completion:
- When your assigned task is done, call complete_agent with status success and a concise summary.
- If you cannot continue without input, call complete_agent with status blocked and explain what is needed.
- If the task failed, call complete_agent with status failed and summarize the failure.
- A human may attach to this tmux session at any time.
"""


_TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_key(key: str) -> str:
    if _TOML_BARE_KEY.match(key):
        return key
    return json.dumps(key)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{toml_key(str(key))} = {toml_value(item)}" for key, item in value.items())
        return "{ " + items + " }"
    return json.dumps(str(value))


def toml_table_path(parts: list[str]) -> str:
    return ".".join(toml_key(part) for part in parts)


def toml_dumps(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def scalar_items(table: dict[str, Any]) -> list[tuple[str, Any]]:
        return [(key, value) for key, value in table.items() if not isinstance(value, dict)]

    def child_items(table: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        return [(key, value) for key, value in table.items() if isinstance(value, dict)]

    for key, value in scalar_items(data):
        lines.append(f"{toml_key(str(key))} = {toml_value(value)}")
    if lines:
        lines.append("")

    def emit_table(path: list[str], table: dict[str, Any]) -> None:
        scalars = scalar_items(table)
        children = child_items(table)
        if scalars or not children:
            lines.append(f"[{toml_table_path(path)}]")
            for key, value in scalars:
                lines.append(f"{toml_key(str(key))} = {toml_value(value)}")
            lines.append("")
        for key, value in children:
            emit_table([*path, str(key)], value)

    for key, value in child_items(data):
        emit_table([str(key)], value)

    return "\n".join(lines).rstrip() + "\n"


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def user_codex_config(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        return {}
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PuppetError(
            "codex_config_invalid",
            f"failed to parse {config_path}",
            f"Fix the TOML syntax in {config_path}: {exc}",
        ) from exc


def write_codex_files(config: Config, agent: dict[str, Any], user_prompt: str, orchestrator: bool = False) -> dict[str, str]:
    directory = agent_dir(config, agent["id"])
    codex_home = directory / "codex-config"
    hooks_dir = codex_home / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = directory / "initial-prompt.md"
    prompt_path.write_text(prompt_text(agent, user_prompt), encoding="utf-8")
    stop_hook = hooks_dir / "stop-hook"
    hook_command = "drain-events" if orchestrator else "stop"
    stop_hook.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
exec {sys.executable} -m puppetmaster.cli hook {hook_command} --agent-id "${{PUPPETMASTER_AGENT_ID}}"
""",
        encoding="utf-8",
    )
    stop_hook.chmod(0o755)
    env = {
        "PUPPETMASTER_AGENT_ID": agent["id"],
        "PUPPETMASTER_PARENT_AGENT_ID": agent.get("parent_id") or "",
        "PUPPETMASTER_ROOT_AGENT_ID": agent["root_id"],
        "PUPPETMASTER_STATE_DIR": str(config.state_dir),
        "PUPPETMASTER_CODEX_HOME": str(config.codex_home),
        "PUPPETMASTER_CONFIG_DIR": str(codex_home),
        "PUPPETMASTER_ROLE": agent["role"],
    }
    pythonpath = inherited_pythonpath()
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    hooks_json = codex_home / "hooks.json"
    hook_hash = codex_hook_trust_hash(str(stop_hook), "Reporting Puppetmaster turn stop", 30)
    config_toml = codex_home / "config.toml"
    generated_codex_config = {
        "features": {"hooks": True},
        "hooks": {"state": {f"{hooks_json}:stop:0:0": {"trusted_hash": hook_hash}}},
        "projects": {agent["cwd"]: {"trust_level": "trusted"}},
        "mcp_servers": {
            "puppetmaster": {
                "command": sys.executable,
                "args": ["-m", "puppetmaster.cli", "mcp", "serve"],
                "env": env,
            }
        },
    }
    config_toml.write_text(
        toml_dumps(deep_merge(user_codex_config(config.codex_home), generated_codex_config)),
        encoding="utf-8",
    )
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(stop_hook),
                                    "timeout": 30,
                                    "statusMessage": "Reporting Puppetmaster turn stop",
                                }
                            ]
                        }
                    ]
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    auth_json = config.codex_home / "auth.json"
    if auth_json.exists() and not (codex_home / "auth.json").exists():
        try:
            (codex_home / "auth.json").symlink_to(auth_json)
        except FileExistsError:
            pass
    launch = directory / "launch.sh"
    codex = discover_codex()["path"]
    flags = ["--cd", agent["cwd"]]
    if config.codex_no_alt_screen:
        flags.insert(0, "--no-alt-screen")
    if config.codex_bypass_approvals_and_sandbox:
        flags.insert(1, "--dangerously-bypass-approvals-and-sandbox")
    pythonpath_export = f"export PYTHONPATH={pythonpath!r}\n" if pythonpath is not None else ""
    launch.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
export PUPPETMASTER_AGENT_ID={agent['id']!r}
export PUPPETMASTER_PARENT_AGENT_ID={agent.get('parent_id') or ''}
export PUPPETMASTER_ROOT_AGENT_ID={agent['root_id']!r}
export PUPPETMASTER_STATE_DIR={str(config.state_dir)!r}
export PUPPETMASTER_CODEX_HOME={str(config.codex_home)!r}
export PUPPETMASTER_CONFIG_DIR={str(codex_home)!r}
export PUPPETMASTER_ROLE={agent['role']!r}
{pythonpath_export}\
export CODEX_HOME={str(codex_home)!r}
exec {codex!r} {' '.join(shlex_quote(flag) for flag in flags)}
""",
        encoding="utf-8",
    )
    launch.chmod(0o755)
    return {
        "prompt": str(prompt_path),
        "launch": str(launch),
        "config": str(config_toml),
        "hook": str(stop_hook),
        "codex_home": str(codex_home),
        "source_codex_home": str(config.codex_home),
    }


def codex_hook_trust_hash(command: str, status_message: str | None, timeout: int) -> str:
    handler: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": timeout,
        "async": False,
    }
    if status_message is not None:
        handler["statusMessage"] = status_message
    identity = {"event_name": "stop", "hooks": [handler]}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def create_codex_agent(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    cwd: str,
    description: str,
    prompt: str,
    parent_id: str | None = None,
    name: str | None = None,
    role: str = "subagent",
    metadata: dict[str, Any] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    parent = registry.maybe_agent(parent_id) if parent_id else None
    enforce_create_limits(config, registry, parent)
    if agent_id is not None:
        ensure_agent_id_available(config, registry, tmux, agent_id)
    discover_codex()
    agent = create_agent_record(
        config,
        registry,
        cwd=cwd,
        description=description,
        role=role,
        parent_id=parent_id,
        root_id=parent["root_id"] if parent else None,
        name=name,
        metadata={"runtime": "codex", **(metadata or {})},
        agent_id=agent_id,
    )
    files = write_codex_files(config, agent, prompt, orchestrator=role == "orchestrator")
    agent = registry.update_agent(
        agent["id"],
        initial_prompt_path=files["prompt"],
        metadata_json=json_dumps({**agent_metadata(agent), "generated_files": files}),
    )
    try:
        tmux.create_session(agent["tmux_session"], agent["cwd"], files["launch"])
        tmux.pipe_pane(agent["tmux_session"], agent["log_path"])
        time.sleep(INITIAL_PROMPT_SEND_DELAY_SECONDS)
        tmux.send_prompt(agent["tmux_session"], Path(files["prompt"]).read_text(encoding="utf-8"))
        updated = registry.update_agent(agent["id"], status="running", started_at=now())
        registry.append_event(agent["id"], "agent.started", "Codex agent started.", {"files": files})
        return updated
    except PuppetError as exc:
        registry.update_agent(agent["id"], status="failed", termination_reason=exc.message)
        registry.append_event(agent["id"], "agent.failed", exc.message, exc.as_dict(), severity="error")
        raise


def start_orchestrator(
    config: Config,
    registry: Registry,
    tmux: Tmux,
    *,
    cwd: str,
    prompt: str,
    name: str | None = "root",
    new_root: bool = False,
    agent_id: str | None = None,
    goal: bool = False,
    codex_home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    config = config.with_codex_home(codex_home) if codex_home is not None else config
    task = f"/goal {prompt.strip()}" if goal and prompt.strip() else prompt
    return create_codex_agent(
        config,
        registry,
        tmux,
        cwd=cwd,
        description="Root Puppetmaster orchestrator",
        prompt=task,
        parent_id=None,
        name=name,
        role="orchestrator",
        agent_id=agent_id,
    )


def reconcile(config: Config, registry: Registry, tmux: Tmux, agent_id: str | None = None, root_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    agents = [registry.get_agent(agent_id)] if agent_id else registry.list_agents(root_id=root_id)
    changes = []
    tmux_names = {item["session"] for item in tmux.list_sessions(config.tmux_session_prefix)}
    for agent in agents:
        exists = agent["tmux_session"] in tmux_names
        proposed = None
        reason = None
        if agent["status"] in {"starting", "running", "idle", "awaiting_input", "unknown"} and not exists:
            proposed = "dead"
            reason = "registered nonterminal agent has no tmux session"
        elif agent["status"] == "running":
            stopped = registry.latest_event_time(agent["id"], "agent.turn_stopped")
            prompted = registry.latest_event_time(agent["id"], "agent.prompted")
            if stopped and (not prompted or stopped > prompted):
                proposed = "idle"
                reason = "Stop hook is newer than latest prompt"
        if proposed and proposed != agent["status"]:
            changes.append({"agent_id": agent["id"], "from": agent["status"], "to": proposed, "reason": reason})
            if not dry_run:
                registry.update_agent(agent["id"], status=proposed)
                notify_agent_state_change(
                    registry,
                    agent["id"],
                    "agent.dead_detected" if proposed == "dead" else "supervisor.reconciled",
                    reason or "Reconciled agent status.",
                    {"from": agent["status"], "to": proposed, "tmux_exists": exists},
                    severity="warning" if proposed == "dead" else "info",
                    coalesce=proposed != "dead",
                    config=config,
                    tmux=tmux,
                )
    unmanaged_tmux = [name for name in tmux_names if not any(agent["tmux_session"] == name for agent in agents)]
    return {"changes": changes, "unmanaged_tmux": unmanaged_tmux, "dry_run": dry_run}


def doctor(config: Config, registry: Registry, tmux: Tmux, deep: bool = False) -> dict[str, Any]:
    checks = []
    ok = True

    def add(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        checks.append({"name": name, "ok": passed, "detail": detail})

    add("state_dir_writable", os.access(config.state_dir, os.W_OK), str(config.state_dir))
    try:
        tmux_path = tmux.require_tmux()
        version = subprocess.run(["tmux", "-V"], text=True, capture_output=True).stdout.strip()
        add("tmux", True, f"{tmux_path} {version}")
    except PuppetError as exc:
        add("tmux", False, exc.message)
    try:
        codex = discover_codex()
        add("codex", True, f"{codex['path']} {codex['version']}")
    except PuppetError as exc:
        add("codex", False, exc.message)
    with registry.connect() as conn:
        row = conn.execute("select version from schema_version order by version desc limit 1").fetchone()
        add("registry_schema", bool(row and row["version"] == 1), f"version={row['version'] if row else 'missing'}")
    if deep:
        rec = reconcile(config, registry, tmux, dry_run=True)
        add("registry_tmux_consistency", not rec["changes"], f"{len(rec['changes'])} proposed changes")
        for agent in registry.list_agents():
            generated = agent.get("metadata", {}).get("generated_files", {})
            hook = generated.get("hook")
            if hook:
                add(f"hook_executable:{agent['id']}", os.access(hook, os.X_OK), hook)
    return {"ok": ok, "checks": checks}

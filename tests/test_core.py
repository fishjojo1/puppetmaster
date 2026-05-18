from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from puppetmaster.config import load_config
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry
import puppetmaster.services as services
from puppetmaster.services import complete_agent, create_agent_record, drain_events, handle_stop_hook, reconcile, write_codex_files
from puppetmaster.tmux import Tmux


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(tmp_path / ".state"))
    cfg = load_config()
    return cfg, Registry(cfg), Tmux(cfg)


def test_registry_create_get_list_and_events(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    assert reg.get_agent(agent["id"])["cwd"] == str(tmp_path)
    assert reg.list_agents()[0]["id"] == agent["id"]
    reg.update_agent(agent["id"], status="running")
    event = reg.append_event(agent["id"], "agent.started", "started")
    assert reg.list_events(agent["id"])[0]["id"] == event["id"]


def test_cwd_validation_rejects_relative(ctx):
    cfg, reg, _tmux = ctx
    with pytest.raises(PuppetError) as exc:
        create_agent_record(cfg, reg, cwd="relative", description="bad")
    assert exc.value.code == "invalid_cwd"


def test_completion_queues_parent_and_root(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", parent_id=root["id"])
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=parent["id"])
    result = complete_agent(reg, child["id"], status="success", summary="done")
    assert result["agent"]["status"] == "completed"
    recipients = {d["recipient_agent_id"] for d in reg.all_deliveries()}
    assert parent["id"] in recipients
    assert root["id"] in recipients


def test_drain_events_marks_delivered(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    complete_agent(reg, child["id"], status="blocked", summary="need input")
    result = drain_events(cfg, reg, root["id"])
    assert result["decision"] == "block"
    assert "PUPPETMASTER EVENT" in result["reason"]
    assert reg.pending_deliveries(root["id"]) == []


def test_stop_hook_marks_idle_and_coalesces(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(child["id"], status="running")
    handle_stop_hook(reg, child["id"], "{}")
    handle_stop_hook(reg, child["id"], "{}")
    assert reg.get_agent(child["id"])["status"] == "idle"
    assert len(reg.pending_deliveries(root["id"])) == 1


def test_reconcile_running_missing_tmux_becomes_dead(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.update_agent(agent["id"], status="running")
    monkeypatch.setattr(tmux, "list_sessions", lambda prefix=None: [])
    result = reconcile(cfg, reg, tmux)
    assert result["changes"][0]["to"] == "dead"
    assert reg.get_agent(agent["id"])["status"] == "dead"


def test_generated_codex_config_shape(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    files = write_codex_files(cfg, agent, "hello", orchestrator=True)
    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    assert data["features"]["hooks"] is True
    state_key = f"{Path(files['config']).parent / 'hooks.json'}:stop:0:0"
    assert data["hooks"]["state"][state_key]["trusted_hash"].startswith("sha256:")
    assert data["projects"][str(tmp_path)]["trust_level"] == "trusted"
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_AGENT_ID"] == agent["id"]


def test_completion_injects_pending_event_when_root_idle(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="idle")

    class FakeTmux:
        prompts = []

        def session_exists(self, session):
            return True

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()
    result = complete_agent(reg, child["id"], status="success", summary="done", config=cfg, tmux=fake)
    assert result["injected"] is True
    assert "PUPPETMASTER EVENT" in fake.prompts[0][1]
    assert reg.pending_deliveries(root["id"]) == []

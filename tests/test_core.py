from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

import pytest

import puppetmaster.cli as cli_module
from puppetmaster.config import load_config, write_init_config
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry
import puppetmaster.services as services
import puppetmaster.mcp_server as mcp_server
import puppetmaster.tmux as tmux_module
from puppetmaster.services import (
    cleanup_completed_agents,
    complete_agent,
    create_agent_record,
    drain_events,
    fire_due_wakeups,
    fire_wakeup,
    format_event_prompt,
    handle_stop_hook,
    inject_pending_prompt,
    kill_agent,
    kill_root_tree,
    pause_agent,
    prompt_text,
    reconcile,
    resume_agent,
    schedule_wakeup,
    start_orchestrator,
    stop_agent,
    validate_agent_id,
    write_codex_files,
)
from puppetmaster.tmux import Tmux
import puppetmaster.tui as tui_module
from puppetmaster.tui import (
    TuiApp,
    build_tree_rows,
    format_tree_row,
    parse_context_left,
    summarize_agent_relationships,
    summarize_tree,
)


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


def test_legacy_max_children_config_loads_as_concurrent_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        """[limits]
max_children_per_agent = 7
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cfg = load_config()

    assert cfg.limits.max_concurrent_children_per_agent == 7


def test_default_state_dir_uses_global_home_and_creates_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PUPPETMASTER_STATE_DIR", raising=False)

    cfg = load_config()

    assert cfg.state_dir == (home / ".puppetmaster").resolve()
    assert (cfg.state_dir / "config.toml").exists()
    assert (cfg.state_dir / "agents").is_dir()
    assert not (work / ".puppetmaster").exists()


def test_default_state_dir_is_shared_across_command_directories(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    home.mkdir()
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PUPPETMASTER_STATE_DIR", raising=False)

    monkeypatch.chdir(project_a)
    first = load_config()
    monkeypatch.chdir(project_b)
    second = load_config()

    assert first.state_dir == second.state_dir == (home / ".puppetmaster").resolve()
    assert not (project_a / ".puppetmaster").exists()
    assert not (project_b / ".puppetmaster").exists()


def test_state_dir_override_expands_and_resolves(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", "~/state/../isolated-state")

    cfg = load_config()

    assert cfg.state_dir == (home / "isolated-state").resolve()
    assert (cfg.state_dir / "config.toml").exists()


def test_default_config_creation_writes_discord_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cfg = load_config()

    data = tomllib.loads((state_dir / "config.toml").read_text(encoding="utf-8"))
    assert "discord" in data
    assert data["codex"]["home"] == ""
    assert data["discord"]["token"] == ""
    assert data["discord"]["guild_id"] == ""
    assert cfg.discord.token is None
    assert cfg.discord.guild_id is None


def test_load_config_uses_configured_codex_home_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    home = tmp_path / "home"
    source_home = tmp_path / "source-codex"
    managed_source_home = tmp_path / "managed-source-codex"
    generated_home = tmp_path / "generated-agent-codex"
    home.mkdir()
    source_home.mkdir()
    managed_source_home.mkdir()
    generated_home.mkdir()
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PUPPETMASTER_AGENT_ID", raising=False)
    monkeypatch.delenv("PUPPETMASTER_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)

    default_cfg = load_config()
    assert default_cfg.codex_home == (home / ".codex").resolve()

    (state_dir / "config.toml").write_text(
        """[codex]
home = "~/source-codex"
""",
        encoding="utf-8",
    )
    configured_cfg = load_config()
    assert configured_cfg.codex_home == (home / "source-codex").resolve()

    monkeypatch.setenv("CODEX_HOME", str(source_home))
    env_cfg = load_config()
    assert env_cfg.codex_home == source_home.resolve()

    monkeypatch.setenv("PUPPETMASTER_AGENT_ID", "agt_root")
    monkeypatch.delenv("PUPPETMASTER_CODEX_HOME", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(generated_home))
    managed_without_source_cfg = load_config()
    assert managed_without_source_cfg.codex_home == (home / "source-codex").resolve()

    monkeypatch.setenv("PUPPETMASTER_CODEX_HOME", str(managed_source_home))
    monkeypatch.setenv("CODEX_HOME", str(generated_home))
    managed_cfg = load_config()
    assert managed_cfg.codex_home == managed_source_home.resolve()


def test_init_no_input_creates_default_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    result = cli_module.cmd_init(
        argparse.Namespace(
            discord_token=None,
            discord_guild_id=None,
            start_discord=False,
            no_input=True,
            json=True,
        )
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    data = tomllib.loads((state_dir / "config.toml").read_text(encoding="utf-8"))
    assert output["state_dir"] == str(state_dir)
    assert output["config_path"] == str(state_dir / "config.toml")
    assert output["discord_token_configured"] is False
    assert output["discord_guild_id"] is None
    assert output["started_discord"] is False
    assert set(["limits", "codex", "discord"]).issubset(data)
    assert data["codex"]["home"] == ""
    assert data["discord"]["token"] == ""
    assert data["discord"]["guild_id"] == ""


def test_init_noninteractive_sets_values_without_leaking_token(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cli_module.cmd_init(
        argparse.Namespace(
            discord_token="secret-token",
            discord_guild_id="123456789",
            start_discord=False,
            no_input=True,
            json=True,
        )
    )

    stdout = capsys.readouterr().out
    output = json.loads(stdout)
    data = tomllib.loads((state_dir / "config.toml").read_text(encoding="utf-8"))
    assert "secret-token" not in stdout
    assert output["discord_token_configured"] is True
    assert output["discord_guild_id"] == 123456789
    assert data["discord"]["token"] == "secret-token"
    assert data["discord"]["guild_id"] == "123456789"


def test_init_preserves_existing_values_and_unknown_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        """tmux_session_prefix = "custom_"

[limits]
max_depth = 9

[codex]
no_alt_screen = false

[discord]
token = "existing-token"
guild_id = "987654321"

[custom]
enabled = true
label = "keep-me"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cli_module.cmd_init(
        argparse.Namespace(
            discord_token=None,
            discord_guild_id=None,
            start_discord=False,
            no_input=True,
            json=False,
        )
    )

    data = tomllib.loads((state_dir / "config.toml").read_text(encoding="utf-8"))
    cfg = load_config()
    assert data["tmux_session_prefix"] == "custom_"
    assert data["limits"]["max_depth"] == 9
    assert data["limits"]["max_total_agents"] == 30
    assert data["codex"]["no_alt_screen"] is False
    assert data["discord"]["token"] == "existing-token"
    assert data["discord"]["guild_id"] == "987654321"
    assert data["custom"] == {"enabled": True, "label": "keep-me"}
    assert cfg.discord.token == "existing-token"
    assert cfg.discord.guild_id == 987654321


def test_init_interactive_blank_prompts_preserve_existing_values(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        """[discord]
token = "existing-token"
guild_id = "123456789"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))
    getpass_prompts = []
    answers = iter(["", "n"])

    def fake_getpass(prompt):
        getpass_prompts.append(prompt)
        return ""

    monkeypatch.setattr(cli_module.getpass, "getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    cli_module.cmd_init(
        argparse.Namespace(
            discord_token=None,
            discord_guild_id=None,
            start_discord=False,
            no_input=False,
            json=False,
        )
    )

    stdout = capsys.readouterr().out
    data = tomllib.loads((state_dir / "config.toml").read_text(encoding="utf-8"))
    assert getpass_prompts == ["Discord bot token [hidden, leave blank to keep existing]: "]
    assert "existing-token" not in stdout
    assert data["discord"]["token"] == "existing-token"
    assert data["discord"]["guild_id"] == "123456789"


def test_init_rejects_invalid_guild_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(tmp_path / ".state"))

    with pytest.raises(PuppetError) as exc:
        cli_module.cmd_init(
            argparse.Namespace(
                discord_token=None,
                discord_guild_id="guild-123",
                start_discord=False,
                no_input=True,
                json=True,
            )
        )

    assert exc.value.code == "invalid_config"
    assert "discord.guild_id" in exc.value.message


def test_init_start_discord_json_requires_token_before_spawning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    def fail_popen(*args, **kwargs):
        raise AssertionError("init should not spawn Discord without required config")

    monkeypatch.setattr(cli_module.subprocess, "Popen", fail_popen)

    result = cli_module.main(["init", "--no-input", "--start-discord", "--json"])

    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert result == 1
    assert captured.out == ""
    assert error["error"]["code"] == "discord_token_required"
    assert error["error"]["message"] == "discord.token is required."
    assert not (state_dir / cli_module.DISCORD_PID_FILE).exists()


def test_init_start_discord_json_requires_guild_before_spawning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    def fail_popen(*args, **kwargs):
        raise AssertionError("init should not spawn Discord without required config")

    monkeypatch.setattr(cli_module.subprocess, "Popen", fail_popen)

    result = cli_module.main(
        ["init", "--no-input", "--discord-token", "secret-token", "--start-discord", "--json"]
    )

    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert result == 1
    assert "secret-token" not in captured.out
    assert "secret-token" not in captured.err
    assert error["error"]["code"] == "discord_guild_required"
    assert error["error"]["message"] == "discord.guild_id is required."
    assert not (state_dir / cli_module.DISCORD_PID_FILE).exists()


def test_init_start_discord_reports_existing_process_without_starting(ctx, monkeypatch, capsys):
    cfg, _reg, _tmux = ctx
    status = {
        "running": True,
        "pid": 4242,
        "pid_file": str(cfg.state_dir / cli_module.DISCORD_PID_FILE),
        "log_path": str(cfg.state_dir / cli_module.DISCORD_LOG_FILE),
    }
    monkeypatch.setattr(cli_module, "discord_background_status", lambda config: status)

    def fail_start(config):
        raise AssertionError("init should not start a duplicate Discord bot")

    monkeypatch.setattr(cli_module, "start_discord_background_process", fail_start)

    cli_module.cmd_init(
        argparse.Namespace(
            discord_token=None,
            discord_guild_id=None,
            start_discord=True,
            no_input=True,
            json=True,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert output["started_discord"] is False
    assert output["discord"]["already_running"] is True
    assert output["discord"]["pid"] == 4242


def test_init_start_discord_uses_background_start_path(ctx, monkeypatch, capsys):
    cfg, _reg, _tmux = ctx
    calls = []
    status = {
        "running": False,
        "pid": None,
        "pid_file": str(cfg.state_dir / cli_module.DISCORD_PID_FILE),
        "log_path": str(cfg.state_dir / cli_module.DISCORD_LOG_FILE),
    }
    started = {**status, "running": True, "pid": 5151}
    monkeypatch.setattr(cli_module, "discord_background_status", lambda config: status)

    def fake_start(config):
        calls.append(config.state_dir)
        return started

    monkeypatch.setattr(cli_module, "start_discord_background_process", fake_start)

    cli_module.cmd_init(
        argparse.Namespace(
            discord_token=None,
            discord_guild_id=None,
            start_discord=True,
            no_input=True,
            json=True,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert calls == [cfg.state_dir]
    assert output["started_discord"] is True
    assert output["discord"]["pid"] == 5151


def test_registry_initializes_discord_schema_on_fresh_state_dir(ctx):
    _cfg, reg, _tmux = ctx

    with reg.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type='table' and name in (?, ?, ?)",
                ("discord_channel_bindings", "outbound_human_messages", "discord_skills"),
            )
        }

    assert tables == {"discord_channel_bindings", "outbound_human_messages", "discord_skills"}


def test_registry_initializes_discord_schema_on_existing_state_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))
    cfg = load_config()

    Registry(cfg)
    reopened = Registry(cfg)

    with reopened.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type='table' and name in (?, ?, ?)",
                ("discord_channel_bindings", "outbound_human_messages", "discord_skills"),
            )
        }

    assert tables == {"discord_channel_bindings", "outbound_human_messages", "discord_skills"}


def test_discord_config_accepts_numeric_string_guild_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        """[discord]
token = ""
guild_id = "123456789"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cfg = load_config()

    assert cfg.discord.guild_id == 123456789


def test_discord_config_accepts_integer_guild_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        """[discord]
token = "secret"
guild_id = 123456789
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    cfg = load_config()

    assert cfg.discord.token == "secret"
    assert cfg.discord.guild_id == 123456789


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("poll_interval_seconds", "0", "discord.poll_interval_seconds must be positive"),
        ("typing_timeout_seconds", "nan", "discord.typing_timeout_seconds must be positive"),
        ("chunk_size", "1901", "discord.chunk_size must be no greater than 1900"),
        ("max_chunks", "0", "discord.max_chunks must be a positive integer"),
    ],
)
def test_discord_config_rejects_invalid_values(tmp_path, monkeypatch, field, value, message):
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "config.toml").write_text(
        f"""[discord]
{field} = {value}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_dir))

    with pytest.raises(PuppetError) as exc:
        load_config()

    assert exc.value.code == "invalid_config"
    assert message in exc.value.message


def test_discord_channel_bindings_rebind_and_list(ctx):
    _cfg, reg, _tmux = ctx

    binding = reg.bind_discord_channel("channel-1", "root-1", "guild-1")
    assert binding["channel_id"] == "channel-1"
    assert binding["root_agent_id"] == "root-1"
    assert binding["guild_id"] == "guild-1"
    assert reg.discord_binding_for_channel("channel-1")["root_agent_id"] == "root-1"
    assert reg.discord_binding_for_root("root-1")["channel_id"] == "channel-1"

    reg.bind_discord_channel("channel-1", "root-2", "guild-1")
    assert reg.discord_binding_for_channel("channel-1")["root_agent_id"] == "root-2"
    assert reg.discord_binding_for_root("root-1") is None

    reg.bind_discord_channel("channel-2", "root-2", "guild-1")
    assert reg.discord_binding_for_channel("channel-1") is None
    assert reg.discord_binding_for_root("root-2")["channel_id"] == "channel-2"

    reg.bind_discord_channel("channel-3", "root-3")
    assert [item["channel_id"] for item in reg.list_discord_bindings()] == ["channel-2", "channel-3"]
    assert reg.unbind_discord_channel("channel-2") is True
    assert reg.unbind_discord_channel("channel-2") is False
    assert reg.discord_binding_for_channel("channel-2") is None


def test_discord_skills_persist_and_update(ctx):
    cfg, reg, _tmux = ctx

    first = reg.upsert_discord_skill("review", "Review the current diff.")
    second = reg.upsert_discord_skill("test-plan", "Write a test plan.")
    updated = reg.upsert_discord_skill("review", "Review staged changes.")
    reopened = Registry(cfg)

    assert first["name"] == "review"
    assert first["prompt"] == "Review the current diff."
    assert updated["created_at"] == first["created_at"]
    assert updated["updated_at"] >= first["updated_at"]
    assert reopened.discord_skill("review")["prompt"] == "Review staged changes."
    assert [skill["name"] for skill in reopened.list_discord_skills()] == ["review", "test-plan"]
    assert reopened.delete_discord_skill(second["name"]) is True
    assert reopened.delete_discord_skill(second["name"]) is False
    assert reopened.discord_skill(second["name"]) is None


def test_outbound_human_message_queue_lifecycle(ctx):
    _cfg, reg, _tmux = ctx

    first = reg.enqueue_outbound_human_message("root-1", "agent-1", "discord", "channel-1", "hello")
    second = reg.enqueue_outbound_human_message("root-1", "agent-2", "discord", "channel-1", "follow-up")

    assert first["id"].startswith("msg_")
    assert first["status"] == "pending"
    assert first["message"] == "hello"
    assert [item["id"] for item in reg.pending_outbound_human_messages("discord")] == [first["id"], second["id"]]
    assert reg.pending_outbound_human_messages("email") == []

    delivered = reg.mark_outbound_human_message_delivered(first["id"])
    failed = reg.mark_outbound_human_message_failed(second["id"], "discord API failed")

    assert delivered["status"] == "delivered"
    assert delivered["delivered_at"] is not None
    assert failed["status"] == "failed"
    assert failed["failed_at"] is not None
    assert failed["error"] == "discord API failed"
    assert reg.pending_outbound_human_messages("discord") == []

    failed_after_delivery = reg.mark_outbound_human_message_failed(first["id"], "post-delivery failure")
    assert failed_after_delivery["status"] == "failed"
    assert failed_after_delivery["delivered_at"] is None
    assert failed_after_delivery["failed_at"] is not None


def test_send_human_message_rejects_empty_message(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("bound-channel", agent["root_id"], "guild-1")

    with pytest.raises(PuppetError) as exc:
        services.send_human_message(reg, agent["id"], " \n\t ")

    assert exc.value.code == "message_required"
    assert reg.pending_outbound_human_messages("discord") == []


def test_send_human_message_requires_bound_root(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    with pytest.raises(PuppetError) as exc:
        services.send_human_message(reg, agent["id"], "Hello.")

    assert exc.value.code == "no_human_channel"
    assert "No Discord channel is bound" in exc.value.message
    assert reg.pending_outbound_human_messages("discord") == []


def test_send_human_message_enqueues_pending_message_and_audit_event(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("bound-channel", root["id"], "guild-1")

    result = services.send_human_message(reg, root["id"], "  Hello human.  ", source="test")

    assert result["queued"] is True
    assert result["transport"] == "discord"
    assert result["channel_id"] == "bound-channel"
    queued = reg.pending_outbound_human_messages("discord")
    assert len(queued) == 1
    assert queued[0]["id"] == result["id"]
    assert queued[0]["root_agent_id"] == root["id"]
    assert queued[0]["agent_id"] == root["id"]
    assert queued[0]["channel_id"] == "bound-channel"
    assert queued[0]["message"] == "Hello human."
    events = reg.list_events(root["id"], limit=10)
    audit = next(event for event in events if event["type"] == "human.message.queued")
    assert audit["summary"] == "Human message queued."
    assert audit["source"] == "test"
    assert audit["payload"] == {
        "message_id": result["id"],
        "transport": "discord",
        "channel_id": "bound-channel",
        "message_length": len("Hello human."),
    }


def test_send_human_message_child_routes_through_root_binding(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.bind_discord_channel("root-channel", root["id"], "guild-1")

    result = services.send_human_message(reg, child["id"], "please use channel-id=wrong-channel")

    queued = reg.pending_outbound_human_messages("discord")[0]
    assert result["channel_id"] == "root-channel"
    assert queued["root_agent_id"] == root["id"]
    assert queued["agent_id"] == child["id"]
    assert queued["channel_id"] == "root-channel"
    assert "wrong-channel" not in queued["channel_id"]


def test_multiple_root_trees_remain_distinct_in_one_registry(ctx, tmp_path):
    cfg, reg, tmux = ctx
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    root_a = create_agent_record(cfg, reg, cwd=str(project_a), description="root A", role="orchestrator", name="a")
    root_b = create_agent_record(cfg, reg, cwd=str(project_b), description="root B", role="orchestrator", name="b")
    child_a = create_agent_record(cfg, reg, cwd=str(project_a), description="child A", parent_id=root_a["id"])
    child_b = create_agent_record(cfg, reg, cwd=str(project_b), description="child B", parent_id=root_b["id"])

    roots = [agent for agent in reg.list_agents() if agent["role"] == "orchestrator" and agent["id"] == agent["root_id"]]
    tree_a = reg.list_agents(root_id=root_a["id"])
    tree_b = reg.list_agents(root_id=root_b["id"])
    inspected_a = services.inspect_agent(cfg, reg, tmux, root_a["id"])["agent"]
    inspected_b = services.inspect_agent(cfg, reg, tmux, root_b["id"])["agent"]

    assert {root["id"] for root in roots} == {root_a["id"], root_b["id"]}
    assert inspected_a["cwd"] == str(project_a)
    assert inspected_a["root_id"] == root_a["id"]
    assert inspected_b["cwd"] == str(project_b)
    assert inspected_b["root_id"] == root_b["id"]
    assert {agent["id"] for agent in tree_a} == {root_a["id"], child_a["id"]}
    assert {agent["id"] for agent in tree_b} == {root_b["id"], child_b["id"]}


def test_start_orchestrator_allows_multiple_roots_by_default(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)

    class FakeTmux:
        def __init__(self):
            self.created = []
            self.piped = []
            self.prompts = []

        def create_session(self, session, cwd, command):
            self.created.append((session, cwd, command))

        def pipe_pane(self, session, log_path):
            self.piped.append((session, log_path))

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    root_a = start_orchestrator(cfg, reg, fake, cwd=str(project_a), prompt="Manage A.", name="a")
    root_b = start_orchestrator(cfg, reg, fake, cwd=str(project_b), prompt="Manage B.", name="b")

    assert root_a["id"] != root_b["id"]
    assert root_a["root_id"] == root_a["id"]
    assert root_b["root_id"] == root_b["id"]
    assert root_a["cwd"] == str(project_a)
    assert root_b["cwd"] == str(project_b)
    assert len(fake.created) == 2
    assert {agent["id"] for agent in reg.list_agents()} == {root_a["id"], root_b["id"]}


def test_start_orchestrator_goal_mode_prepends_goal_to_initial_prompt(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)

    class FakeTmux:
        def __init__(self):
            self.prompts = []

        def create_session(self, _session, _cwd, _command):
            return None

        def pipe_pane(self, _session, _log_path):
            return None

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    root = start_orchestrator(cfg, reg, fake, cwd=str(tmp_path), prompt="Manage this project.", goal=True)

    prompt_path = Path(root["metadata"]["generated_files"]["prompt"])
    assert "Task:\n/goal Manage this project." in prompt_path.read_text(encoding="utf-8")
    assert "Task:\n/goal Manage this project." in fake.prompts[0][1]


def test_start_orchestrator_codex_home_feeds_root_mcp_environment(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    source_home = tmp_path / "codex-source"
    source_home.mkdir()
    (source_home / "config.toml").write_text('model = "gpt-source"\n', encoding="utf-8")
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)

    class FakeTmux:
        def create_session(self, _session, _cwd, _command):
            return None

        def pipe_pane(self, _session, _log_path):
            return None

        def send_prompt(self, _session, _prompt):
            return None

    root = start_orchestrator(
        cfg,
        reg,
        FakeTmux(),
        cwd=str(tmp_path),
        prompt="Manage this project.",
        codex_home=str(source_home),
    )

    files = root["metadata"]["generated_files"]
    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    launch = Path(files["launch"]).read_text(encoding="utf-8")
    assert files["source_codex_home"] == str(source_home.resolve())
    assert data["model"] == "gpt-source"
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_CODEX_HOME"] == str(source_home.resolve())
    assert f"export PUPPETMASTER_CODEX_HOME={str(source_home.resolve())!r}" in launch


def test_validate_agent_id_accepts_safe_ids_and_rejects_unsafe_ids():
    for value in ["project-a", "Project_1", "a.b", "A", "a" * 64]:
        assert validate_agent_id(value) == value

    for value in ["", ".hidden", "bad/id", "../bad", "bad..id", "bad id", "bad;id", "a" * 65]:
        with pytest.raises(PuppetError) as exc:
            validate_agent_id(value)
        assert exc.value.code == "invalid_agent_id"
        assert "[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" in exc.value.hint


def test_start_orchestrator_uses_custom_agent_id_for_root_paths_and_session(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)

    class FakeTmux:
        def __init__(self):
            self.created = []
            self.piped = []
            self.prompts = []

        def session_exists(self, session):
            return False

        def create_session(self, session, cwd, command):
            self.created.append((session, cwd, command))

        def pipe_pane(self, session, log_path):
            self.piped.append((session, log_path))

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    root = start_orchestrator(cfg, reg, fake, cwd=str(tmp_path), prompt="Manage project.", name="root", agent_id="project-a")

    assert root["id"] == "project-a"
    assert root["root_id"] == "project-a"
    assert root["tmux_session"] == f"{cfg.tmux_session_prefix}project-a"
    assert Path(root["log_path"]).parent == cfg.agents_dir / "project-a"
    assert Path(root["events_path"]).parent == cfg.agents_dir / "project-a"
    assert Path(root["initial_prompt_path"]).parent == cfg.agents_dir / "project-a"
    files = root["metadata"]["generated_files"]
    assert Path(files["prompt"]).parent == cfg.agents_dir / "project-a"
    assert Path(files["launch"]).parent == cfg.agents_dir / "project-a"
    assert Path(files["config"]).parent == cfg.agents_dir / "project-a" / "codex-config"
    assert fake.created == [(f"{cfg.tmux_session_prefix}project-a", str(tmp_path), files["launch"])]


def test_start_orchestrator_rejects_custom_id_duplicates_before_side_effects(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    existing = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator", agent_id="project-a")
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})

    class FakeTmux:
        def __init__(self):
            self.created = []

        def session_exists(self, session):
            return False

        def create_session(self, session, cwd, command):
            self.created.append((session, cwd, command))

    fake = FakeTmux()
    before = sorted(agent["id"] for agent in reg.list_agents())

    with pytest.raises(PuppetError) as exc:
        start_orchestrator(cfg, reg, fake, cwd=str(tmp_path), prompt="Duplicate.", agent_id="project-a")

    assert exc.value.code == "duplicate_agent_id"
    assert sorted(agent["id"] for agent in reg.list_agents()) == before == [existing["id"]]
    assert fake.created == []


def test_start_orchestrator_rejects_existing_custom_id_directory_before_registry_row(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    (cfg.agents_dir / "project-a").mkdir(parents=True)
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})

    class FakeTmux:
        def session_exists(self, session):
            return False

    with pytest.raises(PuppetError) as exc:
        start_orchestrator(cfg, reg, FakeTmux(), cwd=str(tmp_path), prompt="Manage.", agent_id="project-a")

    assert exc.value.code == "duplicate_agent_id"
    assert reg.list_agents() == []


def test_start_orchestrator_rejects_existing_custom_id_tmux_session_before_registry_row(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})

    class FakeTmux:
        def session_exists(self, session):
            return session == f"{cfg.tmux_session_prefix}project-a"

    with pytest.raises(PuppetError) as exc:
        start_orchestrator(cfg, reg, FakeTmux(), cwd=str(tmp_path), prompt="Manage.", agent_id="project-a")

    assert exc.value.code == "duplicate_agent_id"
    assert reg.list_agents() == []
    assert not (cfg.agents_dir / "project-a").exists()


def test_orchestrator_start_defaults_cwd_to_current_directory(tmp_path, monkeypatch, capsys):
    work = tmp_path / "project"
    work.mkdir()
    monkeypatch.chdir(work)
    parser = cli_module.build_parser()
    args = parser.parse_args(["orchestrator", "start", "--prompt", "Manage this project.", "--json"])
    calls = {}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_build_context():
        return object(), object(), FakeTmux()

    def fake_start_orchestrator(cfg, reg, tmux, *, cwd, prompt, name, new_root, agent_id, goal):
        calls.update({"cwd": cwd, "prompt": prompt, "name": name, "new_root": new_root, "agent_id": agent_id, "goal": goal})
        return {"id": "agt_test", "tmux_session": "puppet_agt_test"}

    monkeypatch.setattr(cli_module, "build_context", fake_build_context)
    monkeypatch.setattr(cli_module, "start_orchestrator", fake_start_orchestrator)

    assert args.cwd is None
    assert args.func(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert calls == {
        "cwd": str(work.resolve()),
        "prompt": "Manage this project.",
        "name": "root",
        "new_root": False,
        "agent_id": None,
        "goal": False,
    }
    assert output["agent"]["id"] == "agt_test"


def test_orchestrator_start_passes_goal_flag_from_cli(tmp_path, monkeypatch, capsys):
    work = tmp_path / "project"
    work.mkdir()
    monkeypatch.chdir(work)
    parser = cli_module.build_parser()
    args = parser.parse_args(["orchestrator", "start", "--goal", "--prompt", "Manage this project.", "--json"])
    calls = {}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_build_context():
        return object(), object(), FakeTmux()

    def fake_start_orchestrator(cfg, reg, tmux, *, cwd, prompt, name, new_root, agent_id, goal):
        calls.update({"cwd": cwd, "prompt": prompt, "name": name, "new_root": new_root, "agent_id": agent_id, "goal": goal})
        return {"id": "agt_test", "tmux_session": "puppet_agt_test"}

    monkeypatch.setattr(cli_module, "build_context", fake_build_context)
    monkeypatch.setattr(cli_module, "start_orchestrator", fake_start_orchestrator)

    assert args.goal is True
    assert args.func(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert calls["goal"] is True
    assert output["agent"]["id"] == "agt_test"


def test_orchestrator_start_applies_codex_home_from_cli(tmp_path, monkeypatch, capsys):
    work = tmp_path / "project"
    source_home = tmp_path / "codex-source"
    work.mkdir()
    source_home.mkdir()
    monkeypatch.chdir(work)
    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "orchestrator",
            "start",
            "--codex-home",
            str(source_home),
            "--prompt",
            "Manage this project.",
            "--json",
        ]
    )
    calls = {}

    class FakeConfig:
        def with_codex_home(self, value):
            calls["codex_home"] = value
            return "configured-cfg"

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_build_context():
        return FakeConfig(), object(), FakeTmux()

    def fake_start_orchestrator(cfg, reg, tmux, *, cwd, prompt, name, new_root, agent_id, goal):
        calls.update(
            {
                "cfg": cfg,
                "cwd": cwd,
                "prompt": prompt,
                "name": name,
                "new_root": new_root,
                "agent_id": agent_id,
                "goal": goal,
            }
        )
        return {"id": "agt_test", "tmux_session": "puppet_agt_test"}

    monkeypatch.setattr(cli_module, "build_context", fake_build_context)
    monkeypatch.setattr(cli_module, "start_orchestrator", fake_start_orchestrator)

    assert args.func(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert calls["codex_home"] == str(source_home)
    assert calls["cfg"] == "configured-cfg"
    assert output["agent"]["id"] == "agt_test"


def test_orchestrator_start_passes_custom_agent_id_from_cli(tmp_path, monkeypatch, capsys):
    work = tmp_path / "project"
    work.mkdir()
    monkeypatch.chdir(work)
    parser = cli_module.build_parser()
    args = parser.parse_args(["orchestrator", "start", "--agent-id", "project-a", "--prompt", "Manage.", "--json"])
    calls = {}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_build_context():
        return object(), object(), FakeTmux()

    def fake_start_orchestrator(cfg, reg, tmux, *, cwd, prompt, name, new_root, agent_id, goal):
        calls.update({"cwd": cwd, "prompt": prompt, "name": name, "new_root": new_root, "agent_id": agent_id, "goal": goal})
        return {"id": agent_id, "root_id": agent_id, "tmux_session": f"puppet_{agent_id}"}

    monkeypatch.setattr(cli_module, "build_context", fake_build_context)
    monkeypatch.setattr(cli_module, "start_orchestrator", fake_start_orchestrator)

    assert args.func(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert calls["agent_id"] == "project-a"
    assert calls["goal"] is False
    assert output["agent"]["id"] == "project-a"
    assert output["agent"]["root_id"] == "project-a"


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


def test_schedule_wakeup_persists_and_spawns_helper(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PYTHONPATH", "src")
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    calls = []
    sleeps = []
    monkeypatch.setattr(services.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))

        class Process:
            pass

        return Process()

    monkeypatch.setattr(services.subprocess, "Popen", fake_popen)

    wakeup = schedule_wakeup(cfg, reg, agent["id"], 30, "waiting for child agents")

    assert wakeup["status"] == "scheduled"
    assert wakeup["agent_id"] == agent["id"]
    assert wakeup["reason"] == "waiting for child agents"
    assert wakeup["payload"] == {"seconds": 30}
    assert calls[0][0][-3:] == ["sleep-and-fire", "--wakeup-id", wakeup["id"]]
    assert "cwd" not in calls[0][1]
    assert calls[0][1]["env"]["PUPPETMASTER_STATE_DIR"] == str(cfg.state_dir)
    assert calls[0][1]["env"]["PUPPETMASTER_CODEX_HOME"] == str(cfg.codex_home)
    assert calls[0][1]["env"]["PYTHONPATH"] == str((checkout / "src").resolve())
    assert calls[0][1]["env"]["PYTHONPATH"] != str(cfg.repo_dir / "src")
    assert sleeps == []


def test_schedule_wakeup_rejects_invalid_waits(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    with pytest.raises(PuppetError) as zero:
        schedule_wakeup(cfg, reg, agent["id"], 0, spawn_helper=False)
    assert zero.value.code == "invalid_wait"

    with pytest.raises(PuppetError) as too_large:
        schedule_wakeup(cfg, reg, agent["id"], cfg.limits.max_wait_seconds + 1, spawn_helper=False)
    assert too_large.value.code == "invalid_wait"


def test_discord_serve_background_spawns_detached(ctx, monkeypatch):
    cfg, _reg, _tmux = ctx
    write_init_config(discord_token="secret-token", discord_guild_id="123456789")
    checkout = cfg.repo_dir / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PYTHONPATH", "src")
    calls = []

    class Process:
        pid = 4242

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)

    result = cli_module.cmd_discord_serve(argparse.Namespace(background=True, json=True))

    assert result == 0
    assert calls[0][0] == [cli_module.sys.executable, "-m", "puppetmaster.cli", "discord", "serve"]
    assert "cwd" not in calls[0][1]
    assert calls[0][1]["stdin"] == cli_module.subprocess.DEVNULL
    assert calls[0][1]["stderr"] == cli_module.subprocess.STDOUT
    assert calls[0][1]["start_new_session"] is True
    assert calls[0][1]["env"]["PUPPETMASTER_STATE_DIR"] == str(cfg.state_dir)
    assert calls[0][1]["env"]["PYTHONPATH"] == str((checkout / "src").resolve())
    assert calls[0][1]["env"]["PYTHONPATH"] != str(cfg.repo_dir / "src")
    assert (cfg.state_dir / cli_module.DISCORD_PID_FILE).read_text(encoding="utf-8") == "4242\n"
    assert (cfg.state_dir / cli_module.DISCORD_LOG_FILE).exists()


def test_discord_serve_background_replaces_stale_pid_file(ctx, monkeypatch):
    _cfg, _reg, _tmux = ctx
    cfg = write_init_config(discord_token="secret-token", discord_guild_id="123456789")
    pid_file = cfg.state_dir / cli_module.DISCORD_PID_FILE
    pid_file.write_text("999999\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "_pid_is_running", lambda pid: False)
    monkeypatch.setattr(cli_module, "_pid_matches_discord_process", lambda pid: False)

    class Process:
        pid = 5151

    monkeypatch.setattr(cli_module.subprocess, "Popen", lambda *args, **kwargs: Process())

    result = cli_module.start_discord_background_process(cfg)

    assert result["pid"] == 5151
    assert pid_file.read_text(encoding="utf-8") == "5151\n"
    assert result["pid_file"] == str(pid_file)
    assert result["log_path"] == str(cfg.state_dir / cli_module.DISCORD_LOG_FILE)


def test_discord_serve_background_rejects_existing_process(ctx, monkeypatch):
    cfg, _reg, _tmux = ctx
    (cfg.state_dir / cli_module.DISCORD_PID_FILE).write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "_pid_is_running", lambda pid: True)
    monkeypatch.setattr(cli_module, "_pid_matches_discord_process", lambda pid: True)

    with pytest.raises(PuppetError) as exc:
        cli_module.cmd_discord_serve(argparse.Namespace(background=True, json=False))

    assert exc.value.code == "discord_bot_already_running"


def test_discord_status_and_stop_use_active_state_dir(ctx, monkeypatch, capsys):
    cfg, _reg, _tmux = ctx
    pid_file = cfg.state_dir / cli_module.DISCORD_PID_FILE
    log_file = cfg.state_dir / cli_module.DISCORD_LOG_FILE
    pid_file.write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "_pid_matches_discord_process", lambda pid: True)
    monkeypatch.setattr(cli_module, "_pid_is_running", lambda pid: True)

    assert cli_module.cmd_discord_status(argparse.Namespace(json=True)) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["running"] is True
    assert status["pid"] == 4242
    assert status["pid_file"] == str(pid_file)
    assert status["log_path"] == str(log_file)

    checks = iter([True, False])
    kills = []
    monkeypatch.setattr(cli_module, "_pid_is_running", lambda pid: next(checks))
    monkeypatch.setattr(cli_module.os, "kill", lambda pid, sig: kills.append((pid, sig)))

    assert cli_module.cmd_discord_stop(argparse.Namespace(timeout=1.0, json=True)) == 0
    stopped = json.loads(capsys.readouterr().out)
    assert kills == [(4242, cli_module.signal.SIGTERM)]
    assert stopped["stopped"] is True
    assert stopped["running"] is False
    assert not pid_file.exists()


def test_discord_process_management_is_isolated_by_state_dir(tmp_path, monkeypatch):
    state_a = tmp_path / "state-a"
    state_b = tmp_path / "state-b"
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_a))
    cfg_a = write_init_config(discord_token="token-a", discord_guild_id="123")
    monkeypatch.setenv("PUPPETMASTER_STATE_DIR", str(state_b))
    cfg_b = write_init_config(discord_token="token-b", discord_guild_id="123")
    (cfg_a.state_dir / cli_module.DISCORD_PID_FILE).write_text("1111\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "_pid_is_running", lambda pid: pid == 1111)
    monkeypatch.setattr(cli_module, "_pid_matches_discord_process", lambda pid: pid == 1111)

    class Process:
        pid = 2222

    popen_envs = []

    def fake_popen(args, **kwargs):
        popen_envs.append(kwargs["env"])
        return Process()

    monkeypatch.setattr(cli_module.subprocess, "Popen", fake_popen)

    result_b = cli_module.start_discord_background_process(cfg_b)

    assert result_b["pid"] == 2222
    assert popen_envs[0]["PUPPETMASTER_STATE_DIR"] == str(cfg_b.state_dir)
    assert (cfg_b.state_dir / cli_module.DISCORD_PID_FILE).read_text(encoding="utf-8") == "2222\n"
    with pytest.raises(PuppetError) as exc:
        cli_module.start_discord_background_process(cfg_a)
    assert exc.value.code == "discord_bot_already_running"


def test_child_agent_can_schedule_wakeup(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    wakeup = schedule_wakeup(cfg, reg, child["id"], 5, spawn_helper=False)

    assert wakeup["agent_id"] == child["id"]
    assert wakeup["root_id"] == root["id"]


def test_mcp_wait_tool_schedules_for_caller_and_returns_instruction(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    caller = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, caller))
    monkeypatch.setattr(services, "spawn_wakeup_helper", lambda config, wakeup_id: None)

    result = mcp_server.wait_tool(10, "backoff")

    assert result["scheduled"] is True
    assert result["instruction"].startswith("End your turn.")
    wakeup = reg.get_wakeup(result["wakeup_id"])
    assert wakeup["agent_id"] == caller["id"]
    assert wakeup["reason"] == "backoff"


def test_mcp_send_human_message_returns_queued_result(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    caller = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("bound-channel", caller["id"], "guild-1")
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, caller))

    result = mcp_server.send_human_message("Hello from MCP.")

    assert result["queued"] is True
    assert result["transport"] == "discord"
    assert result["channel_id"] == "bound-channel"
    assert reg.pending_outbound_human_messages("discord")[0]["message"] == "Hello from MCP."


def test_mcp_send_human_message_rejects_child_callers(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.bind_discord_channel("bound-channel", root["id"], "guild-1")
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, child))

    result = mcp_server.send_human_message("Hello from child.")

    assert result["error"]["code"] == "not_authorized"
    assert "root orchestrators" in result["error"]["message"]
    assert reg.pending_outbound_human_messages("discord") == []


def test_mcp_send_human_message_returns_error_when_unbound(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    caller = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, caller))

    result = mcp_server.send_human_message("Hello from MCP.")

    assert result["error"]["code"] == "no_human_channel"
    assert "No Discord channel is bound" in result["error"]["message"]
    assert reg.pending_outbound_human_messages("discord") == []


def test_mcp_send_human_message_returns_error_for_empty_message(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    caller = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    reg.bind_discord_channel("bound-channel", caller["id"], "guild-1")
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, caller))

    result = mcp_server.send_human_message("")

    assert result["error"]["code"] == "message_required"
    assert reg.pending_outbound_human_messages("discord") == []


def test_mcp_run_removes_human_message_tool_for_child_callers(ctx, tmp_path, monkeypatch):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, tmux, child))

    class FakeMcp:
        def __init__(self):
            self.removed: list[str] = []
            self.ran = False

        def remove_tool(self, name: str) -> None:
            self.removed.append(name)

        def run(self) -> None:
            self.ran = True

    fake = FakeMcp()
    monkeypatch.setattr(mcp_server, "mcp", fake)

    mcp_server.run()

    assert fake.removed == ["send_human_message"]
    assert fake.ran is True


def test_mcp_kill_agent_tool_kills_authorized_child_session(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    class FakeTmux:
        def __init__(self):
            self.killed: list[str] = []

        def kill_session(self, session: str) -> None:
            self.killed.append(session)

        def session_exists(self, _session: str) -> bool:
            return False

    fake = FakeTmux()
    monkeypatch.setattr(mcp_server, "_context", lambda: (cfg, reg, fake, root))

    result = mcp_server.kill_agent_tool(child["id"])

    assert result["id"] == child["id"]
    assert result["status"] == "killed"
    assert result["termination_reason"] == "killed"
    assert fake.killed == [child["tmux_session"]]
    assert reg.get_agent(child["id"])["status"] == "killed"


def test_fire_wakeup_marks_fired_queues_wait_over_and_is_idempotent(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    wakeup = schedule_wakeup(cfg, reg, agent["id"], 5, "poll again", spawn_helper=False)

    first = fire_wakeup(cfg, reg, wakeup["id"])
    second = fire_wakeup(cfg, reg, wakeup["id"])

    assert first["fired"] is True
    assert second["fired"] is False
    assert reg.get_wakeup(wakeup["id"])["status"] == "fired"
    events = reg.list_events(agent["id"], limit=10)
    assert [event["type"] for event in events].count("agent.wait_over") == 1
    delivery = reg.pending_deliveries(agent["id"])[0]
    assert delivery["type"] == "agent.wait_over"
    assert delivery["payload"]["reason"] == "poll again"


def test_fire_due_wakeups_fires_overdue_scheduled_wakeups(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    wakeup = schedule_wakeup(cfg, reg, agent["id"], 300, spawn_helper=False)
    with reg.connect() as conn:
        conn.execute("update scheduled_wakeups set wake_at=? where id=?", ("2000-01-01T00:00:00Z", wakeup["id"]))

    result = fire_due_wakeups(cfg, reg, agent_id=agent["id"])

    assert result["count"] == 1
    assert reg.get_wakeup(wakeup["id"])["status"] == "fired"


def test_drain_events_fires_due_wakeup_before_delivery_lookup(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    wakeup = schedule_wakeup(cfg, reg, agent["id"], 300, "wake me", spawn_helper=False)
    with reg.connect() as conn:
        conn.execute("update scheduled_wakeups set wake_at=? where id=?", ("2000-01-01T00:00:00Z", wakeup["id"]))

    result = drain_events(cfg, reg, agent["id"])

    assert result["decision"] == "block"
    assert "PUPPETMASTER WAIT OVER" in result["reason"]
    assert "Wait over for agent" in result["reason"]
    assert reg.pending_deliveries(agent["id"]) == []


def test_format_event_prompt_renders_state_change_and_wait_over(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    complete_agent(reg, child["id"], status="blocked", summary="need input")

    state_prompt = format_event_prompt(reg, reg.pending_deliveries(root["id"]), 0)

    assert f"{child['id']} has new state blocked." in state_prompt
    assert f'kill_agent({{"agent_id":"{child["id"]}"}})' in state_prompt
    assert "after final output is consumed" in state_prompt
    reg.mark_delivered([delivery["id"] for delivery in reg.pending_deliveries(root["id"])])

    wakeup = schedule_wakeup(cfg, reg, root["id"], 5, "retry", spawn_helper=False)
    fire_wakeup(cfg, reg, wakeup["id"])
    wait_prompt = format_event_prompt(reg, reg.pending_deliveries(root["id"]), 0)

    assert wait_prompt.startswith("PUPPETMASTER WAIT OVER")
    assert "Reason: retry" in wait_prompt


def test_stop_kill_pause_resume_queue_parent_and_root_notifications(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", parent_id=root["id"])
    children = [
        create_agent_record(cfg, reg, cwd=str(tmp_path), description=f"child {index}", parent_id=parent["id"])
        for index in range(4)
    ]

    class FakeTmux:
        def stop_session(self, session):
            pass

        def kill_session(self, session):
            pass

        def session_exists(self, session):
            return False

    fake = FakeTmux()
    stop_agent(reg, fake, children[0]["id"], config=cfg)
    kill_agent(reg, fake, children[1]["id"], config=cfg)
    pause_agent(reg, children[2]["id"], config=cfg, tmux=fake)
    resume_agent(reg, children[3]["id"], config=cfg, tmux=fake)

    parent_types = [delivery["type"] for delivery in reg.pending_deliveries(parent["id"])]
    root_types = [delivery["type"] for delivery in reg.pending_deliveries(root["id"])]
    for event_type in ("agent.stopped", "agent.killed", "agent.paused", "agent.resumed"):
        assert event_type in parent_types
        assert event_type in root_types


def test_kill_root_tree_kills_only_one_root_tree(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root_a = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root A", role="orchestrator")
    child_a = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child A", parent_id=root_a["id"])
    grandchild_a = create_agent_record(cfg, reg, cwd=str(tmp_path), description="grandchild A", parent_id=child_a["id"])
    root_b = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root B", role="orchestrator")
    child_b = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child B", parent_id=root_b["id"])
    live_sessions = {
        root_a["tmux_session"],
        child_a["tmux_session"],
        grandchild_a["tmux_session"],
        root_b["tmux_session"],
        child_b["tmux_session"],
    }

    class FakeTmux:
        def __init__(self):
            self.killed: list[str] = []

        def session_exists(self, session):
            return session in live_sessions

        def kill_session(self, session):
            self.killed.append(session)
            live_sessions.discard(session)

    fake = FakeTmux()

    result = kill_root_tree(reg, fake, root_a["id"])

    assert set(result["killed"]) == {root_a["id"], child_a["id"], grandchild_a["id"]}
    assert set(fake.killed) == {root_a["tmux_session"], child_a["tmux_session"], grandchild_a["tmux_session"]}
    assert root_b["tmux_session"] in live_sessions
    assert child_b["tmux_session"] in live_sessions
    for agent_id in (root_a["id"], child_a["id"], grandchild_a["id"]):
        assert reg.get_agent(agent_id)["status"] == "killed"
    assert reg.get_agent(root_b["id"])["status"] == "starting"
    assert reg.get_agent(child_b["id"])["status"] == "starting"


def test_kill_root_tree_rejects_child_agent_ids(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    class FakeTmux:
        def session_exists(self, _session):
            return True

    with pytest.raises(PuppetError) as exc:
        kill_root_tree(reg, FakeTmux(), child["id"])

    assert exc.value.code == "invalid_agent"


def test_agent_kill_tree_cli_passes_root_and_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    parser = cli_module.build_parser()
    args = parser.parse_args(["agent", "kill-tree", "root-a", "--dry-run", "--json"])
    calls = {}

    def fake_build_context():
        return object(), object(), object()

    def fake_kill_root_tree(reg, tmux, root_agent_id, *, dry_run):
        calls.update({"reg": reg, "tmux": tmux, "root_agent_id": root_agent_id, "dry_run": dry_run})
        return {"root_agent_id": root_agent_id, "dry_run": dry_run, "would_kill": ["root-a"]}

    monkeypatch.setattr(cli_module, "build_context", fake_build_context)
    monkeypatch.setattr(cli_module, "kill_root_tree", fake_kill_root_tree)

    assert args.func(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert calls["root_agent_id"] == "root-a"
    assert calls["dry_run"] is True
    assert output["would_kill"] == ["root-a"]


def test_inject_pending_prompt_does_not_require_idle_status(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="running")
    complete_agent(reg, child["id"], status="success", summary="done")

    class FakeTmux:
        prompts = []

        def session_exists(self, session):
            return True

        def send_prompt(self, session, prompt):
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    assert inject_pending_prompt(cfg, reg, fake, root["id"]) is True
    assert fake.prompts[0][0] == root["tmux_session"]
    assert "PUPPETMASTER EVENT" in fake.prompts[0][1]
    assert reg.pending_deliveries(root["id"]) == []


def test_create_limit_counts_concurrent_children_only(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", role="orchestrator")
    children = [
        create_agent_record(cfg, reg, cwd=str(tmp_path), description=f"child {index}", parent_id=parent["id"])
        for index in range(5)
    ]
    for child, status in zip(children, ["running", "idle", "awaiting_input", "completed", "stopped"], strict=True):
        reg.update_agent(child["id"], status=status)

    services.enforce_create_limits(cfg, reg, parent)

    extra_running_1 = create_agent_record(cfg, reg, cwd=str(tmp_path), description="extra 1", parent_id=parent["id"])
    extra_running_2 = create_agent_record(cfg, reg, cwd=str(tmp_path), description="extra 2", parent_id=parent["id"])
    reg.update_agent(extra_running_1["id"], status="running")
    reg.update_agent(extra_running_2["id"], status="starting")

    with pytest.raises(PuppetError) as exc:
        services.enforce_create_limits(cfg, reg, parent)
    assert exc.value.code == "limit_exceeded"
    assert "max_concurrent_children_per_agent=5" in exc.value.message


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
    launch = Path(files["launch"]).read_text(encoding="utf-8")
    assert data["features"]["hooks"] is True
    state_key = f"{Path(files['config']).parent / 'hooks.json'}:stop:0:0"
    assert data["hooks"]["state"][state_key]["trusted_hash"].startswith("sha256:")
    assert data["projects"][str(tmp_path)]["trust_level"] == "trusted"
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_AGENT_ID"] == agent["id"]
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_CODEX_HOME"] == str(cfg.codex_home)
    assert "initial-prompt.md" not in launch
    assert '"$(cat ' not in launch


def test_generated_codex_files_preserve_explicit_pythonpath_without_guessing_checkout(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PYTHONPATH", "src")
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    files = write_codex_files(cfg, agent, "hello", orchestrator=True)

    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    launch = Path(files["launch"]).read_text(encoding="utf-8")
    pythonpath = str((checkout / "src").resolve())
    assert data["mcp_servers"]["puppetmaster"]["env"]["PYTHONPATH"] == pythonpath
    assert f"export PYTHONPATH={pythonpath!r}" in launch
    assert pythonpath != str(cfg.repo_dir / "src")


def test_prompt_text_explains_orchestrator_wait_and_event_loop(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    root_prompt = prompt_text(root, "Coordinate the work.")
    child_prompt = prompt_text(child, "Run the tests.")

    assert "If you are only waiting for child-agent progress, do not call wait(). Simply end your turn." in root_prompt
    assert "Your primary role is orchestration" in root_prompt
    assert "Do small, low-risk tasks yourself when delegation would add more overhead than value" in root_prompt
    assert "Delegate larger, multi-step, research-heavy, test-heavy, or parallelizable tasks" in root_prompt
    assert "Do not take on the main implementation or investigation yourself" in root_prompt
    assert "Do not use Codex's default spawn_agent tool" in root_prompt
    assert "Call wait(seconds, reason) only when you need a time-based wakeup" in root_prompt
    assert "then call kill_agent(child_id) to close its tmux session" in root_prompt
    assert "prevent stale Codex processes from accumulating" in root_prompt
    assert "Do not kill a child you still expect to prompt" in root_prompt
    assert "Puppetmaster will send you a fresh PUPPETMASTER EVENT message" in root_prompt
    assert "PUPPETMASTER WAIT OVER" in root_prompt
    assert "send_human_message" in root_prompt
    assert "Always use send_human_message for every human-facing message" in root_prompt
    assert "Regularly update the human with send_human_message during longer work" in root_prompt
    assert "Child agents do not have direct human-message tools" in root_prompt
    assert "DISCORD MESSAGE RECEIVED" in root_prompt
    assert "always answer the human by calling send_human_message" in root_prompt
    assert "routing" not in root_prompt.lower()
    assert "If you are only waiting for child-agent progress" not in child_prompt
    assert "Your primary role is orchestration" not in child_prompt
    assert "Do not use Codex's default spawn_agent tool" not in child_prompt
    assert "Use wait(seconds, reason) only for a time-based wakeup." in child_prompt
    assert "send_human_message" not in child_prompt
    assert "send message tool" not in child_prompt
    assert "Always use send_human_message for every human-facing message" not in child_prompt
    assert "Regularly update the human with send_human_message during longer work" not in child_prompt
    assert "Child agents do not have direct human-message tools" in child_prompt
    assert "Use kill_agent after consuming final child output" in child_prompt
    assert "DISCORD MESSAGE RECEIVED" not in child_prompt


def test_create_codex_agent_launches_interactive_session_then_sends_initial_prompt(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    sleeps = []
    monkeypatch.setattr(services.time, "sleep", lambda seconds: sleeps.append(seconds))

    class FakeTmux:
        def __init__(self):
            self.created = []
            self.piped = []
            self.prompts = []
            self.calls = []

        def create_session(self, session, cwd, command):
            self.calls.append(("create", session))
            self.created.append((session, cwd, command))

        def pipe_pane(self, session, log_path):
            self.calls.append(("pipe", session))
            self.piped.append((session, log_path))

        def send_prompt(self, session, prompt):
            self.calls.append(("send", session))
            self.prompts.append((session, prompt))

    fake = FakeTmux()

    agent = services.create_codex_agent(
        cfg,
        reg,
        fake,
        cwd=str(tmp_path),
        description="root",
        prompt="Coordinate child work.",
        role="orchestrator",
    )

    launch = Path(agent["metadata"]["generated_files"]["launch"]).read_text(encoding="utf-8")
    assert '"$(cat ' not in launch
    assert len(fake.created) == 1
    assert len(fake.piped) == 1
    assert fake.prompts[0][0] == agent["tmux_session"]
    assert "Task:\nCoordinate child work." in fake.prompts[0][1]
    assert "Orchestrator event loop:" in fake.prompts[0][1]
    assert fake.calls == [
        ("create", agent["tmux_session"]),
        ("pipe", agent["tmux_session"]),
        ("send", agent["tmux_session"]),
    ]
    assert sleeps == [services.INITIAL_PROMPT_SEND_DELAY_SECONDS]


def test_generated_codex_config_preserves_user_defaults(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        """model = "gpt-custom"
approval_policy = "on-request"

[features]
hooks = false

[profiles.default]
model = "gpt-profile"

[mcp_servers.existing]
command = "existing-server"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    cfg = cfg.with_codex_home(codex_home)
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    files = write_codex_files(cfg, agent, "hello", orchestrator=True)

    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    assert data["model"] == "gpt-custom"
    assert data["approval_policy"] == "on-request"
    assert data["profiles"]["default"]["model"] == "gpt-profile"
    assert data["mcp_servers"]["existing"]["command"] == "existing-server"
    assert data["features"]["hooks"] is True
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_AGENT_ID"] == agent["id"]


def test_generated_codex_files_use_configured_codex_home_for_auth_and_subagents(ctx, tmp_path, monkeypatch):
    cfg, reg, _tmux = ctx
    source_home = tmp_path / "source-codex"
    source_home.mkdir()
    (source_home / "config.toml").write_text('model = "gpt-source"\n', encoding="utf-8")
    (source_home / "auth.json").write_text('{"token":"secret"}\n', encoding="utf-8")
    cfg = cfg.with_codex_home(source_home)
    monkeypatch.setattr(services, "discover_codex", lambda: {"path": "/usr/bin/codex", "version": "test"})
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")

    files = write_codex_files(cfg, agent, "hello", orchestrator=True)

    generated_home = Path(files["codex_home"])
    data = tomllib.loads(Path(files["config"]).read_text(encoding="utf-8"))
    launch = Path(files["launch"]).read_text(encoding="utf-8")
    auth_link = generated_home / "auth.json"
    assert data["model"] == "gpt-source"
    assert data["mcp_servers"]["puppetmaster"]["env"]["PUPPETMASTER_CODEX_HOME"] == str(source_home.resolve())
    assert auth_link.is_symlink()
    assert auth_link.resolve() == source_home.resolve() / "auth.json"
    assert f"export PUPPETMASTER_CODEX_HOME={str(source_home.resolve())!r}" in launch
    assert f"export CODEX_HOME={str(generated_home)!r}" in launch


def test_tmux_send_prompt_pastes_and_confirms_with_second_enter(ctx, monkeypatch):
    _cfg, _reg, tmux = ctx
    calls = []
    sleeps = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(tmux, "require_tmux", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    tmux.send_prompt("puppet_agt_child", "continue")

    commands = [call[0] for call in calls]
    assert commands[:4] == [
        ["tmux", "set-buffer", "-b", commands[0][3], "continue"],
        ["tmux", "paste-buffer", "-pr", "-b", commands[0][3], "-t", "puppet_agt_child"],
        ["tmux", "send-keys", "-t", "puppet_agt_child", "C-m"],
        ["tmux", "send-keys", "-t", "puppet_agt_child", "C-m"],
    ]
    assert commands[4] == ["tmux", "delete-buffer", "-b", commands[0][3]]
    assert sleeps == [
        tmux_module.PROMPT_PASTE_SETTLE_DELAY_SECONDS,
        tmux_module.PROMPT_SUBMIT_CONFIRM_DELAY_SECONDS,
    ]


def test_tmux_capture_visible_pane_uses_current_view(ctx, monkeypatch):
    _cfg, _reg, tmux = ctx
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

        class Result:
            returncode = 0
            stdout = "visible output\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(tmux, "require_tmux", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    output = tmux.capture_visible_pane("puppet_agt_root")

    assert output == "visible output\n"
    assert calls == [
        (
            ["tmux", "capture-pane", "-ep", "-t", "puppet_agt_root"],
            {"text": True, "capture_output": True},
        )
    ]


def test_mcp_create_agent_prepends_goal_mode_to_prompt(tmp_path, monkeypatch):
    captured = {}
    caller = {"id": "agt_parent"}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_create_codex_agent(cfg, reg, tmux, **kwargs):
        captured.update(kwargs)
        return {"id": "agt_child", "status": "running", "cwd": kwargs["cwd"], "tmux_session": "puppet_agt_child"}

    monkeypatch.setattr(mcp_server, "_context", lambda: (object(), object(), FakeTmux(), caller))
    monkeypatch.setattr(mcp_server, "create_codex_agent", fake_create_codex_agent)

    result = mcp_server.create_agent(cwd=str(tmp_path), prompt="Run the focused smoke tests", goal=True)

    assert result["id"] == "agt_child"
    assert captured["prompt"] == "/goal Run the focused smoke tests"
    assert captured["description"] == "Run the focused smoke tests"
    assert captured["parent_id"] == caller["id"]
    assert captured["metadata"] == {}


def test_mcp_create_agent_leaves_prompt_unchanged_without_goal_mode(tmp_path, monkeypatch):
    captured = {}
    caller = {"id": "agt_parent"}

    class FakeTmux:
        def attach_command(self, session):
            return f"tmux attach -t {session}"

    def fake_create_codex_agent(cfg, reg, tmux, **kwargs):
        captured.update(kwargs)
        return {"id": "agt_child", "status": "running", "cwd": kwargs["cwd"], "tmux_session": "puppet_agt_child"}

    monkeypatch.setattr(mcp_server, "_context", lambda: (object(), object(), FakeTmux(), caller))
    monkeypatch.setattr(mcp_server, "create_codex_agent", fake_create_codex_agent)

    result = mcp_server.create_agent(cwd=str(tmp_path), prompt="Use the release workspace.", goal=False)

    assert result["id"] == "agt_child"
    assert captured["prompt"] == "Use the release workspace."
    assert captured["metadata"] == {}


def test_mcp_create_agent_requires_prompt_when_goal_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "_context", lambda: (object(), object(), object(), {"id": "agt_parent"}))

    result = mcp_server.create_agent(cwd=str(tmp_path))

    assert result["error"]["code"] == "prompt_required"


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


def test_tui_builds_nested_tree_rows(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator", name="root")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"], name="child")
    grandchild = create_agent_record(cfg, reg, cwd=str(tmp_path), description="grandchild", parent_id=child["id"])

    rows = build_tree_rows(reg.list_agents())

    assert [row["agent"]["id"] for row in rows] == [root["id"], child["id"], grandchild["id"]]
    assert [row["depth"] for row in rows] == [0, 1, 2]
    assert rows[0]["has_children"] is True
    assert rows[2]["has_children"] is False
    assert "live child" in format_tree_row(rows[1], live=True)


def test_tui_parses_context_left():
    assert parse_context_left("Context left: 42%") == "42%"
    assert parse_context_left("status 17.5% context remaining") == "17.5%"
    assert parse_context_left("\x1b[32mContext: 120k/200k\x1b[0m") == "120k/200k"
    assert parse_context_left("no context metric here") is None


def test_tui_strips_terminal_control_sequences_from_log_preview():
    raw = "\x1b[?1049l\x1b[?25h\x1b]0;agent title\x07done\x1b[0m\x1b7clean\x1b8\x1b]2;unterminated"

    assert tui_module.strip_ansi(raw) == "doneclean"


def test_tui_refresh_preview_sanitizes_stopped_agent_log(ctx, tmp_path):
    cfg, reg, tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    Path(agent["log_path"]).write_text("before\n\x1b[?1049l\x1b]0;title\x07after\x1b[0m\n", encoding="utf-8")

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.rows = build_tree_rows([agent])
    app.live_sessions = set()
    app.refresh_preview()

    assert app.preview == "before\nafter"


def test_tui_summarizes_tree_stats(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    reg.update_agent(root["id"], status="running")
    reg.update_agent(child["id"], status="idle")

    agents = reg.list_agents()
    stats = summarize_tree(agents, {root["tmux_session"]})

    assert stats["total"] == 2
    assert stats["live"] == 1
    assert stats["max_depth"] == 1
    assert "idle:1" in stats["statuses"]
    assert "running:1" in stats["statuses"]


def test_tui_summarizes_agent_relationship_counts(ctx, tmp_path):
    cfg, reg, _tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])
    grandchild = create_agent_record(cfg, reg, cwd=str(tmp_path), description="grandchild", parent_id=child["id"])

    child_counts, descendant_counts = summarize_agent_relationships(reg.list_agents())

    assert child_counts[root["id"]] == 1
    assert child_counts[child["id"]] == 1
    assert child_counts[grandchild["id"]] == 0
    assert descendant_counts[root["id"]] == 2
    assert descendant_counts[child["id"]] == 1
    assert descendant_counts[grandchild["id"]] == 0


def test_tui_preview_scroll_is_bounded(ctx):
    cfg, reg, tmux = ctx

    class Screen:
        def getmaxyx(self):
            return (23, 80)

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.preview = "\n".join(f"line {index}" for index in range(20))
    screen = Screen()

    assert app.preview_page_size(screen) == 5
    assert app.max_preview_scroll(screen) == 15

    app.scroll_preview(10, screen)
    assert app.preview_scroll == 10

    app.scroll_preview(10, screen)
    assert app.preview_scroll == 15

    app.scroll_preview(-100, screen)
    assert app.preview_scroll == 0


def test_tui_agent_list_scroll_defers_preview_refresh(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=root["id"])

    class Screen:
        def getmaxyx(self):
            return (23, 80)

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.rows = build_tree_rows([root, child])

    def fail_refresh(*_args, **_kwargs):
        raise AssertionError("agent-list movement should not refresh preview synchronously")

    app.refresh_preview = fail_refresh

    app.move_selection(1, Screen())

    assert app.selected == 1
    assert app.preview_refresh_due_at is not None
    assert app.preview_reset_pending is True


def test_tui_debounced_preview_refresh_runs_when_due(ctx, tmp_path):
    cfg, reg, tmux = ctx
    agent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.rows = build_tree_rows([agent])
    app.preview_refresh_due_at = 10.0
    app.preview_reset_pending = True
    calls = []

    def record_refresh(*, reset_scroll=False):
        calls.append(reset_scroll)

    app.refresh_preview = record_refresh

    assert app.maybe_refresh_debounced_preview(9.9) is False
    assert calls == []

    assert app.maybe_refresh_debounced_preview(10.0) is True
    assert calls == [True]
    assert app.preview_refresh_due_at is None
    assert app.preview_reset_pending is False


def test_tui_mouse_wheel_on_right_scrolls_preview_not_selection(ctx, monkeypatch):
    cfg, reg, tmux = ctx
    monkeypatch.setattr(tui_module.curses, "BUTTON4_PRESSED", 1, raising=False)

    class Screen:
        def getmaxyx(self):
            return (23, 90)

    app = TuiApp(cfg, reg, tmux, root_id=None, refresh=1.0, lines=120)
    app.preview = "\n".join(f"line {index}" for index in range(20))
    app.selected = 4

    app.handle_mouse_event(TuiApp.right_x(90), 17, 1, Screen())

    assert app.preview_scroll == 3
    assert app.selected == 4


def test_cleanup_completed_prunes_completed_subtrees_and_preserves_active_descendants(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    completed_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="done parent", parent_id=root["id"])
    completed_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="done child", parent_id=completed_parent["id"])
    mixed_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="mixed parent", parent_id=root["id"])
    active_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="active child", parent_id=mixed_parent["id"])
    root_completed_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="done root child", parent_id=root["id"])

    for agent in (root, completed_parent, completed_child, mixed_parent, root_completed_child):
        reg.update_agent(agent["id"], status="completed")
    reg.update_agent(active_child["id"], status="running")

    dry_run = cleanup_completed_agents(reg, tmux, dry_run=True)
    assert set(dry_run["would_prune"]) == {completed_parent["id"], completed_child["id"], root_completed_child["id"]}
    assert dry_run["pruned"] == []
    assert reg.maybe_agent(completed_child["id"]) is not None

    result = cleanup_completed_agents(reg, tmux, dry_run=False)
    assert set(result["pruned"]) == {completed_parent["id"], completed_child["id"], root_completed_child["id"]}
    assert reg.maybe_agent(completed_parent["id"]) is None
    assert reg.maybe_agent(completed_child["id"]) is None
    assert reg.maybe_agent(root_completed_child["id"]) is None
    assert reg.maybe_agent(root["id"]) is not None
    assert reg.maybe_agent(mixed_parent["id"]) is not None
    assert reg.maybe_agent(active_child["id"]) is not None
    assert result["logs_preserved"] is True


def test_cleanup_completed_prunes_subtrees_with_pending_deliveries(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="parent", parent_id=root["id"])
    child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="child", parent_id=parent["id"])

    complete_agent(reg, parent["id"], status="success", summary="done")
    complete_agent(reg, child["id"], status="success", summary="done")

    result = cleanup_completed_agents(reg, tmux, dry_run=False)
    assert set(result["pruned"]) == {parent["id"], child["id"]}
    assert reg.maybe_agent(parent["id"]) is None
    assert reg.maybe_agent(child["id"]) is None
    assert reg.maybe_agent(root["id"]) is not None
    assert reg.pending_deliveries(root["id"]) == []


def test_cleanup_completed_prunes_stopped_subtrees(ctx, tmp_path):
    cfg, reg, tmux = ctx
    root = create_agent_record(cfg, reg, cwd=str(tmp_path), description="root", role="orchestrator")
    stopped_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="stopped parent", parent_id=root["id"])
    stopped_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="stopped child", parent_id=stopped_parent["id"])
    completed_parent = create_agent_record(cfg, reg, cwd=str(tmp_path), description="completed parent", parent_id=root["id"])
    stopped_leaf = create_agent_record(cfg, reg, cwd=str(tmp_path), description="stopped leaf", parent_id=completed_parent["id"])
    active_child = create_agent_record(cfg, reg, cwd=str(tmp_path), description="active child", parent_id=stopped_leaf["id"])

    for agent in (stopped_parent, stopped_child, stopped_leaf):
        reg.update_agent(agent["id"], status="stopped")
    reg.update_agent(completed_parent["id"], status="completed")
    reg.update_agent(active_child["id"], status="running")

    dry_run = cleanup_completed_agents(reg, tmux, dry_run=True)
    assert set(dry_run["would_prune"]) == {stopped_parent["id"], stopped_child["id"]}
    assert {item["agent_id"] for item in dry_run["skipped"]} == {completed_parent["id"], stopped_leaf["id"]}

    result = cleanup_completed_agents(reg, tmux, dry_run=False)
    assert set(result["pruned"]) == {stopped_parent["id"], stopped_child["id"]}
    assert reg.maybe_agent(stopped_parent["id"]) is None
    assert reg.maybe_agent(stopped_child["id"]) is None
    assert reg.maybe_agent(completed_parent["id"]) is not None
    assert reg.maybe_agent(stopped_leaf["id"]) is not None
    assert reg.maybe_agent(active_child["id"]) is not None

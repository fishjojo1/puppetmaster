from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from .config import Config
from .errors import PuppetError
from .model import VALID_STATUSES, json_dumps, json_loads, new_id, now


SCHEMA = """
create table if not exists schema_version(
  version integer primary key,
  applied_at text not null
);
create table if not exists agents(
  id text primary key,
  parent_id text null,
  root_id text not null,
  role text not null,
  name text null,
  description text not null,
  initial_prompt_path text null,
  cwd text not null,
  tmux_session text not null,
  codex_session_id text null,
  status text not null,
  completion_status text null,
  depth integer not null,
  created_at text not null,
  updated_at text not null,
  started_at text null,
  last_turn_stopped_at text null,
  completed_at text null,
  stopped_at text null,
  exit_code integer null,
  termination_reason text null,
  log_path text not null,
  events_path text null,
  metadata_json text not null
);
create index if not exists agents_root_created_at_idx
on agents(root_id, created_at);
create index if not exists agents_parent_created_at_idx
on agents(parent_id, created_at);
create table if not exists events(
  id text primary key,
  agent_id text not null,
  parent_id text null,
  root_id text not null,
  type text not null,
  severity text not null,
  status text not null,
  created_at text not null,
  delivered_at text null,
  acknowledged_at text null,
  summary text not null,
  payload_json text not null,
  source text not null
);
create index if not exists events_agent_created_at_idx
on events(agent_id, created_at);
create table if not exists event_deliveries(
  id text primary key,
  event_id text not null,
  recipient_agent_id text not null,
  status text not null,
  created_at text not null,
  delivered_at text null,
  acknowledged_at text null
);
create index if not exists event_deliveries_recipient_status_idx
on event_deliveries(recipient_agent_id, status);
create table if not exists scheduled_wakeups(
  id text primary key,
  agent_id text not null,
  root_id text not null,
  status text not null,
  requested_at text not null,
  wake_at text not null,
  fired_at text null,
  cancelled_at text null,
  reason text not null,
  payload_json text not null
);
create index if not exists scheduled_wakeups_due_idx
on scheduled_wakeups(status, wake_at);
create table if not exists discord_channel_bindings(
  channel_id text primary key,
  root_agent_id text not null,
  guild_id text,
  created_at text not null,
  updated_at text not null
);
create unique index if not exists discord_channel_bindings_root_agent_id_idx
on discord_channel_bindings(root_agent_id);
create table if not exists outbound_human_messages(
  id text primary key,
  root_agent_id text not null,
  agent_id text not null,
  transport text not null check(transport in ('discord')),
  channel_id text not null,
  status text not null check(status in ('pending','delivered','failed')),
  message text not null,
  attachment_path text null,
  attachment_filename text null,
  attachment_size integer null,
  created_at text not null,
  claimed_at text,
  delivered_at text,
  failed_at text,
  error text
);
create index if not exists outbound_human_messages_transport_status_created_at_idx
on outbound_human_messages(transport, status, created_at);
create index if not exists outbound_human_messages_root_status_created_at_idx
on outbound_human_messages(root_agent_id, status, created_at);
create table if not exists discord_skills(
  name text primary key,
  prompt text not null,
  created_at text not null,
  updated_at text not null
);
create index if not exists discord_skills_updated_at_idx
on discord_skills(updated_at);
create table if not exists discord_inbound_messages(
  id text primary key,
  channel_id text not null,
  discord_message_id text not null,
  root_agent_id text null,
  status text not null,
  created_at text not null,
  updated_at text not null
);
create unique index if not exists discord_inbound_messages_channel_message_idx
on discord_inbound_messages(channel_id, discord_message_id);
"""


class Registry:
    def __init__(self, config: Config):
        self.config = config
        self.path = config.registry_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_outbound_attachment_columns(conn)
            conn.execute(
                "insert or ignore into schema_version(version, applied_at) values(1, ?)",
                (now(),),
            )

    def _ensure_outbound_attachment_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("pragma table_info(outbound_human_messages)").fetchall()}
        for name, definition in {
            "attachment_path": "text null",
            "attachment_filename": "text null",
            "attachment_size": "integer null",
            "claimed_at": "text null",
        }.items():
            if name not in columns:
                conn.execute(f"alter table outbound_human_messages add column {name} {definition}")

    def create_agent(self, fields: dict[str, Any]) -> dict[str, Any]:
        ts = now()
        data = {
            "id": fields["id"],
            "parent_id": fields.get("parent_id"),
            "root_id": fields["root_id"],
            "role": fields["role"],
            "name": fields.get("name"),
            "description": fields["description"],
            "initial_prompt_path": fields.get("initial_prompt_path"),
            "cwd": fields["cwd"],
            "tmux_session": fields["tmux_session"],
            "codex_session_id": fields.get("codex_session_id"),
            "status": fields.get("status", "starting"),
            "completion_status": fields.get("completion_status"),
            "depth": int(fields.get("depth", 0)),
            "created_at": ts,
            "updated_at": ts,
            "started_at": fields.get("started_at"),
            "last_turn_stopped_at": None,
            "completed_at": None,
            "stopped_at": None,
            "exit_code": fields.get("exit_code"),
            "termination_reason": fields.get("termination_reason"),
            "log_path": fields["log_path"],
            "events_path": fields.get("events_path"),
            "metadata_json": json_dumps(fields.get("metadata", {})),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into agents(
                  id,parent_id,root_id,role,name,description,initial_prompt_path,cwd,tmux_session,
                  codex_session_id,status,completion_status,depth,created_at,updated_at,started_at,
                  last_turn_stopped_at,completed_at,stopped_at,exit_code,termination_reason,log_path,
                  events_path,metadata_json
                ) values(
                  :id,:parent_id,:root_id,:role,:name,:description,:initial_prompt_path,:cwd,:tmux_session,
                  :codex_session_id,:status,:completion_status,:depth,:created_at,:updated_at,:started_at,
                  :last_turn_stopped_at,:completed_at,:stopped_at,:exit_code,:termination_reason,:log_path,
                  :events_path,:metadata_json
                )
                """,
                data,
            )
        return self.get_agent(data["id"])

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select * from agents where id=?", (agent_id,)).fetchone()
        if not row:
            raise PuppetError("not_found", f"agent not found: {agent_id}")
        return self._agent(row)

    def maybe_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from agents where id=?", (agent_id,)).fetchone()
        return self._agent(row) if row else None

    def list_agents(
        self,
        root_id: str | None = None,
        parent_id: str | None = None,
        status: str | None = None,
        include_dead: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if root_id:
            clauses.append("root_id=?")
            values.append(root_id)
        if parent_id:
            clauses.append("parent_id=?")
            values.append(parent_id)
        if status:
            clauses.append("status=?")
            values.append(status)
        if not include_dead:
            clauses.append("status not in ('dead','killed','stopped')")
        where = " where " + " and ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(f"select * from agents{where} order by created_at asc", values).fetchall()
        return [self._agent(row) for row in rows]

    def list_nonterminal_agents(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select * from agents where status in ('starting','running','idle','awaiting_input','unknown')"
            ).fetchall()
        return [self._agent(row) for row in rows]

    def count_agents(self) -> int:
        with self.connect() as conn:
            row = conn.execute("select count(*) as count from agents").fetchone()
        return int(row["count"])

    def update_agent(self, agent_id: str, **fields: Any) -> dict[str, Any]:
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            raise PuppetError("invalid_status", f"invalid status: {fields['status']}")
        fields["updated_at"] = now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [agent_id]
        with self.connect() as conn:
            conn.execute(f"update agents set {assignments} where id=?", values)
        return self.get_agent(agent_id)

    def delete_agents(self, agent_ids: list[str]) -> int:
        if not agent_ids:
            return 0
        placeholders = ",".join("?" for _ in agent_ids)
        with self.connect() as conn:
            conn.execute(f"delete from scheduled_wakeups where agent_id in ({placeholders})", agent_ids)
            conn.execute(
                f"""
                delete from event_deliveries
                where recipient_agent_id in ({placeholders})
                   or event_id in (select id from events where agent_id in ({placeholders}))
                """,
                agent_ids + agent_ids,
            )
            cursor = conn.execute(f"delete from agents where id in ({placeholders})", agent_ids)
        return int(cursor.rowcount)

    def clear_agent_state(self) -> dict[str, int]:
        with self.connect() as conn:
            counts = {
                "scheduled_wakeups": conn.execute("delete from scheduled_wakeups").rowcount,
                "event_deliveries": conn.execute("delete from event_deliveries").rowcount,
                "events": conn.execute("delete from events").rowcount,
                "outbound_human_messages": conn.execute("delete from outbound_human_messages").rowcount,
                "discord_channel_bindings": conn.execute("delete from discord_channel_bindings").rowcount,
                "agents": conn.execute("delete from agents").rowcount,
            }
        return {key: int(value) for key, value in counts.items()}

    def clear_event_log_state(self, *, dry_run: bool = False) -> dict[str, int]:
        with self.connect() as conn:
            if dry_run:
                counts = {
                    "event_deliveries": conn.execute("select count(*) from event_deliveries").fetchone()[0],
                    "events": conn.execute("select count(*) from events").fetchone()[0],
                }
            else:
                counts = {
                    "event_deliveries": conn.execute("delete from event_deliveries").rowcount,
                    "events": conn.execute("delete from events").rowcount,
                }
        return {key: int(value) for key, value in counts.items()}

    def append_event(
        self,
        agent_id: str,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
        source: str = "supervisor",
        status: str = "pending",
    ) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        event = {
            "id": new_id("evt"),
            "agent_id": agent_id,
            "parent_id": agent.get("parent_id"),
            "root_id": agent["root_id"],
            "type": event_type,
            "severity": severity,
            "status": status,
            "created_at": now(),
            "delivered_at": None,
            "acknowledged_at": None,
            "summary": summary,
            "payload_json": json_dumps(payload or {}),
            "source": source,
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into events(id,agent_id,parent_id,root_id,type,severity,status,created_at,
                  delivered_at,acknowledged_at,summary,payload_json,source)
                values(:id,:agent_id,:parent_id,:root_id,:type,:severity,:status,:created_at,
                  :delivered_at,:acknowledged_at,:summary,:payload_json,:source)
                """,
                event,
            )
        self._append_agent_event_file(agent, event)
        return self.get_event(event["id"])

    def get_event(self, event_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select * from events where id=?", (event_id,)).fetchone()
        if not row:
            raise PuppetError("not_found", f"event not found: {event_id}")
        return self._event(row)

    def list_events(self, agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if agent_id:
            sql = "select * from events where agent_id=? order by created_at desc limit ?"
            values = (agent_id, limit)
        else:
            sql = "select * from events order by created_at desc limit ?"
            values = (limit,)
        with self.connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [self._event(row) for row in rows]

    def queue_delivery(self, event_id: str, recipient_agent_id: str, coalesce: bool = False) -> dict[str, Any]:
        event = self.get_event(event_id)
        if coalesce:
            with self.connect() as conn:
                existing = conn.execute(
                    """
                    select d.* from event_deliveries d
                    join events e on e.id=d.event_id
                    where d.recipient_agent_id=? and d.status='pending'
                      and e.agent_id=? and e.type=?
                    order by d.created_at desc limit 1
                    """,
                    (recipient_agent_id, event["agent_id"], event["type"]),
                ).fetchone()
                if existing:
                    return self._delivery(existing)
        delivery = {
            "id": new_id("dlv"),
            "event_id": event_id,
            "recipient_agent_id": recipient_agent_id,
            "status": "pending",
            "created_at": now(),
            "delivered_at": None,
            "acknowledged_at": None,
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into event_deliveries(id,event_id,recipient_agent_id,status,created_at,delivered_at,acknowledged_at)
                values(:id,:event_id,:recipient_agent_id,:status,:created_at,:delivered_at,:acknowledged_at)
                """,
                delivery,
            )
        return delivery

    def pending_deliveries(self, recipient_agent_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        sql = """
            select d.*, e.agent_id, e.type, e.severity, e.summary, e.payload_json, e.created_at as event_created_at
            from event_deliveries d join events e on e.id=d.event_id
            where d.recipient_agent_id=? and d.status='pending'
            order by
              case e.severity when 'error' then 0 when 'warning' then 1 else 2 end,
              case e.type
                when 'agent.completed' then 0
                when 'agent.failed' then 0
                when 'agent.blocked' then 0
                when 'agent.cancelled' then 0
                when 'agent.killed' then 0
                when 'agent.stopped' then 0
                when 'agent.wait_over' then 2
                else 1
              end,
              e.created_at asc
        """
        params: list[Any] = [recipient_agent_id]
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._delivery(row) for row in rows]

    def count_pending_deliveries(self, recipient_agent_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "select count(*) from event_deliveries where recipient_agent_id=? and status='pending'",
                (recipient_agent_id,),
            ).fetchone()
        return int(row[0])

    def all_deliveries(self, recipient_agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "select d.*, e.agent_id, e.type, e.severity, e.summary, e.payload_json from event_deliveries d join events e on e.id=d.event_id"
        params: list[Any] = []
        if recipient_agent_id:
            sql += " where d.recipient_agent_id=?"
            params.append(recipient_agent_id)
        sql += " order by d.created_at desc limit ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._delivery(row) for row in rows]

    def mark_delivered(self, delivery_ids: list[str]) -> None:
        if not delivery_ids:
            return
        placeholders = ",".join("?" for _ in delivery_ids)
        ts = now()
        with self.connect() as conn:
            conn.execute(
                f"update event_deliveries set status='delivered', delivered_at=? where id in ({placeholders})",
                [ts] + delivery_ids,
            )

    def acknowledge_delivery(self, delivery_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update event_deliveries set status='acknowledged', acknowledged_at=? where id=?",
                (now(), delivery_id),
            )

    def create_wakeup(self, agent_id: str, wake_at: str, reason: str, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        wakeup = {
            "id": new_id("wkp"),
            "agent_id": agent_id,
            "root_id": agent["root_id"],
            "status": "scheduled",
            "requested_at": now(),
            "wake_at": wake_at,
            "fired_at": None,
            "cancelled_at": None,
            "reason": reason,
            "payload_json": json_dumps(payload),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into scheduled_wakeups(
                  id,agent_id,root_id,status,requested_at,wake_at,fired_at,cancelled_at,reason,payload_json
                ) values(
                  :id,:agent_id,:root_id,:status,:requested_at,:wake_at,:fired_at,:cancelled_at,:reason,:payload_json
                )
                """,
                wakeup,
            )
        return self.get_wakeup(wakeup["id"])

    def get_wakeup(self, wakeup_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select * from scheduled_wakeups where id=?", (wakeup_id,)).fetchone()
        if not row:
            raise PuppetError("not_found", f"wakeup not found: {wakeup_id}")
        return self._wakeup(row)

    def list_wakeups(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        due_at: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if agent_id:
            clauses.append("agent_id=?")
            values.append(agent_id)
        if status:
            clauses.append("status=?")
            values.append(status)
        if due_at:
            clauses.append("wake_at<=?")
            values.append(due_at)
        where = " where " + " and ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(f"select * from scheduled_wakeups{where} order by wake_at asc", values).fetchall()
        return [self._wakeup(row) for row in rows]

    def mark_wakeup_fired(self, wakeup_id: str) -> dict[str, Any] | None:
        ts = now()
        with self.connect() as conn:
            row = conn.execute("select * from scheduled_wakeups where id=?", (wakeup_id,)).fetchone()
            if not row:
                raise PuppetError("not_found", f"wakeup not found: {wakeup_id}")
            if row["status"] != "scheduled":
                return None
            cursor = conn.execute(
                "update scheduled_wakeups set status='fired', fired_at=? where id=? and status='scheduled'",
                (ts, wakeup_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_wakeup(wakeup_id)

    def cancel_wakeup(self, wakeup_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                "update scheduled_wakeups set status='cancelled', cancelled_at=? where id=? and status='scheduled'",
                (now(), wakeup_id),
            )
        return self.get_wakeup(wakeup_id)

    def bind_discord_channel(
        self,
        channel_id: str,
        root_agent_id: str,
        guild_id: str | None = None,
    ) -> dict[str, Any]:
        ts = now()
        binding = {
            "channel_id": channel_id,
            "root_agent_id": root_agent_id,
            "guild_id": guild_id,
            "created_at": ts,
            "updated_at": ts,
        }
        with self.connect() as conn:
            conn.execute(
                "delete from discord_channel_bindings where root_agent_id=? and channel_id<>?",
                (root_agent_id, channel_id),
            )
            conn.execute(
                """
                insert into discord_channel_bindings(channel_id,root_agent_id,guild_id,created_at,updated_at)
                values(:channel_id,:root_agent_id,:guild_id,:created_at,:updated_at)
                on conflict(channel_id) do update set
                  root_agent_id=excluded.root_agent_id,
                  guild_id=excluded.guild_id,
                  updated_at=excluded.updated_at
                """,
                binding,
            )
        found = self.discord_binding_for_channel(channel_id)
        if not found:
            raise PuppetError("not_found", f"discord binding not found for channel: {channel_id}")
        return found

    def unbind_discord_channel(self, channel_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("delete from discord_channel_bindings where channel_id=?", (channel_id,))
        return cursor.rowcount > 0

    def discord_binding_for_channel(self, channel_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from discord_channel_bindings where channel_id=?", (channel_id,)).fetchone()
        return self._discord_binding(row) if row else None

    def discord_binding_for_root(self, root_agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from discord_channel_bindings where root_agent_id=?",
                (root_agent_id,),
            ).fetchone()
        return self._discord_binding(row) if row else None

    def list_discord_bindings(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select * from discord_channel_bindings order by created_at asc, channel_id asc"
            ).fetchall()
        return [self._discord_binding(row) for row in rows]

    def enqueue_outbound_human_message(
        self,
        root_agent_id: str,
        agent_id: str,
        transport: str,
        channel_id: str,
        message: str,
        attachment_path: str | None = None,
        attachment_filename: str | None = None,
        attachment_size: int | None = None,
    ) -> dict[str, Any]:
        if transport != "discord":
            raise PuppetError("invalid_transport", f"unsupported outbound transport: {transport}")
        outbound = {
            "id": new_id("msg"),
            "root_agent_id": root_agent_id,
            "agent_id": agent_id,
            "transport": transport,
            "channel_id": channel_id,
            "status": "pending",
            "message": message,
            "attachment_path": attachment_path,
            "attachment_filename": attachment_filename,
            "attachment_size": attachment_size,
            "created_at": now(),
            "claimed_at": None,
            "delivered_at": None,
            "failed_at": None,
            "error": None,
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into outbound_human_messages(
                  id,root_agent_id,agent_id,transport,channel_id,status,message,
                  attachment_path,attachment_filename,attachment_size,
                  created_at,claimed_at,delivered_at,failed_at,error
                ) values(
                  :id,:root_agent_id,:agent_id,:transport,:channel_id,:status,:message,
                  :attachment_path,:attachment_filename,:attachment_size,
                  :created_at,:claimed_at,:delivered_at,:failed_at,:error
                )
                """,
                outbound,
            )
        return self._get_outbound_human_message(outbound["id"])

    def pending_outbound_human_messages(self, transport: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from outbound_human_messages
                where transport=? and status='pending' and claimed_at is null
                order by created_at asc, rowid asc
                limit ?
                """,
                (transport, int(limit)),
            ).fetchall()
        return [self._outbound_human_message(row) for row in rows]

    def claim_pending_outbound_human_messages(self, transport: str, limit: int = 20) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        ts = now()
        with self.connect() as conn:
            rows = conn.execute(
                """
                select id from outbound_human_messages
                where transport=? and status='pending' and claimed_at is null
                order by created_at asc, rowid asc
                limit ?
                """,
                (transport, int(limit)),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """
                    update outbound_human_messages
                    set claimed_at=?
                    where id=? and status='pending' and claimed_at is null
                    """,
                    (ts, row["id"]),
                )
                if cursor.rowcount == 1:
                    found = conn.execute("select * from outbound_human_messages where id=?", (row["id"],)).fetchone()
                    if found is not None:
                        claimed.append(self._outbound_human_message(found))
        return claimed

    def mark_outbound_human_message_delivered(self, message_id: str) -> dict[str, Any]:
        ts = now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update outbound_human_messages
                set status='delivered', delivered_at=?, failed_at=null, error=null
                where id=?
                """,
                (ts, message_id),
            )
        if cursor.rowcount != 1:
            raise PuppetError("not_found", f"outbound human message not found: {message_id}")
        return self._get_outbound_human_message(message_id)

    def claim_discord_inbound_message(
        self,
        channel_id: str,
        discord_message_id: str | None,
        root_agent_id: str | None = None,
    ) -> bool:
        message_id = (discord_message_id or "").strip()
        if not message_id:
            return True
        ts = now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                insert or ignore into discord_inbound_messages(
                  id,channel_id,discord_message_id,root_agent_id,status,created_at,updated_at
                ) values(?,?,?,?,?,?,?)
                """,
                (new_id("din"), channel_id, message_id, root_agent_id, "claimed", ts, ts),
            )
        return cursor.rowcount == 1

    def mark_outbound_human_message_failed(self, message_id: str, error: str) -> dict[str, Any]:
        ts = now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update outbound_human_messages
                set status='failed', delivered_at=null, failed_at=?, error=?
                where id=?
                """,
                (ts, error, message_id),
            )
        if cursor.rowcount != 1:
            raise PuppetError("not_found", f"outbound human message not found: {message_id}")
        return self._get_outbound_human_message(message_id)

    def upsert_discord_skill(self, name: str, prompt: str) -> dict[str, Any]:
        ts = now()
        skill = {
            "name": name,
            "prompt": prompt,
            "created_at": ts,
            "updated_at": ts,
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into discord_skills(name,prompt,created_at,updated_at)
                values(:name,:prompt,:created_at,:updated_at)
                on conflict(name) do update set
                  prompt=excluded.prompt,
                  updated_at=excluded.updated_at
                """,
                skill,
            )
        found = self.discord_skill(name)
        if not found:
            raise PuppetError("not_found", f"discord skill not found: {name}")
        return found

    def discord_skill(self, name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from discord_skills where name=?", (name,)).fetchone()
        return self._discord_skill(row) if row else None

    def list_discord_skills(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from discord_skills order by name asc").fetchall()
        return [self._discord_skill(row) for row in rows]

    def delete_discord_skill(self, name: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("delete from discord_skills where name=?", (name,))
        return cursor.rowcount > 0

    def children(self, agent_id: str) -> list[dict[str, Any]]:
        return self.list_agents(parent_id=agent_id)

    def descendants(self, agent_id: str) -> set[str]:
        result: set[str] = set()
        stack = [agent_id]
        while stack:
            current = stack.pop()
            for child in self.children(current):
                result.add(child["id"])
                stack.append(child["id"])
        return result

    def count_agents(self, root_id: str | None = None) -> int:
        with self.connect() as conn:
            if root_id:
                row = conn.execute("select count(*) c from agents where root_id=?", (root_id,)).fetchone()
            else:
                row = conn.execute("select count(*) c from agents").fetchone()
        return int(row["c"])

    def latest_event_time(self, agent_id: str, event_type: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "select created_at from events where agent_id=? and type=? order by created_at desc limit 1",
                (agent_id, event_type),
            ).fetchone()
        return row["created_at"] if row else None

    def count_events(self, agent_id: str | None = None, event_type: str | None = None) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if event_type:
            clauses.append("type=?")
            params.append(event_type)
        where = " where " + " and ".join(clauses) if clauses else ""
        with self.connect() as conn:
            row = conn.execute(f"select count(*) c from events{where}", params).fetchone()
        return int(row["c"])

    def _append_agent_event_file(self, agent: dict[str, Any], event: dict[str, Any]) -> None:
        path = agent.get("events_path")
        if not path:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json_dumps(event) + "\n")

    def _agent(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = json_loads(data.pop("metadata_json", "{}"))
        return data

    def _event(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json_loads(data.pop("payload_json", "{}"))
        return data

    def _delivery(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "payload_json" in data:
            data["payload"] = json_loads(data.pop("payload_json", "{}"))
        return data

    def _wakeup(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json_loads(data.pop("payload_json", "{}"))
        return data

    def _discord_binding(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _discord_skill(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _get_outbound_human_message(self, message_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select * from outbound_human_messages where id=?", (message_id,)).fetchone()
        if not row:
            raise PuppetError("not_found", f"outbound human message not found: {message_id}")
        return self._outbound_human_message(row)

    def _outbound_human_message(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

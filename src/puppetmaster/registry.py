from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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
create table if not exists event_deliveries(
  id text primary key,
  event_id text not null,
  recipient_agent_id text not null,
  status text not null,
  created_at text not null,
  delivered_at text null,
  acknowledged_at text null
);
"""


class Registry:
    def __init__(self, config: Config):
        self.config = config
        self.path = config.registry_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "insert or ignore into schema_version(version, applied_at) values(1, ?)",
                (now(),),
            )

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

    def update_agent(self, agent_id: str, **fields: Any) -> dict[str, Any]:
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            raise PuppetError("invalid_status", f"invalid status: {fields['status']}")
        fields["updated_at"] = now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [agent_id]
        with self.connect() as conn:
            conn.execute(f"update agents set {assignments} where id=?", values)
        return self.get_agent(agent_id)

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
            order by case e.severity when 'error' then 0 when 'warning' then 1 else 2 end, e.created_at asc
        """
        params: list[Any] = [recipient_agent_id]
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._delivery(row) for row in rows]

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


import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable


class PlanError(ValueError):
    pass


class StateStore:
    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], int | float] = time.time,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS plans (
              plan_id TEXT PRIMARY KEY,
              action TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              consumed_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              media_type TEXT NOT NULL,
              qas_task_name TEXT,
              aria2_gids_json TEXT NOT NULL DEFAULT '[]',
              aria2_dir TEXT NOT NULL DEFAULT '',
              staging_path TEXT NOT NULL,
              final_path TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(tasks)"
            ).fetchall()
        }
        if "aria2_dir" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN aria2_dir TEXT NOT NULL DEFAULT ''"
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_plan(
        self,
        action: str,
        payload: dict,
        ttl_seconds: int = 1800,
    ) -> str:
        now = int(self.clock())
        plan_id = f"plan-{uuid.uuid4().hex}"
        self.connection.execute(
            """
            INSERT INTO plans
              (plan_id, action, payload_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                action,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now + ttl_seconds,
            ),
        )
        self.connection.commit()
        return plan_id

    def consume_plan(self, plan_id: str, action: str) -> dict:
        now = int(self.clock())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise PlanError("plan not found")
            if row["action"] != action:
                raise PlanError("plan action mismatch")
            if row["consumed_at"] is not None:
                raise PlanError("plan already consumed")
            if row["expires_at"] < now:
                raise PlanError("plan expired")

            self.connection.execute(
                "UPDATE plans SET consumed_at = ? WHERE plan_id = ?",
                (now, plan_id),
            )
            self.connection.commit()
            return json.loads(row["payload_json"])
        except Exception:
            self.connection.rollback()
            raise

    def upsert_task(self, task: dict) -> None:
        now = int(self.clock())
        gids = list(task.get("aria2_gids", []))
        self.connection.execute(
            """
            INSERT INTO tasks (
              task_id, title, media_type, qas_task_name, aria2_gids_json, aria2_dir,
              staging_path, final_path, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              title = excluded.title,
              media_type = excluded.media_type,
              qas_task_name = excluded.qas_task_name,
              aria2_gids_json = excluded.aria2_gids_json,
              aria2_dir = excluded.aria2_dir,
              staging_path = excluded.staging_path,
              final_path = excluded.final_path,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                task["task_id"],
                task["title"],
                task["media_type"],
                task.get("qas_task_name"),
                json.dumps(gids, separators=(",", ":")),
                task.get("aria2_dir", ""),
                task["staging_path"],
                task["final_path"],
                task["status"],
                now,
                now,
            ),
        )
        self.connection.commit()

    def list_tasks(self, states: tuple[str, ...] = ()) -> list[dict]:
        params: tuple = ()
        sql = "SELECT * FROM tasks"
        if states:
            placeholders = ",".join("?" for _ in states)
            sql += f" WHERE status IN ({placeholders})"
            params = states
        sql += " ORDER BY created_at DESC, task_id"
        rows = self.connection.execute(sql, params).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "title": row["title"],
                "media_type": row["media_type"],
                "qas_task_name": row["qas_task_name"],
                "aria2_gids": json.loads(row["aria2_gids_json"]),
                "aria2_dir": row["aria2_dir"],
                "staging_path": row["staging_path"],
                "final_path": row["final_path"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_task(self, task_id: str) -> dict | None:
        return next(
            (
                task
                for task in self.list_tasks()
                if task["task_id"] == task_id
            ),
            None,
        )

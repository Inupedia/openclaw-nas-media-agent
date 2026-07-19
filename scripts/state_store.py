import hashlib
import hmac
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
              schema_version INTEGER NOT NULL DEFAULT 1,
              payload_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              consumed_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS candidates (
              candidate_id TEXT PRIMARY KEY,
              payload_json TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              title_key TEXT NOT NULL DEFAULT '',
              media_type TEXT NOT NULL,
              qas_task_name TEXT,
              aria2_gids_json TEXT NOT NULL DEFAULT '[]',
              episode_keys_json TEXT NOT NULL DEFAULT '[]',
              expected_manifest_json TEXT NOT NULL DEFAULT '{}',
              aria2_dir TEXT NOT NULL DEFAULT '',
              cloud_path TEXT NOT NULL DEFAULT '',
              recover_attempts INTEGER NOT NULL DEFAULT 0,
              recovery_json TEXT NOT NULL DEFAULT '{}',
              staging_path TEXT NOT NULL,
              final_path TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            """
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(tasks)"
            ).fetchall()
        }
        plan_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(plans)"
            ).fetchall()
        }
        if "schema_version" not in plan_columns:
            self.connection.execute(
                "ALTER TABLE plans ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1"
            )
        if "payload_hash" not in plan_columns:
            self.connection.execute(
                "ALTER TABLE plans ADD COLUMN payload_hash TEXT NOT NULL DEFAULT ''"
            )
        if "aria2_dir" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN aria2_dir TEXT NOT NULL DEFAULT ''"
            )
        if "cloud_path" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN cloud_path TEXT NOT NULL DEFAULT ''"
            )
        if "recover_attempts" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN recover_attempts "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "recovery_json" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN recovery_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        if "title_key" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN title_key TEXT NOT NULL DEFAULT ''"
            )
        if "episode_keys_json" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN episode_keys_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "expected_manifest_json" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN expected_manifest_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_title_status
            ON tasks(title_key, status)
            """
        )
        for row in self.connection.execute(
            "SELECT plan_id, payload_json FROM plans WHERE payload_hash = ''"
        ).fetchall():
            payload = json.loads(row["payload_json"])
            self.connection.execute(
                "UPDATE plans SET payload_hash = ? WHERE plan_id = ?",
                (self._payload_hash(payload), row["plan_id"]),
            )
        self.connection.commit()

    @staticmethod
    def _canonical_payload(payload: dict) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _payload_hash(cls, payload: dict) -> str:
        return hashlib.sha256(
            cls._canonical_payload(payload).encode("utf-8")
        ).hexdigest()

    def create_candidate(
        self,
        payload: dict,
        ttl_seconds: int = 900,
    ) -> str:
        now = int(self.clock())
        candidate_id = f"candidate-{uuid.uuid4().hex}"
        self.connection.execute(
            """
            INSERT INTO candidates
              (candidate_id, payload_json, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                candidate_id,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now + ttl_seconds,
            ),
        )
        self.connection.commit()
        return candidate_id

    def get_candidate(self, candidate_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise PlanError("candidate not found")
        if row["expires_at"] < int(self.clock()):
            raise PlanError("candidate expired")
        return json.loads(row["payload_json"])

    def update_candidate(self, candidate_id: str, payload: dict) -> None:
        self.get_candidate(candidate_id)
        self.connection.execute(
            "UPDATE candidates SET payload_json = ? WHERE candidate_id = ?",
            (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                candidate_id,
            ),
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
        payload_json = self._canonical_payload(payload)
        payload_hash = self._payload_hash(payload)
        self.connection.execute(
            """
            INSERT INTO plans
              (
                plan_id, action, schema_version, payload_json, payload_hash,
                created_at, expires_at
              )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                action,
                1,
                payload_json,
                payload_hash,
                now,
                now + ttl_seconds,
            ),
        )
        self.connection.commit()
        return plan_id

    def _validate_plan_row(self, row, action: str, now: int) -> dict:
        if row is None:
            raise PlanError("plan not found")
        if row["action"] != action:
            raise PlanError("plan action mismatch")
        if row["consumed_at"] is not None:
            raise PlanError("plan already consumed")
        if row["expires_at"] < now:
            raise PlanError("plan expired")
        payload = json.loads(row["payload_json"])
        actual_hash = self._payload_hash(payload)
        if not hmac.compare_digest(row["payload_hash"], actual_hash):
            raise PlanError("plan integrity check failed")
        return payload

    def read_plan(self, plan_id: str, action: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        return self._validate_plan_row(row, action, int(self.clock()))

    def consume_plan(self, plan_id: str, action: str) -> dict:
        now = int(self.clock())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            payload = self._validate_plan_row(row, action, now)

            self.connection.execute(
                "UPDATE plans SET consumed_at = ? WHERE plan_id = ?",
                (now, plan_id),
            )
            self.connection.commit()
            return payload
        except Exception:
            self.connection.rollback()
            raise

    def upsert_task(self, task: dict) -> None:
        now = int(self.clock())
        gids = list(task.get("aria2_gids", []))
        episode_keys = list(task.get("episode_keys", []))
        expected_manifest = task.get("expected_manifest")
        if expected_manifest is None:
            expected_manifest = {}
        if not isinstance(expected_manifest, dict):
            raise PlanError("expected_manifest must be an object")
        recovery = task.get("recovery")
        if recovery is None:
            recovery = {}
        if not isinstance(recovery, dict):
            raise PlanError("recovery must be an object")
        self.connection.execute(
            """
            INSERT INTO tasks (
              task_id, title, title_key, media_type, qas_task_name,
              aria2_gids_json, episode_keys_json, expected_manifest_json,
              aria2_dir, cloud_path, recover_attempts, recovery_json,
              staging_path, final_path, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              title = excluded.title,
              title_key = excluded.title_key,
              media_type = excluded.media_type,
              qas_task_name = excluded.qas_task_name,
              aria2_gids_json = excluded.aria2_gids_json,
              episode_keys_json = excluded.episode_keys_json,
              expected_manifest_json = excluded.expected_manifest_json,
              aria2_dir = excluded.aria2_dir,
              cloud_path = excluded.cloud_path,
              recover_attempts = excluded.recover_attempts,
              recovery_json = excluded.recovery_json,
              staging_path = excluded.staging_path,
              final_path = excluded.final_path,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                task["task_id"],
                task["title"],
                task.get("title_key", str(task["title"]).casefold()),
                task["media_type"],
                task.get("qas_task_name"),
                json.dumps(gids, separators=(",", ":")),
                json.dumps(episode_keys, separators=(",", ":")),
                json.dumps(expected_manifest, ensure_ascii=False, separators=(",", ":")),
                task.get("aria2_dir", ""),
                task.get("cloud_path", ""),
                int(task.get("recover_attempts") or 0),
                json.dumps(recovery, ensure_ascii=False, separators=(",", ":")),
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
        results = []
        for row in rows:
            keys = row.keys()
            manifest_raw = (
                row["expected_manifest_json"]
                if "expected_manifest_json" in keys
                else "{}"
            )
            try:
                expected_manifest = json.loads(manifest_raw or "{}")
            except json.JSONDecodeError:
                expected_manifest = {}
            try:
                recovery = (
                    json.loads(row["recovery_json"])
                    if "recovery_json" in keys and row["recovery_json"]
                    else {}
                )
            except json.JSONDecodeError:
                recovery = {}
            if not isinstance(recovery, dict):
                recovery = {}
            results.append(
                {
                    "task_id": row["task_id"],
                    "title": row["title"],
                    "title_key": row["title_key"],
                    "media_type": row["media_type"],
                    "qas_task_name": row["qas_task_name"],
                    "aria2_gids": json.loads(row["aria2_gids_json"]),
                    "episode_keys": json.loads(row["episode_keys_json"]),
                    "expected_manifest": expected_manifest
                    if isinstance(expected_manifest, dict)
                    else {},
                    "aria2_dir": row["aria2_dir"],
                    "cloud_path": row["cloud_path"] if "cloud_path" in keys else "",
                    "recover_attempts": int(
                        row["recover_attempts"] if "recover_attempts" in keys else 0
                    ),
                    "recovery": recovery,
                    "staging_path": row["staging_path"],
                    "final_path": row["final_path"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return results

    def get_task(self, task_id: str) -> dict | None:
        return next(
            (
                task
                for task in self.list_tasks()
                if task["task_id"] == task_id
            ),
            None,
        )

    def pending_episode_refs(
        self,
        title_key: str,
    ) -> set[tuple[int, int, str | None]]:
        now = int(self.clock())
        refs: set[tuple[int, int, str | None]] = set()
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM plans
            WHERE consumed_at IS NULL AND expires_at >= ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("titleKey") != title_key:
                continue
            for item in payload.get("incremental", {}).get("newEpisodes", []):
                refs.add(
                    (
                        int(item["season"]),
                        int(item["episode"]),
                        item.get("special"),
                    )
                )

        active_states = {
            "starting",
            "submitted",
            "active",
            "waiting",
            "paused",
            "downloaded",
            "verified",
            "complete",
            "ready",
            "quarantined",
            "organizing",
        }
        for task in self.list_tasks():
            if (
                task["title_key"] != title_key
                or task["status"] not in active_states
            ):
                continue
            for item in task.get("episode_keys", []):
                refs.add(
                    (
                        int(item["season"]),
                        int(item["episode"]),
                        item.get("special"),
                    )
                )
        return refs

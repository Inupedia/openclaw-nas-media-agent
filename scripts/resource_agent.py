#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

from aria2_client import Aria2Client
from library_catalog import LibraryCatalog
from media_service import MediaService
from output_contract import failure, success
from planner import DownloadPlanner, PlanningError
from qas_client import ClientError, QasClient
from state_store import PlanError, StateStore


class AgentError(RuntimeError):
    pass


class CliUsageError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CliUsageError("invalid command or arguments")


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class ResourceAgent:
    def __init__(self, *, store: StateStore, qas, aria):
        self.store = store
        self.qas = qas
        self.aria = aria

    def _aria_items(self) -> list[dict]:
        return [
            *self.aria.tell_active(),
            *self.aria.tell_waiting(),
            *self.aria.tell_stopped(),
        ]

    def _synchronize(self) -> dict[str, list[dict]]:
        by_dir: dict[str, list[dict]] = {}
        for item in self._aria_items():
            directory = str(item.get("dir", "")).rstrip("/")
            by_dir.setdefault(directory, []).append(item)

        for task in self.store.list_tasks():
            items = by_dir.get(task["aria2_dir"].rstrip("/"), [])
            if not items:
                continue
            task["aria2_gids"] = [
                str(item["gid"]) for item in items if item.get("gid")
            ]
            statuses = {str(item.get("status", "")) for item in items}
            for status in ("active", "waiting", "paused", "error", "complete"):
                if status in statuses:
                    task["status"] = status
                    break
            self.store.upsert_task(task)
        return by_dir

    def _summary(self, task: dict, items: list[dict]) -> dict:
        total = sum(_int(item.get("totalLength")) for item in items)
        completed = sum(_int(item.get("completedLength")) for item in items)
        speed = sum(_int(item.get("downloadSpeed")) for item in items)
        progress = round(completed * 100 / total, 2) if total else 0.0
        remaining = max(0, total - completed)
        eta = int(remaining / speed) if speed else None
        errors = [
            item.get("errorMessage")
            for item in items
            if item.get("errorMessage")
        ]
        return {
            "taskId": task["task_id"],
            "title": task["title"],
            "mediaType": task["media_type"],
            "status": task["status"],
            "aria2Gids": task["aria2_gids"],
            "totalBytes": total,
            "completedBytes": completed,
            "downloadSpeed": speed,
            "progress": progress,
            "etaSeconds": eta,
            "stagingPath": task["staging_path"],
            "finalPath": task["final_path"],
            "errors": errors,
        }

    def downloads_list(self) -> dict:
        by_dir = self._synchronize()
        tasks = self.store.list_tasks()
        return {
            "tasks": [
                self._summary(
                    task,
                    by_dir.get(task["aria2_dir"].rstrip("/"), []),
                )
                for task in tasks
            ]
        }

    def downloads_show(self, task_id: str) -> dict:
        tasks = self.downloads_list()["tasks"]
        for task in tasks:
            if task["taskId"] == task_id:
                return {"task": task}
        raise AgentError("task not found")

    def downloads_control(self, task_id: str, action: str) -> dict:
        by_dir = self._synchronize()
        task = self.store.get_task(task_id)
        if task is None:
            raise AgentError("task not found")
        if not task["aria2_gids"]:
            raise AgentError("no managed aria2 task for this resource")
        operations = {
            "pause": self.aria.pause,
            "resume": self.aria.unpause,
        }
        if action not in (*operations, "cancel"):
            raise AgentError("unsupported download control")
        if action == "cancel":
            statuses = {
                str(item.get("gid")): str(item.get("status", ""))
                for item in by_dir.get(task["aria2_dir"].rstrip("/"), [])
            }
            results = [
                (
                    self.aria.remove_result(gid)
                    if statuses.get(gid) in {"complete", "error", "removed"}
                    else self.aria.remove(gid)
                )
                for gid in task["aria2_gids"]
            ]
        else:
            results = [
                operations[action](gid) for gid in task["aria2_gids"]
            ]
        task["status"] = {
            "pause": "paused",
            "resume": "waiting",
            "cancel": "cancelled",
        }[action]
        self.store.upsert_task(task)
        return {
            "taskId": task_id,
            "action": action,
            "aria2Gids": results,
            "status": task["status"],
        }

    def check_ready(self, staging_roots: list[Path]) -> dict:
        try:
            config = self.qas.get_config()
        except Exception as error:
            return {
                "ok": False,
                "nextAction": "configure_qas",
                "error": str(error),
            }
        if not config.get("cookie"):
            return {
                "ok": False,
                "nextAction": "configure_qas_cookie",
                "error": "QAS cookie is not configured",
            }
        try:
            aria_version = self.aria.get_version()
        except Exception as error:
            return {
                "ok": False,
                "nextAction": "configure_aria2",
                "error": str(error),
            }
        for root in staging_roots:
            if not root.is_dir() or not os.access(root, os.W_OK):
                return {
                    "ok": False,
                    "nextAction": "fix_path_permissions",
                    "error": f"staging path is not writable: {root}",
                }
        return {
            "ok": True,
            "nextAction": "ready",
            "data": {"aria2Version": aria_version.get("version")},
        }


def _load_runtime():
    base_dir = Path(__file__).resolve().parents[1]
    with open(
        base_dir / "config" / "routing.json",
        encoding="utf-8",
    ) as routing_file:
        routing = json.load(routing_file)
    required = [
        "QAS_BASE_URL",
        "QAS_TOKEN",
        "ARIA2_RPC_URL",
        "ARIA2_RPC_SECRET",
    ]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise AgentError(f"missing environment: {', '.join(missing)}")
    state_path = Path(
        os.environ.get(
            "RESOURCE_AGENT_STATE_DB",
            str(base_dir / "data" / "state.db"),
        )
    )
    store = StateStore(state_path)
    qas = QasClient(os.environ["QAS_BASE_URL"], os.environ["QAS_TOKEN"])
    aria = Aria2Client(
        os.environ["ARIA2_RPC_URL"],
        os.environ["ARIA2_RPC_SECRET"],
    )
    planner = DownloadPlanner(
        qas=qas,
        store=store,
        routing=routing,
    )
    return routing, store, qas, aria, planner


def parse_args(argv) -> argparse.Namespace:
    parser = SafeArgumentParser(
        prog="mediactl",
        add_help=True,
        argument_default=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-ready")
    library = subparsers.add_parser("library")
    library_sub = library.add_subparsers(
        dest="library_command",
        required=True,
    )
    lookup = library_sub.add_parser("lookup")
    lookup.add_argument("query")
    lookup.add_argument(
        "--media-type",
        choices=("movie", "tv", "anime", "documentary", "show", "other"),
    )
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument(
        "--media-type",
        choices=("movie", "tv", "anime", "documentary", "show", "other"),
    )
    search.add_argument("--update", action="store_true")
    preview = subparsers.add_parser("preview")
    preview.add_argument("candidate_id")
    plan = subparsers.add_parser("plan")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_download = plan_sub.add_parser("download")
    plan_download.add_argument("candidate_id")
    execute = subparsers.add_parser("execute")
    execute.add_argument("plan_id")
    execute.add_argument("--confirmed", action="store_true")
    downloads = subparsers.add_parser("downloads")
    downloads_sub = downloads.add_subparsers(dest="download_command", required=True)
    downloads_sub.add_parser("list")
    show = downloads_sub.add_parser("show")
    show.add_argument("task_id")
    for command in ("pause", "resume", "cancel"):
        control = downloads_sub.add_parser(command)
        control.add_argument("task_id")

    return parser.parse_args(list(argv))


def emit(result: dict, *, stream=None) -> None:
    stream = sys.stdout if stream is None else stream
    stream.write(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def _default_service_factory(routing, store, qas):
    roots = {
        media_type: Path(route["final_root"])
        for media_type, route in routing.items()
        if isinstance(route, dict) and route.get("final_root")
    }
    return MediaService(LibraryCatalog(roots), qas, store)


def main(
    argv=None,
    *,
    runtime_loader=_load_runtime,
    service_factory=_default_service_factory,
    stream=None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    store = None
    try:
        args = parse_args(arguments)
        routing, store, qas, aria, planner = runtime_loader()
        agent = ResourceAgent(store=store, qas=qas, aria=aria)
        service = service_factory(routing, store, qas)
        if args.command == "check-ready":
            roots = [
                Path(route["staging_root"])
                for route in routing.values()
                if isinstance(route, dict) and route.get("staging_root")
            ]
            unique_roots = list(dict.fromkeys(roots))
            ready = agent.check_ready(unique_roots)
            if ready.get("ok"):
                result = success(
                    ready.get("data"),
                    next_action=ready.get("nextAction", "ready"),
                )
            else:
                result = failure(
                    "NOT_READY",
                    ready.get("error", "runtime is not ready"),
                    next_action=ready.get("nextAction", "review_error"),
                )
        elif args.command == "library":
            result = success(
                {
                    "local": service.catalog.lookup(
                        args.query,
                        getattr(args, "media_type", None),
                    )
                },
                terminal=True,
                next_action="local_lookup_complete",
            )
        elif args.command == "search":
            result = service.search(
                args.query,
                getattr(args, "media_type", None),
                update=getattr(args, "update", False),
            )
        elif args.command == "preview":
            result = service.preview(args.candidate_id)
        elif args.command == "plan":
            result = success(
                planner.plan_selected(args.candidate_id),
                next_action="review_plan",
            )
        elif args.command == "execute":
            result = success(
                planner.execute(
                    args.plan_id,
                    confirmed=args.confirmed,
                ),
                next_action="monitor_download",
            )
        elif args.download_command == "list":
            result = success(
                agent.downloads_list(),
                next_action="none",
            )
        elif args.download_command == "show":
            result = success(
                agent.downloads_show(args.task_id),
                next_action="none",
            )
        else:
            result = success(
                agent.downloads_control(
                    args.task_id,
                    args.download_command,
                ),
                next_action="none",
            )
    except CliUsageError as error:
        result = failure(
            "INVALID_COMMAND",
            str(error),
            next_action="use_mediactl_help",
        )
    except (AgentError, ClientError, PlanningError, PlanError) as error:
        result = failure(
            (
                "CLIENT_ERROR"
                if isinstance(error, ClientError)
                else "PLANNING_ERROR"
                if isinstance(error, (PlanningError, PlanError))
                else "AGENT_ERROR"
            ),
            str(error),
            next_action="review_error",
        )
    finally:
        if store is not None:
            store.close()

    emit(result, stream=stream)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

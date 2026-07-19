#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from aria2_client import Aria2Client
from library_catalog import LibraryCatalog
from media_service import MediaService
from organizer import DownloadValidator, OrganizeError, Organizer
from output_contract import failure, success
from pansou_client import PanSouClient
from jiaofu_client import JiaofuClient, JiaofuError
from path_guard import PathGuard
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


def _aggregate_aria_status(items: list[dict]) -> str:
    """Aggregate multi-GID aria2 status.

    ``complete`` requires *every* GID to be complete. Mixed complete+error becomes
    ``partial_failed`` so validate/organize cannot treat a half-success as done.
    Live states still win over terminal ones.
    """
    if not items:
        return "submitted"
    statuses = {str(item.get("status", "")) for item in items}
    for status in ("active", "waiting", "paused"):
        if status in statuses:
            return status
    if statuses and statuses <= {"complete"}:
        return "complete"
    if "error" in statuses and "complete" in statuses:
        return "partial_failed"
    if "error" in statuses:
        return "error"
    return "submitted"


def _pansou_limit(value: str | None) -> int:
    try:
        parsed = int(value or "50")
    except ValueError:
        return 50
    return parsed if 1 <= parsed <= 100 else 50


def _jiaofu_limit(value: str | None) -> int:
    try:
        parsed = int(value or "20")
    except ValueError:
        return 20
    return parsed if 1 <= parsed <= 50 else 20


def _optional_jiaofu(base_dir: Path):
    del base_dir  # reserved for future relative-path resolution
    state = os.environ.get("JIAOFU_STORAGE_STATE", "").strip()
    if not state:
        return None
    try:
        return JiaofuClient(
            state,
            max_candidates=_jiaofu_limit(
                os.environ.get("JIAOFU_MAX_CANDIDATES")
            ),
        )
    except JiaofuError:
        return None


SYNCABLE_STATES = {
    "starting",
    "submitted",
    "active",
    "waiting",
    "paused",
    "partial_failed",
    "error",
}
TERMINAL_DOWNLOAD_STATES = {
    "ready",
    "quarantined",
    "organized",
    "cancelled",
}


class ResourceAgent:
    def __init__(self, *, store: StateStore, qas, aria, routing: dict | None = None):
        self.store = store
        self.qas = qas
        self.aria = aria
        self.routing = routing or {}

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
            if task["status"] not in SYNCABLE_STATES:
                continue
            items = by_dir.get(task["aria2_dir"].rstrip("/"), [])
            if not items:
                continue
            task["aria2_gids"] = [
                str(item["gid"]) for item in items if item.get("gid")
            ]
            task["status"] = _aggregate_aria_status(items)
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
        error_codes = sorted(
            {
                str(item.get("errorCode"))
                for item in items
                if str(item.get("errorCode") or "") not in {"", "0"}
            }
        )
        staging = Path(task["staging_path"])
        staging_exists = staging.is_dir()
        staging_files = 0
        if staging_exists:
            try:
                staging_files = sum(1 for _ in staging.rglob("*") if _.is_file())
            except OSError:
                staging_files = 0
        notes = []
        if (
            task["status"] == "submitted"
            and not task["aria2_gids"]
            and staging_files == 0
        ):
            notes.append(
                "transfer_idle: QAS submitted but no aria2 activity / staging files yet"
            )
        if task["status"] == "complete" and staging_files > 0:
            notes.append(
                "staging_only: files are in .incoming; run validate + organize before Theater"
            )
        if "18" in error_codes:
            notes.append(
                "aria2_error_18: Download aborted - often missing/unwritable "
                "staging dir under /volume2/downloads/.incoming (chmod 775) "
                "or Quark/QAS failed to push files"
            )
        if task["status"] == "error" and staging_files == 0 and not staging_exists:
            notes.append(
                "staging_missing: aria2 target dir was never created on disk"
            )
        mixed = {str(item.get("status", "")) for item in items}
        if task["status"] == "partial_failed" or (
            "complete" in mixed and "error" in mixed
        ):
            notes.append(
                "aria2_partial_failed: not all GIDs complete - "
                "task cannot validate/organize until every transfer finishes"
            )
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
            "stagingExists": staging_exists,
            "stagingFileCount": staging_files,
            "finalPath": task["final_path"],
            "errors": errors,
            "errorCodes": error_codes,
            "notes": notes,
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
        path_check = self._check_library_roots()
        if not path_check.get("ok"):
            return path_check
        probe = self._probe_aria2_write(staging_roots[0] if staging_roots else None)
        if not probe.get("ok"):
            return probe
        return {
            "ok": True,
            "nextAction": "ready",
            "data": {
                "aria2Version": aria_version.get("version"),
                "aria2WriteProbe": probe.get("data"),
                "pathChecks": path_check.get("data"),
            },
        }

    def _check_library_roots(self) -> dict:
        if not self.routing:
            return {"ok": True, "data": {"skipped": True}}
        from routing_paths import (
            final_roots,
            organizing_root,
            protected_roots,
        )

        missing = []
        for root in (*protected_roots(self.routing), *final_roots(self.routing)):
            if not root.is_dir():
                missing.append(str(root))
        if missing:
            return {
                "ok": False,
                "nextAction": "mount_media_libraries",
                "error": "required media library roots missing: " + ", ".join(missing),
            }
        org = organizing_root(self.routing)
        try:
            org.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return {
                "ok": False,
                "nextAction": "mount_organizing_root",
                "error": f"organizing root unavailable: {org}: {error}",
            }
        # Movie finals live on volume3; organizing must share that device.
        movie_final = None
        for media_type in ("movie",):
            route = self.routing.get(media_type)
            if isinstance(route, dict) and route.get("final_root"):
                movie_final = Path(str(route["final_root"]))
                break
        same_device = None
        if movie_final is not None and movie_final.parent.is_dir():
            try:
                same_device = org.stat().st_dev == movie_final.parent.stat().st_dev
            except OSError:
                same_device = False
            if same_device is False:
                return {
                    "ok": False,
                    "nextAction": "mount_organizing_root",
                    "error": (
                        f"organizing root {org} is not on the same filesystem as "
                        f"{movie_final.parent}; cross-disk os.replace will fail"
                    ),
                }
        return {
            "ok": True,
            "data": {
                "organizingRoot": str(org),
                "organizingSameDeviceAsMovie": same_device,
            },
        }

    def _probe_aria2_write(self, staging_root: Path | None) -> dict:
        """Verify aria2 can write into the shared downloads mount."""
        if staging_root is None or not hasattr(self.aria, "add_uri"):
            return {"ok": True, "data": {"skipped": True}}
        probe_url = os.environ.get(
            "ARIA2_PROBE_URL",
            "https://example.com/",
        ).strip()
        if probe_url in {"", "skip", "0", "false", "False"}:
            return {"ok": True, "data": {"skipped": True, "reason": "ARIA2_PROBE_URL"}}
        from routing_paths import agent_path_to_aria2

        probe_dir = staging_root / f".check-ready-{os.getpid()}"
        probe_name = "openclaw-aria2-probe.bin"
        probe_file = probe_dir / probe_name
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(probe_dir, 0o775)
            except OSError:
                pass
            try:
                aria2_dir = agent_path_to_aria2(probe_dir, self.routing)
            except Exception as error:
                return {
                    "ok": False,
                    "nextAction": "fix_aria2_path_mapping",
                    "error": f"aria2 path mapping failed: {error}",
                }
            gid = self.aria.add_uri(
                [probe_url],
                options={
                    "dir": aria2_dir,
                    "out": probe_name,
                    "follow-torrent": "false",
                    "max-connection-per-server": "1",
                },
            )
            import time as _time

            deadline = _time.time() + 12
            while _time.time() < deadline:
                if probe_file.is_file() and probe_file.stat().st_size > 0:
                    break
                _time.sleep(0.25)
            visible = probe_file.is_file() and probe_file.stat().st_size > 0
            try:
                if gid:
                    try:
                        self.aria.remove(str(gid))
                    except Exception:
                        pass
                    self.aria.remove_result(str(gid))
            except Exception:
                pass
            if not visible:
                return {
                    "ok": False,
                    "nextAction": "fix_aria2_path_mapping",
                    "error": (
                        "aria2 write probe failed: agent staging is writable but "
                        f"aria2 did not create {probe_name} under mapped dir {aria2_dir}"
                    ),
                }
            return {
                "ok": True,
                "data": {
                    "probeFile": probe_name,
                    "gid": str(gid),
                    "aria2Dir": aria2_dir,
                },
            }
        except Exception as error:
            return {
                "ok": False,
                "nextAction": "fix_aria2_path_mapping",
                "error": f"aria2 write probe failed: {error}",
            }
        finally:
            try:
                if probe_file.exists():
                    probe_file.unlink()
                if probe_dir.exists():
                    probe_dir.rmdir()
            except OSError:
                pass


def _load_runtime(command: str | None = None):
    base_dir = Path(__file__).resolve().parents[1]
    with open(
        base_dir / "config" / "routing.json",
        encoding="utf-8",
    ) as routing_file:
        routing = json.load(routing_file)

    needs_qas = command in {
        None,
        "check-ready",
        "search",
        "share",
        "import-url",
        "preview",
        "tree",
        "plan",
        "execute",
    }
    needs_aria = command in {None, "check-ready", "downloads", "execute"}
    # PanSou is optional supplemental discovery; never hard-required.
    needs_pansou = command in {None, "search"}

    required = []
    if needs_qas:
        required.extend(["QAS_BASE_URL", "QAS_TOKEN"])
    if needs_aria:
        required.extend(["ARIA2_RPC_URL", "ARIA2_RPC_SECRET"])
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
    qas = None
    aria = None
    pansou = None
    planner = None
    if needs_qas:
        qas = QasClient(os.environ["QAS_BASE_URL"], os.environ["QAS_TOKEN"])
    if needs_aria:
        aria = Aria2Client(
            os.environ["ARIA2_RPC_URL"],
            os.environ["ARIA2_RPC_SECRET"],
        )
    if needs_pansou and os.environ.get("PANSOU_BASE_URL"):
        pansou = PanSouClient(
            os.environ["PANSOU_BASE_URL"],
            max_candidates=_pansou_limit(
                os.environ.get("PANSOU_MAX_CANDIDATES")
            ),
        )
    jiaofu = _optional_jiaofu(base_dir) if needs_qas or command == "search" else None
    if needs_qas and qas is not None:
        from routing_paths import path_guard_roots

        allowed, protected = path_guard_roots(routing)
        # PathGuard resolve requires existing roots; skip guard when absent in unit envs.
        existing_allowed = []
        existing_protected = []
        for root in allowed:
            try:
                existing_allowed.append(root.resolve(strict=True))
            except OSError:
                continue
        for root in protected:
            try:
                resolved = root.resolve(strict=True)
            except OSError:
                continue
            if resolved in existing_allowed:
                existing_protected.append(resolved)
        guard = None
        if existing_allowed and set(existing_protected).issubset(set(existing_allowed)):
            try:
                guard = PathGuard(existing_allowed, protected_roots=existing_protected)
            except Exception:
                guard = None
        planner = DownloadPlanner(
            qas=qas,
            store=store,
            routing=routing,
            path_guard=guard,
        )
    return routing, store, qas, aria, planner, pansou, jiaofu


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
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
    )
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument(
        "--media-type",
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
    )
    search.add_argument("--update", action="store_true")
    share = subparsers.add_parser("share")
    share_sub = share.add_subparsers(dest="share_command", required=True)
    share_open = share_sub.add_parser("open")
    share_open.add_argument("url")
    share_open.add_argument(
        "--media-type",
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
    )
    import_url = subparsers.add_parser("import-url")
    import_url.add_argument("url")
    import_url.add_argument(
        "--media-type",
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
    )
    preview = subparsers.add_parser("preview")
    preview.add_argument(
        "candidate_id",
        help="candidateId or Quark share URL (https://pan.quark.cn/s/...)",
    )
    preview.add_argument(
        "--media-type",
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
    )
    tree = subparsers.add_parser("tree")
    tree.add_argument(
        "candidate_id",
        help="candidateId or Quark share URL (https://pan.quark.cn/s/...)",
    )
    tree.add_argument(
        "--media-type",
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
    )
    plan = subparsers.add_parser("plan")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_download = plan_sub.add_parser("download")
    plan_download.add_argument(
        "candidate_id",
        help="candidateId or Quark share URL (https://pan.quark.cn/s/...)",
    )
    plan_download.add_argument(
        "--node",
        action="append",
        dest="nodes",
        default=[],
        help="tree nodeId selected via mediactl tree (repeatable)",
    )
    plan_download.add_argument(
        "--media-type",
        choices=("movie", "drama", "tv", "anime", "documentary", "show", "other"),
        help="override auto classification / final library route",
    )
    execute = subparsers.add_parser("execute")
    execute.add_argument("plan_id")
    execute.add_argument("--confirmed", action="store_true")
    downloads = subparsers.add_parser("downloads")
    downloads_sub = downloads.add_subparsers(dest="download_command", required=True)
    downloads_sub.add_parser("list")
    show = downloads_sub.add_parser("show")
    show.add_argument("task_id")
    validate = downloads_sub.add_parser("validate")
    validate.add_argument("task_id")
    for command in ("pause", "resume", "cancel"):
        control = downloads_sub.add_parser(command)
        control.add_argument("task_id")
    organize = subparsers.add_parser("organize")
    organize_sub = organize.add_subparsers(
        dest="organize_command",
        required=True,
    )
    organize_plan = organize_sub.add_parser("plan")
    organize_plan.add_argument("task_id")
    organize_execute = organize_sub.add_parser("execute")
    organize_execute.add_argument("plan_id")
    organize_execute.add_argument("--confirmed", action="store_true")

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


def _default_service_factory(routing, store, qas, pansou, jiaofu=None):
    from videomgr_client import client_from_env

    roots = {
        media_type: Path(route["final_root"])
        for media_type, route in routing.items()
        if isinstance(route, dict) and route.get("final_root")
    }
    return MediaService(
        LibraryCatalog(roots, videomgr=client_from_env()),
        qas,
        store,
        pansou,
        jiaofu,
    )


def _ffprobe_runner(path: Path) -> bool:
    binary = shutil.which("ffprobe")
    if not binary:
        return True
    completed = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            "--",
            str(path),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    return completed.returncode == 0


def _default_organizer_factory(routing, store):
    from routing_paths import (
        downloads_root as routing_downloads_root,
        organizing_root as routing_organizing_root,
        path_guard_roots,
    )

    downloads = routing_downloads_root(routing)
    org_root = routing_organizing_root(routing)
    allowed, protected = path_guard_roots(routing)
    # Only auto-create staging / organizing dirs. Formal libraries and
    # protected roots must already exist as bind mounts (never shadow them).
    for root in (
        downloads / ".incoming",
        downloads / ".ready",
        downloads / ".quarantine",
        org_root,
    ):
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    guard = PathGuard(
        allowed,
        protected_roots=protected,
    )
    validator = DownloadValidator(
        store,
        guard,
        downloads,
        ffprobe_runner=_ffprobe_runner,
    )
    return Organizer(
        store,
        guard,
        downloads,
        validator=validator,
        organizing_root=org_root,
    )


def main(
    argv=None,
    *,
    runtime_loader=_load_runtime,
    service_factory=_default_service_factory,
    organizer_factory=_default_organizer_factory,
    stream=None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    store = None
    try:
        args = parse_args(arguments)
        try:
            loaded = runtime_loader(args.command)
        except TypeError:
            loaded = runtime_loader()
        routing, store, qas, aria, planner, pansou, jiaofu = loaded
        agent = ResourceAgent(store=store, qas=qas, aria=aria, routing=routing)
        service = None
        if args.command in {
            "library",
            "search",
            "share",
            "import-url",
            "preview",
            "tree",
            "plan",
        }:
            service = service_factory(routing, store, qas, pansou, jiaofu)
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
        elif args.command in {"share", "import-url"}:
            url = args.url
            result = service.open_share(
                url,
                getattr(args, "media_type", None),
            )
        elif args.command == "preview":
            result = service.preview(
                args.candidate_id,
                getattr(args, "media_type", None),
            )
        elif args.command == "tree":
            result = service.tree(
                args.candidate_id,
                getattr(args, "media_type", None),
            )
        elif args.command == "plan":
            candidate_id = service.resolve_candidate_ref(
                args.candidate_id,
                getattr(args, "media_type", None),
            )
            result = success(
                planner.plan_selected(
                    candidate_id,
                    node_ids=list(getattr(args, "nodes", []) or []),
                    preferred_media_type=getattr(args, "media_type", None),
                ),
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
        elif args.command == "organize":
            organizer = organizer_factory(routing, store)
            if args.organize_command == "plan":
                result = success(
                    organizer.plan(args.task_id),
                    next_action="review_organize_plan",
                )
            else:
                result = success(
                    organizer.execute(
                        args.plan_id,
                        confirmed=getattr(args, "confirmed", False),
                    ),
                    next_action="none",
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
        elif args.download_command == "validate":
            organizer = organizer_factory(routing, store)
            report = organizer.validator.validate(args.task_id)
            task = store.get_task(args.task_id) or {}
            result = success(
                {
                    "taskId": args.task_id,
                    "valid": report.ok,
                    "problems": list(report.problems),
                    "fileCount": len(report.manifest),
                    "totalBytes": sum(report.manifest.values()),
                    "stagingPath": task.get("staging_path"),
                    "status": task.get("status"),
                    **(
                        {"relocatedPath": report.relocated_path}
                        if report.relocated_path
                        else {}
                    ),
                },
                terminal=True,
                next_action=report.next_action,
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
    except (
        AgentError,
        ClientError,
        OrganizeError,
        PlanningError,
        PlanError,
    ) as error:
        result = failure(
            (
                "CLIENT_ERROR"
                if isinstance(error, ClientError)
                else "ORGANIZE_ERROR"
                if isinstance(error, OrganizeError)
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

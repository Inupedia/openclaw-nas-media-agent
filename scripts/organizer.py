import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from episode_diff import extract_episode_key, normalize_title_key
from path_guard import PathGuard, PathGuardError
from state_store import PlanError, StateStore


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | {
    ".ass",
    ".ssa",
    ".srt",
    ".vtt",
    ".sub",
    ".nfo",
    ".jpg",
    ".jpeg",
    ".png",
}
TEMPORARY_EXTENSIONS = {".aria2", ".part", ".tmp"}


class OrganizeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    next_action: str
    problems: tuple[str, ...]
    manifest: dict[str, int]
    relocated_path: str | None = None


class DownloadValidator:
    def __init__(
        self,
        store: StateStore,
        guard: PathGuard,
        downloads_root: Path,
        *,
        ffprobe_runner: Callable[[Path], bool] | None = None,
        relocate: bool = True,
    ):
        self.store = store
        self.guard = guard
        self.downloads_root = Path(downloads_root).resolve(strict=True)
        self.incoming_root = (
            self.downloads_root / ".incoming"
        ).resolve(strict=True)
        self.ready_root = (
            self.downloads_root / ".ready"
        ).resolve(strict=True)
        self.quarantine_root = (
            self.downloads_root / ".quarantine"
        ).resolve(strict=True)
        self.ffprobe_runner = ffprobe_runner
        self.relocate = relocate

    def _check_expected_manifest(
        self,
        task: dict,
        source: Path,
        videos: list[Path],
        problems: list[str],
    ) -> None:
        expected = task.get("expected_manifest") or {}
        if not isinstance(expected, dict) or not expected:
            return
        expected_names = [
            str(name) for name in (expected.get("expectedFileNames") or []) if name
        ]
        if not expected_names:
            return
        present = {path.name for path in videos}
        missing = [name for name in expected_names if name not in present]
        if missing:
            problems.append("expected_files_missing")
        expected_count = int(expected.get("expectedFileCount") or len(expected_names))
        if len(videos) < expected_count:
            problems.append("expected_file_count_mismatch")
        expected_jobs = int(expected.get("transferJobCount") or 0)
        if expected_jobs > 1 and len(videos) < expected_count:
            problems.append("transfer_jobs_incomplete")
        episode_keys = list(expected.get("expectedEpisodeKeys") or [])
        if episode_keys:
            title_key = str(task.get("title_key") or normalize_title_key(task["title"]))
            seasons = {int(item["season"]) for item in episode_keys}
            default_season = next(iter(seasons)) if len(seasons) == 1 else None
            found = set()
            for path in videos:
                key = extract_episode_key(
                    path.name,
                    title_key,
                    default_season=default_season,
                )
                if key is not None:
                    found.add((key.season, key.episode, key.special))
            wanted = {
                (
                    int(item["season"]),
                    int(item["episode"]),
                    item.get("special"),
                )
                for item in episode_keys
            }
            if not wanted.issubset(found):
                problems.append("expected_episodes_missing")

    def _relocate_source(
        self,
        task: dict,
        source: Path,
        *,
        ok: bool,
    ) -> str | None:
        if not self.relocate:
            return None
        if not source.is_relative_to(self.incoming_root):
            return None
        target_root = self.ready_root if ok else self.quarantine_root
        target = self.guard.resolve_target(str(target_root / task["task_id"]))
        self.guard.assert_mutable(target)
        if target.exists():
            raise OrganizeError(
                f"{'ready' if ok else 'quarantine'} target exists"
            )
        target_root.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        task["staging_path"] = str(target)
        task["status"] = "ready" if ok else "quarantined"
        self.store.upsert_task(task)
        return str(target)

    def validate(self, task_id: str) -> ValidationReport:
        task = self.store.get_task(task_id)
        if task is None:
            raise OrganizeError("task not found")
        problems = []
        if task["status"] not in {"complete", "ready", "quarantined"}:
            problems.append("task_not_complete")
        try:
            source = self.guard.resolve_existing(task["staging_path"])
        except PathGuardError as error:
            raise OrganizeError(str(error)) from None
        in_incoming = source.is_relative_to(self.incoming_root)
        in_ready = source.is_relative_to(self.ready_root)
        in_quarantine = source.is_relative_to(self.quarantine_root)
        if not (in_incoming or in_ready or in_quarantine):
            problems.append("source_not_in_incoming")

        manifest = {}
        videos = []
        for path in source.rglob("*"):
            if path.is_symlink():
                problems.append("symlink_found")
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            extension = path.suffix.casefold()
            if extension in TEMPORARY_EXTENSIONS:
                problems.append("temporary_files")
                continue
            if extension not in ALLOWED_EXTENSIONS:
                problems.append("unsupported_files")
                continue
            size = path.stat().st_size
            manifest[relative] = size
            if extension in VIDEO_EXTENSIONS:
                videos.append(path)
                if size == 0:
                    problems.append("zero_byte_media")
        if not videos:
            problems.append("no_video_media")
        if self.ffprobe_runner is not None:
            for video in videos:
                if video.stat().st_size and not self.ffprobe_runner(video):
                    problems.append("unreadable_video")
                    break
        self._check_expected_manifest(task, source, videos, problems)
        unique_problems = tuple(sorted(set(problems)))
        ok = not unique_problems
        relocated = None
        if task["status"] == "complete" and in_incoming and (
            "task_not_complete" not in unique_problems
        ):
            # Only relocate when aria2 reported complete and path checks ran.
            try:
                relocated = self._relocate_source(task, source, ok=ok)
            except OrganizeError:
                raise
            except Exception as error:
                raise OrganizeError(f"validate relocate failed: {error}") from None
        return ValidationReport(
            ok=ok,
            next_action=(
                "ready_to_organize"
                if ok
                else "quarantine_download"
            ),
            problems=unique_problems,
            manifest=manifest,
            relocated_path=relocated,
        )


class Organizer:
    def __init__(
        self,
        store: StateStore,
        guard: PathGuard,
        downloads_root: Path,
        *,
        validator: DownloadValidator,
        organizing_root: Path | None = None,
        same_filesystem: Callable[[Path, Path], bool] | None = None,
        copy_verifier: Callable[[Path, Path, dict[str, int]], bool] | None = None,
        fsync_tree: Callable[[Path], None] | None = None,
    ):
        self.store = store
        self.guard = guard
        self.downloads_root = Path(downloads_root).resolve(strict=True)
        self.incoming_root = (
            self.downloads_root / ".incoming"
        ).resolve(strict=True)
        self.ready_root = (
            self.downloads_root / ".ready"
        ).resolve(strict=True)
        self.organizing_root = (
            Path(organizing_root).resolve(strict=False)
            if organizing_root is not None
            else (self.downloads_root / ".organizing").resolve(strict=False)
        )
        self.validator = validator
        self.same_filesystem = same_filesystem or self._same_filesystem
        self.copy_verifier = copy_verifier or self._verify_manifest
        self.fsync_tree = fsync_tree or self._fsync_tree

    @staticmethod
    def _same_filesystem(source: Path, target: Path) -> bool:
        return source.stat().st_dev == target.parent.stat().st_dev

    @staticmethod
    def _verify_manifest(
        source: Path,
        target: Path,
        manifest: dict[str, int],
    ) -> bool:
        for relative, expected_size in manifest.items():
            copied = target / Path(relative)
            if not copied.is_file() or copied.stat().st_size != expected_size:
                return False
        return True

    @staticmethod
    def _fsync_tree(root: Path) -> None:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            with path.open("rb") as handle:
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        for directory in sorted(
            [path for path in root.rglob("*") if path.is_dir()],
            key=lambda path: len(path.parts),
            reverse=True,
        ) + [root]:
            descriptor = None
            try:
                descriptor = os.open(directory, os.O_RDONLY)
                os.fsync(descriptor)
            except OSError:
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def plan(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if task is None:
            raise OrganizeError("task not found")
        if task["status"] not in {"complete", "ready"}:
            raise OrganizeError("task is not complete")
        final_path = self.guard.resolve_target(task["final_path"])
        self.guard.assert_mutable(final_path)
        if final_path.exists():
            raise OrganizeError("target exists")
        report = self.validator.validate(task_id)
        if not report.ok:
            raise OrganizeError(
                "download validation failed: " + ",".join(report.problems)
            )
        task = self.store.get_task(task_id) or task
        ready_path = self.guard.resolve_target(
            str(self.ready_root / task_id)
        )
        source_path = report.relocated_path or task["staging_path"]
        payload = {
            "schemaVersion": 1,
            "taskId": task_id,
            "sourcePath": source_path,
            "readyPath": str(ready_path),
            "finalPath": str(final_path),
            "manifest": report.manifest,
            "requiresConfirmation": True,
        }
        plan_id = self.store.create_plan("organize", payload)
        return {
            "planId": plan_id,
            "taskId": task_id,
            "sourcePath": source_path,
            "finalPath": str(final_path),
            "fileCount": len(report.manifest),
            "totalBytes": sum(report.manifest.values()),
            "requiresConfirmation": True,
        }

    def execute(self, plan_id: str, *, confirmed: bool = False) -> dict:
        try:
            plan = self.store.read_plan(plan_id, "organize")
        except PlanError as error:
            raise OrganizeError(str(error)) from None
        if not confirmed:
            raise OrganizeError("organize plan requires confirmation")
        try:
            plan = self.store.consume_plan(plan_id, "organize")
        except PlanError as error:
            raise OrganizeError(str(error)) from None

        task = self.store.get_task(plan["taskId"])
        if task is None:
            raise OrganizeError("task not found")
        source = self.guard.resolve_existing(plan["sourcePath"])
        ready = self.guard.resolve_target(plan["readyPath"])
        final_path = self.guard.resolve_target(plan["finalPath"])
        self.guard.assert_mutable(ready)
        self.guard.assert_mutable(final_path)
        if final_path.exists():
            raise OrganizeError("target exists")
        if not final_path.parent.is_dir():
            raise OrganizeError("final parent is not available")

        if source.is_relative_to(self.incoming_root):
            if ready.exists():
                raise OrganizeError("ready target exists")
            os.replace(source, ready)
            source = ready
            task["staging_path"] = str(ready)
            task["status"] = "ready"
            self.store.upsert_task(task)

        if self.same_filesystem(source, final_path):
            self.guard.assert_deletable(source)
            os.replace(source, final_path)
        else:
            self.organizing_root.mkdir(parents=True, exist_ok=True)
            hidden_target = self.guard.resolve_target(
                str(self.organizing_root / f".organizing-{plan['taskId']}")
            )
            self.guard.assert_mutable(hidden_target)
            if hidden_target.exists():
                raise OrganizeError("temporary target exists")
            try:
                shutil.copytree(source, hidden_target, copy_function=shutil.copy2)
                self.fsync_tree(hidden_target)
                if not self.copy_verifier(
                    source,
                    hidden_target,
                    plan["manifest"],
                ):
                    raise OrganizeError("copy verification failed")
                os.replace(hidden_target, final_path)
                self.guard.assert_deletable(source)
                shutil.rmtree(source)
            except Exception:
                if hidden_target.exists():
                    try:
                        self.guard.assert_deletable(hidden_target)
                        shutil.rmtree(hidden_target)
                    except PathGuardError:
                        pass
                    except OSError:
                        pass
                raise

        task["staging_path"] = str(source)
        task["final_path"] = str(final_path)
        task["status"] = "organized"
        self.store.upsert_task(task)
        return {
            "taskId": task["task_id"],
            "status": "organized",
            "finalPath": str(final_path),
        }

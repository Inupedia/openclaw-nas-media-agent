import os
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from episode_diff import extract_episode_key, normalize_title_key
from path_guard import PathGuard, PathGuardError
from state_store import PlanError, StateStore
from download_fs import ensure_aria2_writable, ensure_managed_download_roots


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts"}
SIDECAR_EXTENSIONS = {".ass", ".ssa", ".srt", ".vtt", ".sub"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS | {
    ".nfo",
    ".jpg",
    ".jpeg",
    ".png",
}
TEMPORARY_EXTENSIONS = {".aria2", ".part", ".tmp"}


class OrganizeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ORGANIZE_ERROR",
        next_action: str = "review_error",
    ):
        super().__init__(message)
        self.code = code
        self.next_action = next_action


def _source_manifest_detail(root: Path, manifest: dict[str, int]) -> dict[str, dict]:
    detail: dict[str, dict] = {}
    for relative, expected_size in manifest.items():
        path = root / relative
        if not path.is_file():
            detail[relative] = {
                "size": int(expected_size),
                "mtimeNs": None,
                "missing": True,
            }
            continue
        stat = path.stat()
        detail[relative] = {
            "size": int(stat.st_size),
            "mtimeNs": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
            "missing": False,
        }
    return detail


def _manifest_hash(detail: dict[str, dict]) -> str:
    import hashlib
    import json

    payload = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        ).resolve(strict=False)
        self.ready_root = (
            self.downloads_root / ".ready"
        ).resolve(strict=False)
        self.quarantine_root = (
            self.downloads_root / ".quarantine"
        ).resolve(strict=False)
        ensure_managed_download_roots(self.downloads_root)
        self.ffprobe_runner = ffprobe_runner
        self.relocate = relocate

    def _check_expected_manifest(
        self,
        task: dict,
        source: Path,
        videos: list[Path],
        sidecars: list[Path],
        problems: list[str],
    ) -> None:
        expected = task.get("expected_manifest") or {}
        if not isinstance(expected, dict) or not expected:
            return

        video_entries = list(expected.get("expectedVideoFiles") or [])
        sidecar_entries = list(expected.get("expectedSidecarFiles") or [])
        # Legacy manifests only listed basenames (often mixing videos+subs).
        if not video_entries and not sidecar_entries:
            legacy_names = [
                str(name)
                for name in (expected.get("expectedFileNames") or [])
                if name
            ]
            for name in legacy_names:
                extension = Path(name).suffix.casefold()
                entry = {"name": name, "id": f"legacy/{name}"}
                if extension in SIDECAR_EXTENSIONS:
                    sidecar_entries.append(entry)
                else:
                    video_entries.append(entry)

        present_videos = Counter(path.name for path in videos)
        present_sidecars = Counter(path.name for path in sidecars)
        wanted_videos = Counter(
            str(item.get("name") or "") for item in video_entries if item.get("name")
        )
        wanted_sidecars = Counter(
            str(item.get("name") or "")
            for item in sidecar_entries
            if item.get("name")
        )

        if wanted_videos and any(
            present_videos[name] < count for name, count in wanted_videos.items()
        ):
            problems.append("expected_files_missing")
        if wanted_sidecars and any(
            present_sidecars[name] < count for name, count in wanted_sidecars.items()
        ):
            problems.append("expected_sidecar_files_missing")

        expected_video_count = int(
            expected.get("expectedFileCount")
            or (len(video_entries) if video_entries else 0)
        )
        if expected_video_count and len(videos) < expected_video_count:
            problems.append("expected_file_count_mismatch")
        expected_all = int(expected.get("expectedAllFileCount") or 0)
        if expected_all and (len(videos) + len(sidecars)) < expected_all:
            problems.append("expected_all_file_count_mismatch")
        expected_jobs = int(expected.get("transferJobCount") or 0)
        if expected_jobs > 1 and expected_video_count and len(videos) < expected_video_count:
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

    def _clear_download_sync(self, task: dict) -> None:
        """Stop aria2 status sync after validate/organize terminal states."""
        task["aria2_gids"] = []

    def _relocate_source(
        self,
        task: dict,
        source: Path,
        *,
        ok: bool,
    ) -> str | None:
        if not self.relocate:
            return None
        target_root = self.ready_root if ok else self.quarantine_root
        desired_status = "ready" if ok else "quarantined"
        # Already in the correct lane: only refresh status / clear GIDs.
        if source.is_relative_to(target_root):
            task["status"] = desired_status
            self._clear_download_sync(task)
            self.store.upsert_task(task)
            return str(source)
        allowed_sources = (
            self.incoming_root,
            self.ready_root,
            self.quarantine_root,
        )
        if not any(source.is_relative_to(root) for root in allowed_sources):
            return None
        target = self.guard.resolve_target(str(target_root / task["task_id"]))
        self.guard.assert_mutable(target)
        if target.exists():
            raise OrganizeError(
                f"{'ready' if ok else 'quarantine'} target exists"
            )
        target_root.mkdir(parents=True, exist_ok=True)
        ensure_aria2_writable(target_root)
        os.replace(source, target)
        task["staging_path"] = str(target)
        task["status"] = desired_status
        self._clear_download_sync(task)
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
        sidecars = []
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
            elif extension in SIDECAR_EXTENSIONS:
                sidecars.append(path)
        if not videos:
            problems.append("no_video_media")
        if self.ffprobe_runner is not None:
            for video in videos:
                if video.stat().st_size and not self.ffprobe_runner(video):
                    problems.append("unreadable_video")
                    break
        self._check_expected_manifest(task, source, videos, sidecars, problems)
        unique_problems = tuple(sorted(set(problems)))
        ok = not unique_problems
        relocated = None
        status = task["status"]
        should_relocate = "task_not_complete" not in unique_problems and (
            (status == "complete" and in_incoming)
            or (status == "quarantined" and in_quarantine)
            or (status == "ready" and in_ready and not ok)
        )
        if should_relocate:
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
        try:
            return source.stat().st_dev == target.parent.stat().st_dev
        except OSError:
            return False

    @staticmethod
    def _is_exdev(error: OSError) -> bool:
        # errno 18 == EXDEV on Linux; also accept Windows winerror 17.
        return int(getattr(error, "errno", 0) or 0) == 18 or int(
            getattr(error, "winerror", 0) or 0
        ) == 17

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
        merge_into_existing = False
        if final_path.exists():
            if not final_path.is_dir():
                raise OrganizeError("target exists")
            # Incremental updates land into an existing title folder.
            merge_into_existing = True
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
        source_detail = _source_manifest_detail(Path(source_path), report.manifest)
        payload = {
            "schemaVersion": 1,
            "taskId": task_id,
            "taskStatus": task["status"],
            "sourcePath": source_path,
            "readyPath": str(ready_path),
            "finalPath": str(final_path),
            "mergeIntoExisting": merge_into_existing,
            "manifest": report.manifest,
            "sourceManifest": source_detail,
            "manifestHash": _manifest_hash(source_detail),
            "requiresConfirmation": True,
        }
        plan_id = self.store.create_plan("organize", payload)
        return {
            "planId": plan_id,
            "taskId": task_id,
            "sourcePath": source_path,
            "finalPath": str(final_path),
            "mergeIntoExisting": merge_into_existing,
            "fileCount": len(report.manifest),
            "totalBytes": sum(report.manifest.values()),
            "manifestHash": payload["manifestHash"],
            "requiresConfirmation": True,
        }

    def execute(self, plan_id: str, *, confirmed: bool = False) -> dict:
        try:
            plan = self.store.read_plan(plan_id, "organize")
        except PlanError as error:
            raise OrganizeError(
                str(error),
                code="PLAN_EXPIRED",
                next_action="regenerate_plan",
            ) from None
        if not confirmed:
            raise OrganizeError(
                "organize plan requires confirmation",
                code="CONFIRMATION_REQUIRED",
                next_action="confirm_action",
            )
        try:
            plan = self.store.consume_plan(plan_id, "organize")
        except PlanError as error:
            raise OrganizeError(
                str(error),
                code="PLAN_EXPIRED",
                next_action="regenerate_plan",
            ) from None

        task = self.store.get_task(plan["taskId"])
        if task is None:
            raise OrganizeError(
                "task not found",
                code="TASK_NOT_FOUND",
                next_action="stop",
            )
        if str(task.get("status")) != str(plan.get("taskStatus") or task.get("status")):
            raise OrganizeError(
                "organize plan stale: task status changed",
                code="ORGANIZE_PLAN_STALE",
                next_action="revalidate_download",
            )
        source = self.guard.resolve_existing(plan["sourcePath"])
        current_detail = _source_manifest_detail(source, plan.get("manifest") or {})
        expected_hash = str(plan.get("manifestHash") or "")
        if expected_hash and _manifest_hash(current_detail) != expected_hash:
            raise OrganizeError(
                "organize plan stale: source files changed",
                code="ORGANIZE_PLAN_STALE",
                next_action="revalidate_download",
            )
        ready = self.guard.resolve_target(plan["readyPath"])
        final_path = self.guard.resolve_target(plan["finalPath"])
        merge_into_existing = bool(plan.get("mergeIntoExisting"))
        self.guard.assert_mutable(ready)
        self.guard.assert_mutable(final_path)
        if final_path.exists():
            if not (merge_into_existing and final_path.is_dir()):
                raise OrganizeError("target exists")
        elif not final_path.parent.is_dir():
            raise OrganizeError("final parent is not available")

        if source.is_relative_to(self.incoming_root):
            if ready.exists():
                raise OrganizeError("ready target exists")
            try:
                os.replace(source, ready)
            except OSError as error:
                if not self._is_exdev(error):
                    raise
                shutil.copytree(source, ready, copy_function=shutil.copy2)
                self.guard.assert_deletable(source)
                shutil.rmtree(source)
            source = ready
            task["staging_path"] = str(ready)
            task["status"] = "ready"
            self.store.upsert_task(task)

        if merge_into_existing:
            self._merge_into_existing(source, final_path, plan.get("manifest") or {})
        else:
            self._promote_directory(source, final_path, plan)

        task["staging_path"] = str(source)
        task["final_path"] = str(final_path)
        task["status"] = "organized"
        task["aria2_gids"] = []
        self.store.upsert_task(task)
        return {
            "taskId": task["task_id"],
            "status": "organized",
            "finalPath": str(final_path),
            "mergeIntoExisting": merge_into_existing,
        }

    def _merge_into_existing(
        self,
        source: Path,
        final_path: Path,
        manifest: dict,
    ) -> None:
        """Copy/move individual files into an existing title directory."""
        conflicts = []
        planned: list[tuple[Path, Path]] = []
        for relative in manifest:
            src = source / relative
            dest = final_path / relative
            if not src.is_file():
                raise OrganizeError(f"merge source missing: {relative}")
            if dest.exists():
                conflicts.append(relative)
            planned.append((src, dest))
        if conflicts:
            raise OrganizeError(
                "merge conflicts: " + ",".join(conflicts[:8])
            )
        for src, dest in planned:
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.guard.assert_mutable(dest)
            try:
                os.replace(src, dest)
            except OSError as error:
                if not self._is_exdev(error):
                    raise
                shutil.copy2(src, dest)
                self.guard.assert_deletable(src)
                src.unlink()
        self.guard.assert_deletable(source)
        shutil.rmtree(source)

    def _promote_directory(
        self,
        source: Path,
        final_path: Path,
        plan: dict,
    ) -> None:
        """Move a staging directory to a new final title path."""
        moved = False
        if self.same_filesystem(source, final_path):
            self.guard.assert_deletable(source)
            try:
                os.replace(source, final_path)
                moved = True
            except OSError as error:
                if not self._is_exdev(error):
                    raise
                moved = False
        if moved:
            return

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
            try:
                os.replace(hidden_target, final_path)
            except OSError as error:
                if not self._is_exdev(error):
                    raise
                shutil.copytree(
                    hidden_target, final_path, copy_function=shutil.copy2
                )
                self.fsync_tree(final_path)
                if not self.copy_verifier(
                    source,
                    final_path,
                    plan["manifest"],
                ):
                    raise OrganizeError("copy verification failed")
                self.guard.assert_deletable(hidden_target)
                shutil.rmtree(hidden_target)
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
            if final_path.exists() and not plan.get("mergeIntoExisting"):
                try:
                    self.guard.assert_deletable(final_path)
                    shutil.rmtree(final_path)
                except Exception:
                    pass
            raise

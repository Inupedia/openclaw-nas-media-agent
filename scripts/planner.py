import os
import re
import uuid
from pathlib import Path
from typing import Callable

from episode_diff import extract_episode_key, normalize_title_key
from media_classifier import classify, score_candidate
from media_namer import build_paths
from path_guard import PathGuard, PathGuardError
from qas_client import ClientError, share_url_for_directory
from routing_paths import staging_root
from state_store import PlanError, StateStore
from download_fs import ensure_aria2_writable


class PlanningError(RuntimeError):
    pass


QUARK_LINK = re.compile(r"https://pan\.quark\.cn/s/[A-Za-z0-9_-]+")
INTENT_PREFIX = re.compile(r"^\s*(?:请|帮我|给我)?\s*(?:下载|追更|搜索|查找)\s*")
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts")
SIDECAR_EXTENSIONS = (".ass", ".ssa", ".srt", ".vtt", ".sub")


def _episode_dict(key) -> dict:
    payload = {"season": key.season, "episode": key.episode}
    if key.special:
        payload["special"] = key.special
    return payload


def _episode_tuple(item: dict) -> tuple[int, int, str | None]:
    return (
        int(item["season"]),
        int(item["episode"]),
        item.get("special"),
    )


def _file_kind(name: str) -> str:
    extension = Path(str(name)).suffix.casefold()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in SIDECAR_EXTENSIONS:
        return "sidecar"
    return "other"


def build_expected_manifest(
    *,
    transfer_jobs: list[dict],
    selected_files: list[str],
    title_key: str,
    new_episodes: list[dict] | None = None,
    default_season: int | None = None,
) -> dict:
    videos: list[dict] = []
    sidecars: list[dict] = []
    seen: set[str] = set()

    def add_name(name: str, *, fid: str, job_index: int) -> None:
        key = str(name or "").strip()
        if not key:
            return
        identity = f"{fid}/{key}"
        if identity in seen:
            return
        seen.add(identity)
        entry = {
            "id": identity,
            "name": key,
            "fid": fid,
            "jobIndex": job_index,
        }
        kind = _file_kind(key)
        if kind == "video":
            videos.append(entry)
        elif kind == "sidecar":
            sidecars.append(entry)

    if transfer_jobs:
        for job_index, job in enumerate(transfer_jobs):
            fid = str(job.get("fid") or f"job-{job_index}")
            for name in job.get("fileNames") or []:
                add_name(name, fid=fid, job_index=job_index)
    else:
        for name in selected_files:
            add_name(name, fid="__root__", job_index=0)

    episode_keys: list[dict] = []
    if new_episodes:
        episode_keys = list(new_episodes)
    else:
        found = []
        for item in videos:
            key = extract_episode_key(
                item["name"],
                title_key,
                default_season=default_season,
            )
            if key is not None:
                found.append(_episode_dict(key))
        episode_keys = found

    all_names = [item["name"] for item in (*videos, *sidecars)]
    return {
        "transferJobCount": len(transfer_jobs),
        "transferJobs": [
            {
                "fid": str(job.get("fid") or f"job-{index}"),
                "fileNames": list(job.get("fileNames") or []),
                "fileCount": int(job.get("fileCount") or 0),
            }
            for index, job in enumerate(transfer_jobs)
        ],
        "expectedVideoFiles": videos,
        "expectedSidecarFiles": sidecars,
        # Backward-compatible aliases used by older callers/tests.
        "expectedFileNames": [item["name"] for item in videos],
        "expectedFileCount": len(videos),
        "expectedAllFileCount": len(videos) + len(sidecars),
        "expectedEpisodeKeys": episode_keys,
        "expectedNames": all_names,
    }


def _clean_hint(value: str) -> str:
    return INTENT_PREFIX.sub("", value or "").strip()


def resolve_transfer_shareurl(
    base_shareurl: str,
    index: dict,
    selected_nodes: list[str],
) -> str:
    """Deep-link into a single selected folder for QAS.

    Prefer :func:`build_transfer_jobs` when multiple folders may be selected.
    """
    jobs = build_transfer_jobs(base_shareurl, index, selected_nodes)
    if len(jobs) == 1:
        return jobs[0]["shareurl"]
    return str(base_shareurl or "")


def _pattern_for_names(names: list[str]) -> str:
    return "(?i)^(?:" + "|".join(re.escape(name) for name in names) + ")$"


def build_transfer_jobs(
    base_shareurl: str,
    index: dict,
    selected_nodes: list[str],
) -> list[dict]:
    """One QAS job per selected directory (or parent of selected files).

    quark-auto-save does not recurse into nested season folders unless the
    share URL already points at that folder. Selecting two seasons therefore
    must become two deep-linked jobs, not one root URL with a filename regex.
    """
    by_fid: dict[str, list[str]] = {}
    for node_id in selected_nodes:
        entry = index.get(node_id) or {}
        names = [str(name) for name in (entry.get("mediaNames") or []) if name]
        if not names:
            continue
        if entry.get("isDirectory"):
            fid = str(entry.get("fid") or "").strip() or "__root__"
        else:
            parent = index.get(entry.get("parentId") or "") or {}
            fid = (
                str(parent.get("fid") or "").strip()
                or str(entry.get("fid") or "").strip()
                or "__root__"
            )
        bucket = by_fid.setdefault(fid, [])
        for name in names:
            if name not in bucket:
                bucket.append(name)

    jobs: list[dict] = []
    for fid, names in by_fid.items():
        names = sorted(names, key=str.casefold)
        shareurl = (
            str(base_shareurl or "")
            if fid == "__root__"
            else share_url_for_directory(base_shareurl, fid)
        )
        jobs.append(
            {
                "fid": fid,
                "shareurl": shareurl,
                "pattern": _pattern_for_names(names),
                "fileNames": names,
                "fileCount": len(names),
            }
        )
    if jobs:
        return jobs
    return [
        {
            "fid": "__root__",
            "shareurl": str(base_shareurl or ""),
            "pattern": r"(?i)^(?!.*\.(?:zip|rar|7z)$).*$",
            "fileNames": [],
            "fileCount": 0,
        }
    ]


_NO_TRANSFER = re.compile(r"没有新的转存任务|没有新的文件|未匹配到|匹配到\s*0")
_ARIA2_PUSHED = re.compile(r"Aria2\s*下载|📥\s*Aria2", re.I)
_TRANSFER_ERROR = re.compile(
    r"失败|illegal text|登录已过期|参数错误|转存失败",
    re.I,
)


def assess_qas_run_events(events: list[str]) -> None:
    """Raise ClientError-compatible PlanningError when QAS transferred nothing."""
    blob = "\n".join(str(item) for item in events)
    if _ARIA2_PUSHED.search(blob):
        return
    if _TRANSFER_ERROR.search(blob):
        raise PlanningError("QAS execution failed: transfer error in QAS output")
    if _NO_TRANSFER.search(blob):
        raise PlanningError(
            "QAS transferred nothing: pattern matched no files "
            "(often caused by selecting multiple season folders without deep-link jobs)"
        )


class DownloadPlanner:
    def __init__(
        self,
        *,
        qas,
        store: StateStore,
        routing: dict,
        path_exists: Callable[[str], bool] = os.path.exists,
        path_guard: PathGuard | None = None,
    ):
        self.qas = qas
        self.store = store
        self.routing = routing
        self.path_exists = path_exists
        self.path_guard = path_guard
        self.incoming_root = staging_root(routing)

    def _candidate_details(self, query: str) -> list[dict]:
        candidates = self.qas.search(query, deep=True)
        results = []
        for candidate in candidates:
            share_url = candidate.get("shareurl") or candidate.get("url")
            if not share_url:
                continue
            try:
                details = self.qas.get_share_preview(share_url)
            except ClientError:
                continue
            score = score_candidate(query, candidate, details)
            if "archive_only" in score.penalties:
                continue
            results.append(
                {
                    "shareurl": share_url,
                    "score": score.score,
                    "scoreReasons": score.reasons,
                    "penalties": score.penalties,
                    "candidate": candidate,
                    "details": details,
                }
            )
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def plan(self, query_or_url: str, *, query_hint: str = "") -> dict:
        direct_match = QUARK_LINK.search(query_or_url)
        hint = _clean_hint(query_hint or query_or_url)
        if direct_match:
            share_url = direct_match.group(0)
            details = self.qas.get_share_preview(share_url)
            candidate = {
                "taskname": details.get("share", {}).get("title", hint),
                "content": " ".join(
                    str(item.get("file_name", ""))
                    for item in details.get("list", [])
                ),
            }
            candidate_score = score_candidate(hint, candidate, details)
            ranked = [
                {
                    "shareurl": share_url,
                    "score": candidate_score.score,
                    "scoreReasons": candidate_score.reasons,
                    "penalties": candidate_score.penalties,
                    "candidate": candidate,
                    "details": details,
                }
            ]
        else:
            ranked = self._candidate_details(hint)

        if not ranked:
            raise PlanningError("no valid resource candidates")

        selected = ranked[0]
        classification = classify(hint, selected["details"])
        task_id = f"rd-{uuid.uuid4().hex[:12]}"
        paths = build_paths(classification, self.routing, task_id)
        warnings = []

        if selected["score"] < 70:
            warnings.append("candidate_score_below_threshold")
        if len(ranked) > 1 and selected["score"] - ranked[1]["score"] < 10:
            warnings.append("candidate_scores_too_close")
        if classification.confidence < 0.85:
            warnings.append("classification_low_confidence")
        if self.path_exists(paths["final_path"]):
            warnings.append("final_path_exists")

        ongoing = any(
            token in (query_hint or query_or_url)
            for token in ("追更", "连载", "每周更新", "自动更新")
        )
        task = {
            "taskname": classification.title,
            "shareurl": selected["shareurl"],
            "savepath": paths["cloud_path"],
            "pattern": r"(?i)^(?!.*\.(?:zip|rar|7z)$).*$",
            "replace": "",
            "runweek": [1, 2, 3, 4, 5, 6, 7],
            "addition": {
                "aria2": {
                    "auto_download": True,
                    "download_subdir": True,
                    "save_path": paths["aria2_save_path"],
                    "pause": False,
                }
            },
        }
        classification_data = {
            "mediaType": classification.media_type,
            "title": classification.title,
            "year": classification.year,
            "season": classification.season,
            "episodes": classification.episodes,
            "confidence": classification.confidence,
            "reasons": classification.reasons,
        }
        plan_payload = {
            "taskId": task_id,
            "action": "subscribe" if ongoing else "download",
            "selected": {
                "shareurl": selected["shareurl"],
                "score": selected["score"],
                "scoreReasons": selected["scoreReasons"],
                "penalties": selected["penalties"],
            },
            "alternatives": [
                {
                    "shareurl": item["shareurl"],
                    "score": item["score"],
                    "penalties": item["penalties"],
                }
                for item in ranked[1:5]
            ],
            "classification": classification_data,
            "cloudPath": paths["cloud_path"],
            "stagingPath": paths["staging_path"],
            "finalPath": paths["final_path"],
            "task": task,
            "warnings": warnings,
            "requiresConfirmation": True,
        }
        plan_id = self.store.create_plan("download", plan_payload)
        return {"planId": plan_id, **plan_payload}

    def plan_selected(
        self,
        candidate_id: str,
        *,
        node_ids: list[str] | None = None,
        preferred_media_type: str | None = None,
    ) -> dict:
        try:
            candidate = self.store.get_candidate(candidate_id)
        except PlanError as error:
            raise PlanningError(str(error)) from None

        selected_nodes = [str(item).strip() for item in (node_ids or []) if str(item).strip()]
        if not selected_nodes:
            raise PlanningError(
                "selection_required: run mediactl tree and pass --node nodeId"
            )

        index = candidate.get("treeIndex")
        if not isinstance(index, dict) or not index:
            raise PlanningError(
                "selection_required: run mediactl tree before planning"
            )

        details = candidate.get("details")
        if not isinstance(details, dict):
            details = {"share": {}, "list": []}
        tree_stats = details.get("treeStats") if isinstance(details.get("treeStats"), dict) else {}
        truncated = bool(tree_stats.get("truncated"))

        selected_files: list[str] = []
        seen: set[str] = set()
        new_episodes = list(candidate.get("newEpisodes") or [])
        update_mode = bool(candidate.get("updateMode") or new_episodes)
        title_key = str(
            candidate.get("titleKey")
            or normalize_title_key(str(candidate.get("query") or ""))
        )
        allowed_abs = {
            int(item["episode"]) for item in new_episodes
        } if update_mode else set()
        allowed = {
            _episode_tuple(item) for item in new_episodes
        } if update_mode else set()
        default_season = None
        if update_mode and new_episodes:
            seasons = {int(item["season"]) for item in new_episodes}
            if len(seasons) == 1:
                default_season = next(iter(seasons))

        for node_id in selected_nodes:
            entry = index.get(node_id)
            if not entry:
                raise PlanningError(f"unknown tree node: {node_id}")
            if truncated and entry.get("isDirectory"):
                raise PlanningError(
                    "truncated_tree: directory nodes are not allowed when "
                    "treeStats.truncated=true; select concrete file nodes or re-fetch tree"
                )
            for name in entry.get("mediaNames") or []:
                key_name = str(name)
                if not key_name or key_name in seen:
                    continue
                if update_mode:
                    mapped = extract_episode_key(
                        key_name,
                        title_key,
                        default_season=default_season,
                    )
                    if mapped is None:
                        continue
                    # Prefer absolute episode match for continuous long-form anime.
                    if mapped.episode in allowed_abs or (
                        mapped.season,
                        mapped.episode,
                        mapped.special,
                    ) in allowed:
                        seen.add(key_name)
                        selected_files.append(key_name)
                    continue
                seen.add(key_name)
                selected_files.append(key_name)
        selected_files = sorted(selected_files, key=str.casefold)
        if not selected_files:
            raise PlanningError(
                "selected nodes contain no media files"
                + (" matching newEpisodes" if update_mode else "")
            )

        if update_mode:
            if not new_episodes:
                raise PlanningError(
                    "update_selection_required: candidate has updateMode but empty newEpisodes"
                )
            for name in selected_files:
                key = extract_episode_key(
                    name,
                    title_key,
                    default_season=default_season,
                )
                if key is None:
                    raise PlanningError(
                        f"update_selection_invalid: cannot map file to episode: {name}"
                    )
                if key.episode not in allowed_abs and (
                    key.season,
                    key.episode,
                    key.special,
                ) not in allowed:
                    raise PlanningError(
                        "update_selection_invalid: selected files must be a subset of newEpisodes"
                    )
            selected_files = [
                name
                for name in selected_files
                if name.casefold().endswith(VIDEO_EXTENSIONS)
            ] or selected_files

        # Prefer classification against the selected media only.
        selected_details = {
            "share": details.get("share") if isinstance(details.get("share"), dict) else {},
            "list": [
                {"file_name": name, "dir": False}
                for name in selected_files
            ],
        }

        query = str(candidate.get("query", ""))
        preferred = preferred_media_type or candidate.get("mediaType")
        classification = classify(
            query,
            selected_details,
            preferred_type=str(preferred) if preferred else None,
        )
        task_id = f"rd-{uuid.uuid4().hex[:12]}"
        paths = build_paths(classification, self.routing, task_id)
        transfer_jobs = build_transfer_jobs(
            str(candidate.get("shareurl") or ""),
            index,
            selected_nodes,
        )
        if update_mode:
            selected_set = set(selected_files)
            filtered_jobs = []
            for job in transfer_jobs:
                names = [
                    name
                    for name in (job.get("fileNames") or [])
                    if name in selected_set
                ]
                if not names:
                    continue
                filtered_jobs.append(
                    {
                        "fid": str(job.get("fid") or "__root__"),
                        "shareurl": job["shareurl"],
                        "pattern": _pattern_for_names(names),
                        "fileNames": names,
                        "fileCount": len(names),
                    }
                )
            if not filtered_jobs:
                filtered_jobs = [
                    {
                        "fid": "__root__",
                        "shareurl": str(candidate.get("shareurl") or ""),
                        "pattern": _pattern_for_names(selected_files),
                        "fileNames": list(selected_files),
                        "fileCount": len(selected_files),
                    }
                ]
            transfer_jobs = filtered_jobs
        primary = transfer_jobs[0]
        pattern = primary["pattern"]
        transfer_shareurl = primary["shareurl"]

        warnings = []
        if classification.confidence < 0.85:
            warnings.append("classification_low_confidence")
        if not preferred_media_type and not candidate.get("mediaType"):
            warnings.append("confirm_media_type")
        if self.path_exists(paths["final_path"]):
            warnings.append("final_path_exists")
        if truncated:
            warnings.append("truncated_tree_file_selection")
        task = {
            "taskname": classification.title,
            "shareurl": transfer_shareurl,
            "savepath": paths["cloud_path"],
            "pattern": pattern,
            "replace": "",
            "runweek": [1, 2, 3, 4, 5, 6, 7],
            "addition": {
                "aria2": {
                    "auto_download": True,
                    "download_subdir": True,
                    "save_path": paths["aria2_save_path"],
                    "pause": False,
                }
            },
        }
        transfer_tasks = [
            {
                **task,
                "shareurl": job["shareurl"],
                "pattern": job["pattern"],
            }
            for job in transfer_jobs
        ]
        expected_manifest = build_expected_manifest(
            transfer_jobs=transfer_jobs,
            selected_files=selected_files,
            title_key=title_key or normalize_title_key(classification.title),
            new_episodes=new_episodes if update_mode else None,
            default_season=classification.season,
        )
        incremental = {
            "existingEpisodes": list(candidate.get("existingEpisodes", [])),
            "newEpisodes": (
                list(expected_manifest["expectedEpisodeKeys"])
                if update_mode
                else list(candidate.get("newEpisodes", []))
            ),
            "selectedFiles": selected_files,
            "selectedNodes": selected_nodes,
            "updateMode": update_mode,
        }
        classification_data = {
            "mediaType": classification.media_type,
            "title": classification.title,
            "year": classification.year,
            "season": classification.season,
            "episodes": classification.episodes,
            "confidence": classification.confidence,
            "reasons": classification.reasons,
        }
        title_key = str(
            candidate.get("titleKey")
            or normalize_title_key(classification.title)
        )
        updated = dict(candidate)
        updated["selectedFiles"] = selected_files
        updated["selectedNodes"] = selected_nodes
        updated["details"] = selected_details
        self.store.update_candidate(candidate_id, updated)

        plan_payload = {
            "schemaVersion": 1,
            "taskId": task_id,
            "titleKey": title_key,
            "action": "download",
            "candidateId": candidate_id,
            "classification": classification_data,
            "cloudPath": paths["cloud_path"],
            "stagingPath": paths["staging_path"],
            "finalPath": paths["final_path"],
            "incremental": incremental,
            "expectedManifest": expected_manifest,
            "task": task,
            "transferTasks": transfer_tasks,
            "warnings": warnings,
            "requiresConfirmation": True,
        }
        plan_id = self.store.create_plan("download", plan_payload)
        return {
            "planId": plan_id,
            "taskId": task_id,
            "action": "download",
            "classification": classification_data,
            "stagingPath": paths["staging_path"],
            "finalPath": paths["final_path"],
            "cloudPath": paths["cloud_path"],
            "incremental": incremental,
            "expectedManifest": expected_manifest,
            "warnings": warnings,
            "requiresConfirmation": True,
            "transferJobCount": len(transfer_tasks),
        }

    def execute(self, plan_id: str, *, confirmed: bool = False) -> dict:
        try:
            plan = self.store.read_plan(plan_id, "download")
        except PlanError as error:
            raise PlanningError(str(error)) from None
        if not confirmed:
            raise PlanningError("download plan requires confirmation")
        try:
            plan = self.store.consume_plan(plan_id, "download")
        except PlanError as error:
            raise PlanningError(str(error)) from None

        classification = plan["classification"]
        expected_manifest = plan.get("expectedManifest") or {}
        episode_keys = list(
            expected_manifest.get("expectedEpisodeKeys")
            or plan.get("incremental", {}).get("newEpisodes", [])
        )
        task_record = {
            "task_id": plan["taskId"],
            "title": classification["title"],
            "title_key": plan.get(
                "titleKey",
                normalize_title_key(classification["title"]),
            ),
            "media_type": classification["mediaType"],
            "qas_task_name": plan["task"]["taskname"],
            "aria2_gids": [],
            "episode_keys": episode_keys,
            "expected_manifest": expected_manifest,
            "aria2_dir": f"/nas/{plan['task']['addition']['aria2']['save_path']}",
            "cloud_path": str(plan.get("cloudPath") or plan["task"].get("savepath") or ""),
            "recover_attempts": 0,
            "staging_path": plan["stagingPath"],
            "final_path": plan["finalPath"],
            "status": "starting",
        }
        self.store.upsert_task(task_record)
        try:
            staging = Path(plan["stagingPath"])
            if self.path_guard is not None:
                try:
                    resolved = self.path_guard.resolve_target(str(staging))
                    self.path_guard.assert_mutable(resolved)
                except PathGuardError as error:
                    raise PlanningError(f"unsafe staging path: {error}") from None
                incoming = self.incoming_root.resolve(strict=False)
                if not resolved.is_relative_to(incoming):
                    raise PlanningError(
                        "unsafe staging path: must be under downloads/.incoming/<task-id>"
                    )
                staging = resolved
            ensure_aria2_writable(staging)
            if plan["action"] == "subscribe":
                self.qas.add_task(plan["task"])
            transfer_tasks = list(plan.get("transferTasks") or [])
            if not transfer_tasks:
                transfer_tasks = [plan["task"]]
            for transfer_task in transfer_tasks:
                result = self.qas.run_task(transfer_task)
                events = list(result.get("events") or [])
                assess_qas_run_events(events)
            task_record["status"] = "submitted"
            self.store.upsert_task(task_record)
            return {
                "taskId": plan["taskId"],
                "status": "submitted",
                "action": plan["action"],
                "transferJobCount": len(transfer_tasks),
                "stagingPath": plan["stagingPath"],
                "expectedFileCount": int(
                    expected_manifest.get("expectedFileCount") or 0
                ),
            }
        except PlanningError:
            task_record["status"] = "failed"
            self.store.upsert_task(task_record)
            raise
        except Exception as error:
            task_record["status"] = "failed"
            self.store.upsert_task(task_record)
            message = str(error)
            raise PlanningError(f"QAS execution failed: {message}") from None

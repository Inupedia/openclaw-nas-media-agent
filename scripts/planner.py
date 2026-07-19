import os
import re
import uuid
from pathlib import Path
from typing import Callable

from episode_diff import normalize_title_key
from media_classifier import classify, score_candidate
from media_namer import build_paths
from qas_client import share_url_for_directory
from state_store import PlanError, StateStore


class PlanningError(RuntimeError):
    pass


QUARK_LINK = re.compile(r"https://pan\.quark\.cn/s/[A-Za-z0-9_-]+")
INTENT_PREFIX = re.compile(r"^\s*(?:请|帮我|给我)?\s*(?:下载|追更|搜索|查找)\s*")


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
    ):
        self.qas = qas
        self.store = store
        self.routing = routing
        self.path_exists = path_exists

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
            "requiresConfirmation": bool(warnings),
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

        selected_files: list[str] = []
        seen: set[str] = set()
        for node_id in selected_nodes:
            entry = index.get(node_id)
            if not entry:
                raise PlanningError(f"unknown tree node: {node_id}")
            for name in entry.get("mediaNames") or []:
                key = str(name)
                if key and key not in seen:
                    seen.add(key)
                    selected_files.append(key)
        selected_files = sorted(selected_files, key=str.casefold)
        if not selected_files:
            raise PlanningError("selected nodes contain no media files")

        details = candidate.get("details")
        if not isinstance(details, dict):
            details = {"share": {}, "list": []}
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
        incremental = {
            "existingEpisodes": list(candidate.get("existingEpisodes", [])),
            "newEpisodes": list(candidate.get("newEpisodes", [])),
            "selectedFiles": selected_files,
            "selectedNodes": selected_nodes,
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
            "task": task,
            "transferTasks": transfer_tasks,
            "warnings": warnings,
            "requiresConfirmation": bool(warnings),
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
            "warnings": warnings,
            "requiresConfirmation": bool(warnings),
            "transferJobCount": len(transfer_tasks),
        }

    def execute(self, plan_id: str, *, confirmed: bool = False) -> dict:
        try:
            plan = self.store.read_plan(plan_id, "download")
        except PlanError as error:
            raise PlanningError(str(error)) from None
        if plan["requiresConfirmation"] and not confirmed:
            raise PlanningError("download plan requires confirmation")
        try:
            plan = self.store.consume_plan(plan_id, "download")
        except PlanError as error:
            raise PlanningError(str(error)) from None

        classification = plan["classification"]
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
            "episode_keys": list(
                plan.get("incremental", {}).get("newEpisodes", [])
            ),
            "aria2_dir": f"/nas/{plan['task']['addition']['aria2']['save_path']}",
            "staging_path": plan["stagingPath"],
            "final_path": plan["finalPath"],
            "status": "starting",
        }
        self.store.upsert_task(task_record)
        try:
            staging = Path(plan["stagingPath"])
            staging.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(staging, 0o777)
            except OSError:
                pass
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

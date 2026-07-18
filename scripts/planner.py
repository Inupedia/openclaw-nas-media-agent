import os
import re
import uuid
from typing import Callable

from episode_diff import normalize_title_key
from media_classifier import classify, score_candidate
from media_namer import build_paths
from qas_client import ClientError
from state_store import PlanError, StateStore


class PlanningError(RuntimeError):
    pass


QUARK_LINK = re.compile(r"https://pan\.quark\.cn/s/[A-Za-z0-9_-]+")
INTENT_PREFIX = re.compile(r"^\s*(?:请|帮我|给我)?\s*(?:下载|追更|搜索|查找)\s*")


def _clean_hint(value: str) -> str:
    return INTENT_PREFIX.sub("", value or "").strip()


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
                details = self.qas.get_share_expanded(share_url)
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
            details = self.qas.get_share_expanded(share_url)
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

    def plan_selected(self, candidate_id: str) -> dict:
        try:
            candidate = self.store.get_candidate(candidate_id)
        except PlanError as error:
            raise PlanningError(str(error)) from None
        details = candidate.get("details")
        if not isinstance(details, dict):
            raise PlanningError("candidate must be previewed before planning")

        query = str(candidate.get("query", ""))
        classification = classify(query, details)
        task_id = f"rd-{uuid.uuid4().hex[:12]}"
        paths = build_paths(classification, self.routing, task_id)
        selected_files = list(candidate.get("selectedFiles", []))
        if not selected_files:
            allowed_extensions = (
                ".mkv",
                ".mp4",
                ".avi",
                ".mov",
                ".m4v",
                ".ts",
                ".ass",
                ".ssa",
                ".srt",
                ".vtt",
            )
            selected_files = [
                str(item.get("file_name", ""))
                for item in details.get("list", [])
                if not item.get("dir")
                and str(item.get("file_name", "")).lower().endswith(
                    allowed_extensions
                )
            ]
        selected_files = sorted(
            {name for name in selected_files if name},
            key=str.casefold,
        )
        if not selected_files:
            raise PlanningError("candidate has no selectable media files")

        warnings = []
        if classification.confidence < 0.85:
            warnings.append("classification_low_confidence")
        if self.path_exists(paths["final_path"]):
            warnings.append("final_path_exists")
        pattern = "(?i)^(?:" + "|".join(
            re.escape(name) for name in selected_files
        ) + ")$"
        task = {
            "taskname": classification.title,
            "shareurl": candidate["shareurl"],
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
        incremental = {
            "existingEpisodes": list(candidate.get("existingEpisodes", [])),
            "newEpisodes": list(candidate.get("newEpisodes", [])),
            "selectedFiles": selected_files,
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
            "incremental": incremental,
            "warnings": warnings,
            "requiresConfirmation": bool(warnings),
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
            if plan["action"] == "subscribe":
                self.qas.add_task(plan["task"])
            result = self.qas.run_task(plan["task"])
            task_record["status"] = "submitted"
            self.store.upsert_task(task_record)
            return {
                "taskId": plan["taskId"],
                "status": "submitted",
                "action": plan["action"],
            }
        except Exception as error:
            task_record["status"] = "failed"
            self.store.upsert_task(task_record)
            message = str(error)
            raise PlanningError(f"QAS execution failed: {message}") from None

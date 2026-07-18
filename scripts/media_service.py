from episode_diff import (
    EpisodeKey,
    compute_missing,
    extract_episode_key,
    normalize_title_key,
    select_incremental_files,
)
from media_classifier import (
    ARCHIVE_EXTENSIONS,
    extract_candidate_spec,
)
from output_contract import success
from pansou_client import PanSouError, normalize_quark_url, query_variants
from jiaofu_client import JiaofuError
from qas_client import ClientError, MEDIA_EXTENSIONS
from state_store import StateStore
import hashlib


class MediaService:
    def __init__(self, catalog, qas, store: StateStore, pansou=None, jiaofu=None):
        self.catalog = catalog
        self.qas = qas
        self.store = store
        self.pansou = pansou
        self.jiaofu = jiaofu

    def search(
        self,
        query: str,
        media_type: str | None = None,
        *,
        update: bool = False,
    ) -> dict:
        local = self.catalog.lookup(query, media_type)
        if local.get("found") and not update:
            return success(
                {
                    "local": local,
                    "missing": [],
                    "remoteCandidates": [],
                },
                terminal=True,
                next_action="stop_local_exists",
            )

        if update:
            return self._search_update(query, media_type, local)

        candidates, warnings = self._discover_candidates(query)
        projected = []
        rejected = {}
        for candidate in candidates:
            share_url = candidate.get("shareurl") or candidate.get("url")
            if not share_url:
                self._count_rejection(rejected, "missing_share")
                continue
            try:
                details = self.qas.get_share_preview(share_url)
            except ClientError:
                self._count_rejection(rejected, "expired_or_unavailable")
                continue
            items = list(details.get("list", []) or [])
            names = [
                str(item.get("file_name", ""))
                for item in items
                if item.get("file_name") and not item.get("dir")
            ]
            share_meta = details.get("share") if isinstance(details.get("share"), dict) else {}
            has_share_files = (
                int(share_meta.get("file_only_num") or 0) > 0
                or int(share_meta.get("video_total") or 0) > 0
                or int(share_meta.get("all_file_num") or 0) > 0
                or any(
                    item.get("dir") and int(item.get("include_items") or 0) > 0
                    for item in items
                    if isinstance(item, dict)
                )
            )
            if not names and not has_share_files:
                self._count_rejection(rejected, "empty")
                continue
            if names and all(name.casefold().endswith(ARCHIVE_EXTENSIONS) for name in names):
                self._count_rejection(rejected, "archive_only")
                continue
            specification = extract_candidate_spec(details)
            if not specification["videoFileCount"]:
                self._count_rejection(rejected, "no_video")
                continue
            candidate_id = self.store.create_candidate(
                {
                    "query": query,
                    "mediaType": media_type,
                    "shareurl": share_url,
                    "candidate": candidate,
                    "details": details,
                    "specification": specification,
                }
            )
            projected.append(
                {
                    "candidateId": candidate_id,
                    "title": str(
                        candidate.get("taskname")
                        or candidate.get("title")
                        or "未命名候选"
                    ),
                    "provider": "quark",
                    "discoverySources": candidate["_discoverySources"],
                    "specification": self._safe_specification(specification),
                }
            )

        projected.sort(key=self._candidate_sort_key)
        return success(
            {
                "local": local,
                "missing": [],
                "remoteCandidates": projected,
                "specificationGroups": self._group_candidates(projected),
                "candidateCount": len(projected),
                "rejectedCandidateCounts": rejected,
                **({"warnings": warnings} if warnings else {}),
            },
            terminal=not projected,
            next_action="choose_candidate" if projected else "no_candidates",
        )

    def _search_update(
        self,
        query: str,
        media_type: str | None,
        local: dict,
    ) -> dict:
        title = str(local.get("title") or local.get("queryTitle") or query)
        title_key = normalize_title_key(title)
        local_keys = {
            EpisodeKey(
                title_key,
                int(item["season"]),
                int(item["episode"]),
                item.get("special"),
            )
            for item in local.get("episodes", [])
        }
        pending_keys = {
            EpisodeKey(title_key, season, episode, special)
            for season, episode, special in self.store.pending_episode_refs(
                title_key
            )
        }
        seasons = {item.season for item in local_keys}
        default_season = next(iter(seasons)) if len(seasons) == 1 else 1

        projected = []
        all_missing: set[EpisodeKey] = set()
        selection_failed = False
        candidates, warnings = self._discover_candidates(query)
        for candidate in candidates:
            share_url = candidate.get("shareurl") or candidate.get("url")
            if not share_url:
                continue
            details = self.qas.get_share_preview(share_url)
            items = list(details.get("list", []) or [])
            remote_keys = {
                key
                for item in items
                if not item.get("dir")
                and (
                    key := extract_episode_key(
                        str(item.get("file_name", "")),
                        title_key,
                        default_season=default_season,
                    )
                )
                is not None
            }
            missing = compute_missing(
                remote_keys,
                local_keys,
                pending_keys,
                set(),
            )
            if not missing:
                continue
            all_missing.update(missing)
            selection = select_incremental_files(
                items,
                wanted=missing,
                title_key=title_key,
                default_season=default_season,
            )
            if not selection["selectable"]:
                selection_failed = True
                continue
            serialized_missing = self._serialize_episodes(missing)
            specification = extract_candidate_spec(details)
            candidate_id = self.store.create_candidate(
                {
                    "query": query,
                    "mediaType": media_type,
                    "titleKey": title_key,
                    "shareurl": share_url,
                    "candidate": candidate,
                    "details": details,
                    "selectedFiles": selection["files"],
                    "existingEpisodes": self._serialize_episodes(local_keys),
                    "newEpisodes": serialized_missing,
                    "specification": specification,
                }
            )
            projected.append(
                {
                    "candidateId": candidate_id,
                    "title": str(
                        candidate.get("taskname")
                        or details.get("share", {}).get("title")
                        or "未命名候选"
                    ),
                    "provider": "quark",
                    "discoverySources": candidate["_discoverySources"],
                    "newEpisodes": serialized_missing,
                    "specification": self._safe_specification(specification),
                }
            )

        if not all_missing:
            return success(
                {
                    "local": local,
                    "missing": [],
                    "remoteCandidates": [],
                    **({"warnings": warnings} if warnings else {}),
                },
                terminal=True,
                next_action="already_up_to_date",
            )
        if not projected:
            return success(
                {
                    "local": local,
                    "missing": self._serialize_episodes(all_missing),
                    "remoteCandidates": [],
                    **({"warnings": warnings} if warnings else {}),
                },
                terminal=True,
                next_action=(
                    "incremental_selection_unavailable"
                    if selection_failed
                    else "no_candidates"
                ),
            )
        projected.sort(key=self._candidate_sort_key)
        return success(
            {
                "local": local,
                "missing": self._serialize_episodes(all_missing),
                "remoteCandidates": projected,
                "specificationGroups": self._group_candidates(projected),
                "candidateCount": len(projected),
                "rejectedCandidateCounts": {},
                **({"warnings": warnings} if warnings else {}),
            },
            terminal=False,
            next_action="choose_candidate",
        )

    def _discover_candidates(self, query: str) -> tuple[list[dict], list[str]]:
        warnings = []
        jiaofu_hits = self._discover_jiaofu(query, warnings)
        if jiaofu_hits:
            return jiaofu_hits, warnings
        return self._discover_qas_pansou(query, warnings), warnings

    def _discover_jiaofu(
        self,
        query: str,
        warnings: list[str],
    ) -> list[dict]:
        if self.jiaofu is None:
            return []
        discovered = []
        by_share: dict[str, dict] = {}
        try:
            for variant in query_variants(query):
                for raw_candidate in self.jiaofu.search(variant):
                    candidate = dict(raw_candidate)
                    share_url = candidate.get("shareurl") or candidate.get("url")
                    normalized = normalize_quark_url(share_url)
                    key = normalized or str(share_url or "").strip()
                    if not key:
                        continue
                    existing = by_share.get(key)
                    if existing:
                        if "jiaofu" not in existing["_discoverySources"]:
                            existing["_discoverySources"].append("jiaofu")
                        continue
                    if normalized:
                        candidate["shareurl"] = normalized
                        candidate.pop("url", None)
                    candidate["_discoverySources"] = ["jiaofu"]
                    by_share[key] = candidate
                    discovered.append(candidate)
        except JiaofuError:
            warnings.append("jiaofu_unavailable")
            return []
        return discovered

    def _discover_qas_pansou(
        self,
        query: str,
        warnings: list[str],
    ) -> list[dict]:
        discovered = []
        by_share = {}
        pansou_available = self.pansou is not None
        pansou_keys = set()
        pansou_limit = int(
            getattr(self.pansou, "max_candidates", 50)
        )

        for variant in query_variants(query):
            sources = [("qas", self.qas.search(variant, deep=True))]
            if pansou_available:
                try:
                    sources.append(("pansou", self.pansou.search(variant)))
                except PanSouError:
                    warnings.append("pansou_unavailable")
                    pansou_available = False

            for source, candidates in sources:
                for raw_candidate in candidates:
                    candidate = dict(raw_candidate)
                    share_url = candidate.get("shareurl") or candidate.get("url")
                    normalized = normalize_quark_url(share_url)
                    key = normalized or str(share_url or "").strip()
                    if source == "pansou" and key not in pansou_keys:
                        if len(pansou_keys) >= pansou_limit:
                            continue
                        pansou_keys.add(key)
                    if not key:
                        discovered.append(
                            {
                                **candidate,
                                "_discoverySources": [source],
                            }
                        )
                        continue
                    existing = by_share.get(key)
                    if existing:
                        if source not in existing["_discoverySources"]:
                            existing["_discoverySources"].append(source)
                        continue
                    if normalized:
                        candidate["shareurl"] = normalized
                        candidate.pop("url", None)
                    candidate["_discoverySources"] = [source]
                    by_share[key] = candidate
                    discovered.append(candidate)

        return discovered

    @staticmethod
    def _serialize_episodes(
        episodes: set[EpisodeKey],
    ) -> list[dict]:
        return [
            {
                "season": item.season,
                "episode": item.episode,
                **({"special": item.special} if item.special else {}),
            }
            for item in sorted(episodes)
        ]

    @staticmethod
    def _count_rejection(counts: dict, reason: str) -> None:
        counts[reason] = int(counts.get(reason, 0)) + 1

    @staticmethod
    def _safe_specification(specification: dict) -> dict:
        return {
            key: value
            for key, value in specification.items()
            if key != "groupKey"
        }

    @staticmethod
    def _candidate_sort_key(candidate: dict) -> tuple:
        specification = candidate["specification"]
        subtitle_order = {
            "zh_en": 0,
            "zh": 1,
            "en": 2,
            "unknown": 3,
            "none": 4,
        }
        resolution = str(specification.get("resolution", "unknown"))
        resolution_value = (
            int(resolution[:-1])
            if resolution.endswith("p") and resolution[:-1].isdigit()
            else 0
        )
        return (
            subtitle_order.get(specification.get("subtitleClass"), 5),
            -resolution_value,
            int(specification.get("totalBytes") or 0),
            candidate["candidateId"],
        )

    @classmethod
    def _group_candidates(cls, candidates: list[dict]) -> list[dict]:
        groups = {}
        for candidate in candidates:
            specification = candidate["specification"]
            coverage = tuple(
                (
                    int(item.get("season", 0)),
                    int(item.get("episode", 0)),
                )
                for item in specification.get("episodeCoverage", [])
            )
            key = (
                specification.get("resolution"),
                specification.get("dynamicRange"),
                specification.get("videoCodec"),
                specification.get("audioFormat"),
                specification.get("subtitleClass"),
                coverage,
            )
            group = groups.setdefault(
                key,
                {
                    "specification": specification,
                    "candidates": [],
                },
            )
            group["candidates"].append(
                {
                    "candidateId": candidate["candidateId"],
                    "title": candidate["title"],
                    "provider": candidate["provider"],
                    "discoverySources": candidate["discoverySources"],
                    "totalBytes": specification.get("totalBytes", 0),
                    **(
                        {"newEpisodes": candidate["newEpisodes"]}
                        if candidate.get("newEpisodes")
                        else {}
                    ),
                }
            )
        result = list(groups.values())
        for group in result:
            group["candidateCount"] = len(group["candidates"])
        return result

    def preview(self, candidate_id: str) -> dict:
        candidate = self.store.get_candidate(candidate_id)
        details = self.qas.get_share_preview(candidate["shareurl"])
        candidate = dict(candidate)
        candidate["details"] = details
        self.store.update_candidate(candidate_id, candidate)

        files = []
        for item in list(details.get("list", []) or [])[:200]:
            files.append(
                {
                    "name": str(item.get("file_name", "")),
                    "isDirectory": bool(item.get("dir")),
                    "size": int(item.get("size") or 0),
                }
            )
        share_meta = details.get("share") if isinstance(details.get("share"), dict) else {}
        listed_files = [
            item
            for item in list(details.get("list", []) or [])
            if not item.get("dir")
        ]
        file_count = max(
            len(listed_files),
            int(share_meta.get("file_only_num") or 0),
            int(share_meta.get("video_total") or 0),
        )
        return success(
            {
                "candidateId": candidate_id,
                "title": str(
                    details.get("share", {}).get("title")
                    or candidate.get("candidate", {}).get("taskname")
                    or "未命名候选"
                ),
                "files": files,
                "fileCount": file_count,
                "totalBytes": int(share_meta.get("size") or 0),
            },
            terminal=False,
            next_action="plan_or_choose",
        )

    def tree(self, candidate_id: str) -> dict:
        candidate = self.store.get_candidate(candidate_id)
        try:
            raw = self.qas.get_share_tree(candidate["shareurl"])
        except ClientError as error:
            raise ClientError(str(error)) from None

        public_tree, index = self._assign_tree_node_ids(raw.get("tree") or [])
        stats = dict(raw.get("stats") or {})
        share = raw.get("share") if isinstance(raw.get("share"), dict) else {}

        # Flatten media list into details for later classification hints.
        media_list = [
            {
                "file_name": entry["name"],
                "dir": False,
                "size": entry.get("size") or 0,
            }
            for entry in index.values()
            if not entry.get("isDirectory")
            and str(entry.get("name") or "").casefold().endswith(MEDIA_EXTENSIONS)
        ]
        updated = dict(candidate)
        updated["treeIndex"] = index
        updated["details"] = {
            "share": share,
            "list": media_list,
            "treeStats": stats,
        }
        self.store.update_candidate(candidate_id, updated)

        return success(
            {
                "candidateId": candidate_id,
                "title": str(
                    share.get("title")
                    or candidate.get("candidate", {}).get("taskname")
                    or "未命名候选"
                ),
                "tree": public_tree,
                "stats": {
                    "directories": int(stats.get("directories") or 0),
                    "files": int(stats.get("files") or 0),
                    "videos": int(stats.get("videos") or 0),
                    "truncated": bool(stats.get("truncated")),
                    "nodes": int(stats.get("nodes") or len(index)),
                },
            },
            terminal=False,
            next_action="choose_tree_nodes",
        )

    @staticmethod
    def _assign_tree_node_ids(raw_nodes: list) -> tuple[list[dict], dict]:
        index: dict[str, dict] = {}
        counter = 0

        def walk(nodes: list, parent_id: str | None = None) -> list[dict]:
            nonlocal counter
            public = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                counter += 1
                seed = f"{node.get('path')}|{node.get('fid')}|{counter}"
                node_id = "n-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
                children_raw = list(node.get("children") or [])
                children_public = walk(children_raw, node_id)
                child_ids = [child["nodeId"] for child in children_public]
                is_dir = bool(node.get("isDirectory"))
                name = str(node.get("name") or "")
                media_names: list[str] = []
                if is_dir:
                    for child_id in child_ids:
                        media_names.extend(index[child_id].get("mediaNames") or [])
                elif name.casefold().endswith(MEDIA_EXTENSIONS):
                    media_names = [name]
                index[node_id] = {
                    "nodeId": node_id,
                    "name": name,
                    "isDirectory": is_dir,
                    "size": int(node.get("size") or 0),
                    "path": str(node.get("path") or name),
                    "parentId": parent_id,
                    "childIds": child_ids,
                    "mediaNames": media_names,
                    # fid kept only in store for possible future deep ops; never returned
                    "fid": node.get("fid"),
                }
                public.append(
                    {
                        "nodeId": node_id,
                        "name": name,
                        "isDirectory": is_dir,
                        "size": int(node.get("size") or 0),
                        "children": children_public,
                    }
                )
            return public

        return walk(list(raw_nodes or [])), index

    def resolve_tree_selection(
        self,
        candidate_id: str,
        node_ids: list[str],
    ) -> list[str]:
        candidate = self.store.get_candidate(candidate_id)
        index = candidate.get("treeIndex")
        if not isinstance(index, dict) or not index:
            raise ValueError("candidate tree missing; run tree first")
        selected: list[str] = []
        seen: set[str] = set()
        for raw_id in node_ids:
            node_id = str(raw_id or "").strip()
            entry = index.get(node_id)
            if not entry:
                raise ValueError(f"unknown tree node: {node_id}")
            for name in entry.get("mediaNames") or []:
                key = str(name)
                if key and key not in seen:
                    seen.add(key)
                    selected.append(key)
        if not selected:
            raise ValueError("selected nodes contain no media files")
        return sorted(selected, key=str.casefold)

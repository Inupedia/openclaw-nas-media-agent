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
from qas_client import ClientError
from state_store import StateStore


class MediaService:
    def __init__(self, catalog, qas, store: StateStore, pansou=None):
        self.catalog = catalog
        self.qas = qas
        self.store = store
        self.pansou = pansou

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
                details = self.qas.get_share(share_url, show_all=True)
            except ClientError:
                self._count_rejection(rejected, "expired_or_unavailable")
                continue
            items = list(details.get("list", []) or [])
            names = [
                str(item.get("file_name", ""))
                for item in items
                if item.get("file_name") and not item.get("dir")
            ]
            if not names:
                self._count_rejection(rejected, "empty")
                continue
            if all(name.casefold().endswith(ARCHIVE_EXTENSIONS) for name in names):
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
            details = self.qas.get_share(share_url, show_all=True)
            items = list(details.get("list", []) or [])
            remote_keys = {
                key
                for item in items
                if (
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
        discovered = []
        by_share = {}
        warnings = []
        pansou_available = self.pansou is not None

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

        return discovered, warnings

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
        details = self.qas.get_share(
            candidate["shareurl"],
            show_all=True,
        )
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
        return success(
            {
                "candidateId": candidate_id,
                "title": str(
                    details.get("share", {}).get("title")
                    or candidate.get("candidate", {}).get("taskname")
                    or "未命名候选"
                ),
                "files": files,
                "fileCount": len(list(details.get("list", []) or [])),
            },
            terminal=False,
            next_action="plan_or_choose",
        )

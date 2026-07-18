from episode_diff import (
    EpisodeKey,
    compute_missing,
    extract_episode_key,
    normalize_title_key,
    select_incremental_files,
)
from output_contract import success
from state_store import StateStore


class MediaService:
    def __init__(self, catalog, qas, store: StateStore):
        self.catalog = catalog
        self.qas = qas
        self.store = store

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

        projected = []
        for candidate in self.qas.search(query, deep=True):
            share_url = candidate.get("shareurl") or candidate.get("url")
            if not share_url:
                continue
            candidate_id = self.store.create_candidate(
                {
                    "query": query,
                    "mediaType": media_type,
                    "shareurl": share_url,
                    "candidate": candidate,
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
                }
            )

        return success(
            {
                "local": local,
                "missing": [],
                "remoteCandidates": projected,
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
        for candidate in self.qas.search(query, deep=True):
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
                    "newEpisodes": serialized_missing,
                }
            )

        if not all_missing:
            return success(
                {
                    "local": local,
                    "missing": [],
                    "remoteCandidates": [],
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
                },
                terminal=True,
                next_action=(
                    "incremental_selection_unavailable"
                    if selection_failed
                    else "no_candidates"
                ),
            )
        return success(
            {
                "local": local,
                "missing": self._serialize_episodes(all_missing),
                "remoteCandidates": projected,
            },
            terminal=False,
            next_action="choose_candidate",
        )

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

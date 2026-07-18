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

import re
import unicodedata
from pathlib import Path
from typing import Mapping

from library_scanner import scan


QUOTED_TITLE = re.compile(r"[《「『【](.+?)[》」』】]")
YEAR_SUFFIX = re.compile(r"\s*[\(\[]((?:19|20)\d{2})[\)\]]\s*")
SEASON_SUFFIX = re.compile(
    r"(?i)\s*(?:Season\s*\d{1,2}|S\d{1,2}|第\s*\d+\s*季)\s*$"
)
INTENT_WORDS = re.compile(
    r"(?:请|帮我|给我|搜索|查找|找一下|看看|预览|不要下载|下载|"
    r"影视资源|动画资源|动漫资源|电视剧资源|电影资源|资源|"
    r"动画|动漫|电视剧|电影|先|一下)"
)
RESOLUTION = re.compile(r"(?i)(2160p|1080p|720p|4k)")
SKIP_NAMES = {".incoming", ".ready", ".quarantine", "#recycle"}


def query_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    quoted = QUOTED_TITLE.search(normalized)
    if quoted:
        return quoted.group(1).strip()
    cleaned = INTENT_WORDS.sub(" ", normalized)
    cleaned = re.sub(r"[，。！？、,!?;；:：]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .-_")


def _work_title(value: str) -> tuple[str, int | None]:
    normalized = unicodedata.normalize("NFKC", value or "")
    year_match = YEAR_SUFFIX.search(normalized)
    year = int(year_match.group(1)) if year_match else None
    cleaned = YEAR_SUFFIX.sub(" ", normalized)
    cleaned = SEASON_SUFFIX.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .-_"), year


def _title_key(value: str) -> str:
    title, _ = _work_title(value)
    return re.sub(r"[\W_]+", "", title.casefold())


def _resolution_summary(names: list[str]) -> list[str]:
    resolutions = set()
    for name in names:
        for match in RESOLUTION.findall(name):
            value = match.casefold()
            resolutions.add("2160p" if value == "4k" else value)
    order = {"2160p": 0, "1080p": 1, "720p": 2}
    return sorted(resolutions, key=lambda item: (order.get(item, 99), item))


class LibraryCatalog:
    def __init__(
        self,
        roots: Mapping[str, Path],
        *,
        max_candidates: int = 20,
        max_entries: int = 10_000,
    ):
        self.roots = {
            str(media_type): Path(root)
            for media_type, root in roots.items()
        }
        self.max_candidates = max_candidates
        self.max_entries = max_entries

    def _matches(
        self,
        query_key: str,
        media_type: str | None,
    ) -> list[tuple[str, Path, str, int | None]]:
        selected = (
            [(media_type, self.roots[media_type])]
            if media_type in self.roots
            else list(self.roots.items())
            if media_type is None
            else []
        )
        matches = []
        for kind, root in selected:
            if not root.is_dir() or root.is_symlink():
                continue
            root_resolved = root.resolve()
            for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
                if len(matches) >= self.max_candidates:
                    return matches
                if (
                    child.name in SKIP_NAMES
                    or child.name.startswith(".")
                    or child.is_symlink()
                    or not child.is_dir()
                ):
                    continue
                resolved = child.resolve()
                if root_resolved not in resolved.parents:
                    continue
                title, year = _work_title(child.name)
                if _title_key(title) == query_key:
                    matches.append((kind, resolved, title, year))
        return matches

    def lookup(self, query: str, media_type: str | None = None) -> dict:
        requested_title = query_title(query)
        query_key = _title_key(requested_title)
        if not query_key:
            return {
                "found": False,
                "queryTitle": requested_title,
                "matches": [],
            }

        matches = self._matches(query_key, media_type)
        if not matches:
            return {
                "found": False,
                "queryTitle": requested_title,
                "matches": [],
            }

        kind, path, title, year = matches[0]
        entries = scan(path)[: self.max_entries]
        videos = [entry for entry in entries if entry.kind == "video"]
        episodes = sorted(
            {
                (entry.season, entry.episode)
                for entry in videos
                if entry.season is not None and entry.episode is not None
            }
        )
        names = [entry.path.name for entry in videos]
        return {
            "found": True,
            "queryTitle": requested_title,
            "title": title,
            "year": year,
            "mediaType": kind,
            "path": str(path),
            "fileCount": len(videos),
            "totalBytes": sum(entry.size for entry in videos),
            "episodes": [
                {"season": season, "episode": episode}
                for season, episode in episodes
            ],
            "resolutions": _resolution_summary(names),
            "matchCount": len(matches),
        }

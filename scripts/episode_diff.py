import re
import unicodedata
from dataclasses import dataclass


SXXEXX = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
EPISODE = re.compile(r"(?i)\bEP?\s*(\d{1,3})\b")
CHINESE_EPISODE = re.compile(r"第\s*(\d{1,3})\s*集")
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".vtt", ".sub"}


@dataclass(frozen=True, order=True)
class EpisodeKey:
    title_key: str
    season: int
    episode: int
    special: str | None = None


def normalize_title_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[\W_]+", "", text)


def extract_episode_key(
    filename: str,
    title_key: str,
    *,
    default_season: int | None = None,
) -> EpisodeKey | None:
    match = SXXEXX.search(filename)
    if match:
        return EpisodeKey(
            title_key,
            int(match.group(1)),
            int(match.group(2)),
        )
    for pattern in (CHINESE_EPISODE, EPISODE):
        match = pattern.search(filename)
        if match and default_season is not None:
            return EpisodeKey(
                title_key,
                int(default_season),
                int(match.group(1)),
            )
    return None


def compute_missing(
    remote: set[EpisodeKey],
    local: set[EpisodeKey],
    active: set[EpisodeKey],
    planned: set[EpisodeKey],
) -> set[EpisodeKey]:
    return set(remote) - set(local) - set(active) - set(planned)


def select_incremental_files(
    items: list[dict],
    *,
    wanted: set[EpisodeKey],
    title_key: str,
    default_season: int | None,
) -> dict:
    if not wanted:
        return {"selectable": True, "files": []}

    matched_keys = set()
    selected = []
    for item in items:
        if item.get("dir"):
            continue
        filename = str(item.get("file_name", ""))
        key = extract_episode_key(
            filename,
            title_key,
            default_season=default_season,
        )
        if key not in wanted:
            continue
        matched_keys.add(key)
        selected.append(filename)

    if matched_keys != wanted:
        return {"selectable": False, "files": []}
    return {
        "selectable": True,
        "files": sorted(set(selected), key=str.casefold),
    }

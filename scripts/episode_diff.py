import re
import unicodedata
from dataclasses import dataclass


SXXEXX = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
EPISODE = re.compile(r"(?i)\bEP?\s*(\d{1,3})\b")
CHINESE_EPISODE = re.compile(r"第\s*(\d{1,3})\s*集")
# Leading bare episode: "01 4K.mp4", "91.mkv", "092-Title.mkv"
BARE_LEADING = re.compile(
    r"(?i)^0*(\d{1,3})(?=\s|[._\-\[\(]|$)"
)
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
    text = str(filename or "")
    match = SXXEXX.search(text)
    if match:
        return EpisodeKey(
            title_key,
            int(match.group(1)),
            int(match.group(2)),
        )
    season = default_season if default_season is not None else 1
    for pattern in (CHINESE_EPISODE, EPISODE):
        match = pattern.search(text)
        if match:
            return EpisodeKey(
                title_key,
                int(season),
                int(match.group(1)),
            )
    bare = BARE_LEADING.match(text.strip())
    if bare:
        episode = int(bare.group(1))
        # Avoid treating years / resolutions as episodes.
        if 1 <= episode <= 300:
            return EpisodeKey(title_key, int(season), episode)
    return None


def looks_like_absolute_episode_scheme(keys: set[EpisodeKey]) -> bool:
    """True when episode numbers are unique and span a continuous-like range.

    Remote packs for long-running anime often encode absolute episode numbers
    inside SxxExx (S02E27..S02E51) while local files use bare ``01``..``91``.
    Comparing full (season, episode) tuples then falsely marks everything missing.
    """
    if len(keys) < 2:
        return False
    episodes = [item.episode for item in keys]
    if len(episodes) != len(set(episodes)):
        return False
    return max(episodes) >= len(episodes)


def compute_missing(
    remote: set[EpisodeKey],
    local: set[EpisodeKey],
    active: set[EpisodeKey],
    planned: set[EpisodeKey],
) -> set[EpisodeKey]:
    if (
        remote
        and looks_like_absolute_episode_scheme(remote)
        and (
            not local
            or looks_like_absolute_episode_scheme(local)
            or all(item.season == 1 for item in local)
        )
    ):
        blocked = {
            item.episode for item in (local | active | planned)
        }
        return {item for item in remote if item.episode not in blocked}
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

    absolute = looks_like_absolute_episode_scheme(wanted)
    wanted_abs = {item.episode for item in wanted}
    matched_keys: set[EpisodeKey] = set()
    matched_abs: set[int] = set()
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
        if key is None:
            continue
        if absolute:
            if key.episode not in wanted_abs:
                continue
            matched_abs.add(key.episode)
            # Prefer the remote key object from wanted with same absolute ep.
            for wanted_key in wanted:
                if wanted_key.episode == key.episode:
                    matched_keys.add(wanted_key)
                    break
        else:
            if key not in wanted:
                continue
            matched_keys.add(key)
        selected.append(filename)

    if absolute:
        if matched_abs != wanted_abs:
            return {"selectable": False, "files": []}
    elif matched_keys != wanted:
        return {"selectable": False, "files": []}
    return {
        "selectable": True,
        "files": sorted(set(selected), key=str.casefold),
    }

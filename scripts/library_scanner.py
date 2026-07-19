import os
import time
from dataclasses import dataclass
from pathlib import Path

from episode_diff import SXXEXX, extract_episode_key


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}
SKIP_DIRECTORIES = {".incoming", "#recycle"}


@dataclass(frozen=True)
class MediaEntry:
    path: Path
    kind: str
    extension: str
    size: int
    modified_at: float
    season: int | None = None
    episode: int | None = None


def _season_hint(path: Path, root: Path) -> int | None:
    for parent in path.parents:
        if parent == root or parent == path:
            continue
        match = SXXEXX.search(parent.name)
        if match:
            return int(match.group(1))
        lowered = parent.name.casefold()
        if "season" in lowered or parent.name.startswith("第"):
            digits = "".join(ch for ch in parent.name if ch.isdigit())
            if digits:
                try:
                    value = int(digits)
                except ValueError:
                    continue
                if 1 <= value <= 40:
                    return value
        if parent.parent == root:
            break
    return None


def scan(root: Path) -> list[MediaEntry]:
    root = Path(root)
    entries = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name not in SKIP_DIRECTORIES
            and not (Path(current) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(current) / filename
            if path.is_symlink():
                continue
            stat = path.stat()
            extension = path.suffix.lower()
            if extension in VIDEO_EXTENSIONS:
                kind = "video"
            elif extension in SUBTITLE_EXTENSIONS:
                kind = "subtitle"
            elif extension == ".aria2":
                kind = "control"
            elif extension in {".part", ".tmp"}:
                kind = "partial"
            else:
                continue
            season = None
            episode = None
            if kind == "video":
                hint = _season_hint(path, root)
                key = extract_episode_key(
                    path.name,
                    "",
                    default_season=hint if hint is not None else 1,
                )
                if key is not None:
                    season = key.season
                    episode = key.episode
            entries.append(
                MediaEntry(
                    path=path,
                    kind=kind,
                    extension=extension,
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    season=season,
                    episode=episode,
                )
            )
    return entries


def _subtitle_matches(subtitle: MediaEntry, videos: list[MediaEntry]) -> bool:
    stem = subtitle.path.stem
    for suffix in (".zh", ".zh-CN", ".chs", ".cht", ".en"):
        if stem.lower().endswith(suffix.lower()):
            stem = stem[: -len(suffix)]
            break
    return any(
        video.path.parent == subtitle.path.parent
        and video.path.stem == stem
        for video in videos
    )


def health(entries: list[MediaEntry], *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    videos = [entry for entry in entries if entry.kind == "video"]
    subtitles = [entry for entry in entries if entry.kind == "subtitle"]
    return {
        "totalEntries": len(entries),
        "healthyMedia": sum(entry.size > 0 for entry in videos),
        "zeroByteMedia": sum(entry.size == 0 for entry in videos),
        "orphanSubtitles": sum(
            not _subtitle_matches(subtitle, videos) for subtitle in subtitles
        ),
        "controlFiles": sum(entry.kind == "control" for entry in entries),
        "stalePartFiles": sum(
            entry.kind == "partial"
            and now - entry.modified_at >= 7 * 86400
            for entry in entries
        ),
    }

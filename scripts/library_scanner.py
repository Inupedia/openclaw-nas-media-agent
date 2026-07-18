import os
import re
import time
from dataclasses import dataclass
from pathlib import Path


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}
SKIP_DIRECTORIES = {".incoming", "#recycle"}
EPISODE = re.compile(r"(?i)S(\d{1,2})E(\d{1,3})")


@dataclass(frozen=True)
class MediaEntry:
    path: Path
    kind: str
    extension: str
    size: int
    modified_at: float
    season: int | None = None
    episode: int | None = None


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
            match = EPISODE.search(path.name)
            entries.append(
                MediaEntry(
                    path=path,
                    kind=kind,
                    extension=extension,
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    season=int(match.group(1)) if match else None,
                    episode=int(match.group(2)) if match else None,
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

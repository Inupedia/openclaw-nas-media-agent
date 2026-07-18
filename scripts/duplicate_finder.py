import hashlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from library_scanner import MediaEntry


CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class DuplicateGroup:
    paths: tuple[Path, ...]
    size_each: int
    reclaimable_bytes: int
    sha256: str


def _edge_hash(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        digest.update(file.read(CHUNK_SIZE))
        if size > CHUNK_SIZE:
            file.seek(max(0, size - CHUNK_SIZE))
            digest.update(file.read(CHUNK_SIZE))
    return digest.hexdigest()


def _full_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def find_duplicates(entries: list[MediaEntry]) -> list[DuplicateGroup]:
    by_size = defaultdict(list)
    for entry in entries:
        if entry.kind == "video" and entry.size > 0:
            by_size[entry.size].append(entry)

    results = []
    for size, size_group in by_size.items():
        if len(size_group) < 2:
            continue
        by_edge = defaultdict(list)
        for entry in size_group:
            by_edge[_edge_hash(entry.path, size)].append(entry)
        for edge_group in by_edge.values():
            if len(edge_group) < 2:
                continue
            by_full = defaultdict(list)
            for entry in edge_group:
                by_full[_full_hash(entry.path)].append(entry)
            for digest, full_group in by_full.items():
                if len(full_group) > 1:
                    paths = tuple(sorted((entry.path for entry in full_group)))
                    results.append(
                        DuplicateGroup(
                            paths=paths,
                            size_each=size,
                            reclaimable_bytes=size * (len(paths) - 1),
                            sha256=digest,
                        )
                    )
    return sorted(results, key=lambda group: group.reclaimable_bytes, reverse=True)

import re
from dataclasses import dataclass, field

from media_namer import normalize_title


VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts")
ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z")
SUBTITLE_EXTENSIONS = (".ass", ".ssa", ".srt", ".vtt")


@dataclass(frozen=True)
class Classification:
    media_type: str
    title: str
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateScore:
    score: int
    reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)


def _files(share: dict) -> list[dict]:
    return list(share.get("list", []) or [])


def _year(text: str) -> int | None:
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    return int(match.group(1)) if match else None


def _episode_markers(text: str) -> tuple[int | None, list[int]]:
    pairs = re.findall(r"(?i)\bS(\d{1,2})E(\d{1,3})\b", text)
    if pairs:
        seasons = [int(season) for season, _ in pairs]
        episodes = sorted({int(episode) for _, episode in pairs})
        return seasons[0], episodes

    episode_matches = re.findall(
        r"(?i)(?:\bEP?\s*|第\s*)(\d{1,3})(?:\s*集)?",
        text,
    )
    if episode_matches:
        season_match = re.search(r"第\s*(\d+)\s*季", text)
        season = int(season_match.group(1)) if season_match else 1
        return season, sorted({int(value) for value in episode_matches})
    return None, []


def classify(query: str, share: dict) -> Classification:
    names = [
        str(item.get("file_name", ""))
        for item in _files(share)
        if item.get("file_name")
    ]
    combined = " ".join([query, str(share.get("share", {}).get("title", "")), *names])
    season, episodes = _episode_markers(combined)
    reasons = []

    lower = combined.lower()
    if any(token in lower for token in ("动画", "动漫", "anime", "ova")):
        media_type = "anime"
        reasons.append("anime_hint")
    elif any(token in lower for token in ("纪录片", "documentary")):
        media_type = "documentary"
        reasons.append("documentary_hint")
    elif any(token in lower for token in ("综艺", "真人秀", "show")):
        media_type = "show"
        reasons.append("show_hint")
    elif episodes:
        media_type = "drama"
        reasons.append("episode_marker")
    else:
        video_files = [
            name for name in names if name.lower().endswith(VIDEO_EXTENSIONS)
        ]
        if len(video_files) == 1:
            media_type = "movie"
            reasons.append("single_video")
        else:
            media_type = "other"
            reasons.append("ambiguous_collection")

    video_count = sum(
        name.lower().endswith(VIDEO_EXTENSIONS) for name in names
    )
    collection_hint = any(
        token in lower for token in ("合集", "花絮", "多个版本", "多版本")
    )
    if (
        (video_count > 1 or collection_hint)
        and not episodes
        and media_type in {"movie", "other"}
    ):
        if "ambiguous_collection" not in reasons:
            reasons.append("ambiguous_collection")

    confidence = 0.95 if episodes else 0.9 if media_type == "movie" else 0.88
    if "ambiguous_collection" in reasons:
        confidence = 0.5

    title = normalize_title(query)
    if not title:
        title = normalize_title(str(share.get("share", {}).get("title", "")))
    return Classification(
        media_type=media_type,
        title=title,
        year=_year(combined),
        season=season,
        episodes=episodes,
        confidence=confidence,
        reasons=reasons,
    )


def score_candidate(
    query: str,
    candidate: dict,
    share: dict,
) -> CandidateScore:
    query_title = normalize_title(query).casefold()
    candidate_text = " ".join(
        [
            str(candidate.get("taskname", "")),
            str(candidate.get("content", "")),
            str(share.get("share", {}).get("title", "")),
        ]
    )
    candidate_title = normalize_title(candidate_text).casefold()
    names = [
        str(item.get("file_name", ""))
        for item in _files(share)
        if item.get("file_name")
    ]
    all_text = " ".join([candidate_text, *names])
    lower = all_text.lower()
    score = 0
    reasons = []
    penalties = []

    if query_title and query_title in candidate_title:
        score += 35
        reasons.append("title")
    query_year = _year(query)
    if query_year and str(query_year) in all_text:
        score += 15
        reasons.append("year")

    query_season, query_episodes = _episode_markers(query)
    candidate_season, candidate_episodes = _episode_markers(all_text)
    if query_episodes and set(query_episodes).issubset(candidate_episodes):
        if query_season is None or query_season == candidate_season:
            score += 20
            reasons.append("episode_coverage")

    video_files = [
        name for name in names if name.lower().endswith(VIDEO_EXTENSIONS)
    ]
    if video_files:
        score += 10
        reasons.append("direct_video")
    if "1080p" in lower:
        score += 8
        reasons.append("1080p")
    if any(token in lower for token in ("hevc", "h265", "h.265")):
        score += 4
        reasons.append("hevc")
    if any(name.lower().endswith(SUBTITLE_EXTENSIONS) for name in names) or any(
        token in lower for token in ("中文字幕", "中字", "zh-cn", "chs")
    ):
        score += 4
        reasons.append("chinese_subtitle")
    if any(token in lower for token in ("全集", "完结", "全季")):
        score += 4
        reasons.append("complete")

    if any(token in lower for token in (" cam", "cam.", "枪版", " telesync")):
        score -= 40
        penalties.append("cam")
    if names and all(name.lower().endswith(ARCHIVE_EXTENSIONS) for name in names):
        score -= 20
        penalties.append("archive_only")

    return CandidateScore(
        score=max(0, min(100, score)),
        reasons=reasons,
        penalties=penalties,
    )

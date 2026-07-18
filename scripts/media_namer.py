import re
import unicodedata
from pathlib import PurePosixPath


TECHNICAL_TOKENS = re.compile(
    r"(?ix)"
    r"\b(?:"
    r"S\d{1,2}E\d{1,3}(?:-E?\d{1,3})?|"
    r"EP?\d{1,3}|"
    r"2160P|1080P|720P|4K|"
    r"HEVC|H\.?265|H\.?264|AVC|WEB-?DL|WEBRIP|BLURAY|"
    r"AAC|DTS(?:-HD)?|TRUEHD|ATMOS|"
    r"ZH-CN|ZH-TW|CHS|CHT|"
    r"MKV|MP4|AVI|MOV|M4V|ASS|SSA|SRT|ZIP|RAR|7Z"
    r")\b"
)
YEAR_TOKEN = re.compile(r"\b(?:19|20)\d{2}\b")
URL_TOKEN = re.compile(
    r"(?i)(?:https?://\S+|www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?)"
)
AD_BRACKET = re.compile(r"【[^】]*(?:网|群|资源|发布|下载)[^】]*】")
EPISODE_CN = re.compile(r"第\s*\d+\s*集")
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = PurePosixPath(text.replace("\\", "/")).name
    text = AD_BRACKET.sub(" ", text)
    text = URL_TOKEN.sub(" ", text)
    text = re.sub(r"\.[A-Za-z0-9]{2,4}$", " ", text)
    text = text.replace("_", " ").replace(".", " ")
    text = TECHNICAL_TOKENS.sub(" ", text)
    text = YEAR_TOKEN.sub(" ", text)
    text = EPISODE_CN.sub(" ", text)
    text = re.sub(r"(?i)\b(?:CAM|TS|枪版|中字|字幕)\b", " ", text)
    text = ILLEGAL.sub(" ", text)
    text = re.sub(r"[\s\-–—]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    return text


def build_paths(classification, routing: dict, task_id: str) -> dict:
    route = routing[classification.media_type]
    directory = classification.title
    if classification.year:
        directory = f"{directory} ({classification.year})"
    return {
        "cloud_path": f"{route['cloud_prefix'].rstrip('/')}/{directory}",
        "staging_path": f"{route['staging_root'].rstrip('/')}/{task_id}",
        "final_path": f"{route['final_root'].rstrip('/')}/{directory}",
        "aria2_save_path": f"{route['aria2_prefix'].rstrip('/')}/{task_id}",
    }


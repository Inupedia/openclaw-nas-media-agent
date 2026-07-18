import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from media_classifier import classify, score_candidate
from media_namer import build_paths, normalize_title


def share(*names):
    return {
        "share": {"title": names[0] if names else ""},
        "list": [
            {
                "file_name": name,
                "dir": False,
                "size": 1_000_000_000,
            }
            for name in names
        ],
    }


class ClassificationTests(unittest.TestCase):
    def test_single_video_without_episode_markers_is_movie(self):
        result = classify(
            "沙丘2 2024",
            share("【某某网】沙丘2.2024.1080P.HEVC.mkv"),
        )

        self.assertEqual(result.media_type, "movie")
        self.assertEqual(result.year, 2024)
        self.assertGreaterEqual(result.confidence, 0.85)

    def test_sxxexx_is_tv(self):
        result = classify(
            "黑镜 第二季",
            share("Black.Mirror.S02E03.1080p.mkv"),
        )

        self.assertEqual(result.media_type, "tv")
        self.assertEqual(result.season, 2)
        self.assertEqual(result.episodes, [3])

    def test_chinese_episode_and_anime_hint_is_anime(self):
        result = classify(
            "葬送的芙莉莲 动画",
            share("葬送的芙莉莲 第12集.mp4"),
        )

        self.assertEqual(result.media_type, "anime")
        self.assertEqual(result.season, 1)
        self.assertEqual(result.episodes, [12])

    def test_special_episode_uses_season_zero(self):
        result = classify(
            "某剧 特别篇",
            share("某剧.S00E01.特别篇.mkv"),
        )

        self.assertEqual(result.media_type, "tv")
        self.assertEqual(result.season, 0)
        self.assertEqual(result.episodes, [1])

    def test_ambiguous_collection_is_low_confidence(self):
        result = classify(
            "某作品",
            share("某作品合集", "某作品花絮.mp4"),
        )

        self.assertLess(result.confidence, 0.85)
        self.assertIn("ambiguous_collection", result.reasons)


class NamingTests(unittest.TestCase):
    def setUp(self):
        with open(
            Path(__file__).resolve().parents[1] / "config" / "routing.json",
            encoding="utf-8",
        ) as routing_file:
            self.routing = json.load(routing_file)

    def test_title_normalization_removes_ads_and_technical_tags(self):
        normalized = normalize_title(
            "【XX电影网】黑镜.S02E03.1080P.HEVC.www.example.com.mkv"
        )

        self.assertEqual(normalized, "黑镜")

    def test_movie_paths_follow_volume_three_rule(self):
        classification = classify(
            "沙丘2 2024",
            share("沙丘2.2024.1080P.mkv"),
        )

        paths = build_paths(classification, self.routing, "rd-123")

        self.assertEqual(
            paths["staging_path"],
            "/volume3/临时影视/.incoming/rd-123",
        )
        self.assertEqual(
            paths["final_path"],
            "/volume3/临时影视/Movie/沙丘2 (2024)",
        )
        self.assertEqual(
            paths["aria2_save_path"],
            "临时影视/.incoming/rd-123",
        )


class CandidateScoreTests(unittest.TestCase):
    def test_good_1080p_hevc_candidate_scores_above_threshold(self):
        candidate = {
            "taskname": "沙丘2 2024",
            "content": "沙丘2 2024 1080P HEVC 中文字幕",
        }
        details = share("沙丘2.2024.1080P.HEVC.mkv", "沙丘2.zh-CN.ass")

        result = score_candidate("沙丘2 2024", candidate, details)

        self.assertGreaterEqual(result.score, 70)
        self.assertNotIn("cam", result.penalties)

    def test_cam_and_archive_only_are_penalized(self):
        candidate = {
            "taskname": "沙丘2 2024 CAM",
            "content": "枪版 压缩包",
        }
        details = share("沙丘2.2024.CAM.zip")

        result = score_candidate("沙丘2 2024", candidate, details)

        self.assertLess(result.score, 70)
        self.assertIn("cam", result.penalties)
        self.assertIn("archive_only", result.penalties)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from media_classifier import classify, extract_candidate_spec, score_candidate
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

    def test_sxxexx_is_drama(self):
        result = classify(
            "黑镜 第二季",
            share("Black.Mirror.S02E03.1080p.mkv"),
        )

        self.assertEqual(result.media_type, "drama")
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

    def test_anime_release_group_beats_episode_drama(self):
        result = classify(
            "幼女战记",
            share(
                "[HiveWeb] Youjo Senki S02E01 1080p WEB-DL HEVC AAC CHS.mkv",
                "[HiveWeb] Youjo Senki S02E02 1080p WEB-DL HEVC AAC CHS.mkv",
            ),
        )

        self.assertEqual(result.media_type, "anime")
        self.assertIn("anime_release_group", result.reasons)
        self.assertEqual(result.season, 2)
        self.assertEqual(result.episodes, [1, 2])

    def test_preferred_media_type_overrides_auto(self):
        result = classify(
            "黑镜 第二季",
            share("Black.Mirror.S02E03.1080p.mkv"),
            preferred_type="anime",
        )

        self.assertEqual(result.media_type, "anime")
        self.assertIn("preferred_media_type", result.reasons)

    def test_special_episode_uses_season_zero(self):
        result = classify(
            "某剧 特别篇",
            share("某剧.S00E01.特别篇.mkv"),
        )

        self.assertEqual(result.media_type, "drama")
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

    def test_drama_uses_the_existing_tv_route(self):
        for key in (
            "cloud_prefix",
            "aria2_prefix",
            "staging_root",
            "final_root",
        ):
            self.assertEqual(
                self.routing["drama"][key],
                self.routing["tv"][key],
            )

    def test_title_normalization_removes_ads_and_technical_tags(self):
        normalized = normalize_title(
            "【XX电影网】黑镜.S02E03.1080P.HEVC.www.example.com.mkv"
        )

        self.assertEqual(normalized, "黑镜")

    def test_movie_uses_unified_download_area_then_volume_three_final(self):
        classification = classify(
            "沙丘2 2024",
            share("沙丘2.2024.1080P.mkv"),
        )

        paths = build_paths(classification, self.routing, "rd-123")

        self.assertEqual(
            paths["staging_path"],
            "/volume2/downloads/.incoming/rd-123",
        )
        self.assertEqual(
            paths["final_path"],
            "/volume3/临时影视/Movie/沙丘2 (2024)",
        )
        self.assertEqual(
            paths["aria2_save_path"],
            "downloads/.incoming/rd-123",
        )
        self.assertEqual(paths["cloud_path"], "/OpenClaw/Movies/rd-123")


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


class CandidateSpecificationTests(unittest.TestCase):
    def test_extracts_quality_size_and_bilingual_external_subtitles(self):
        details = {
            "list": [
                {
                    "file_name": (
                        "Example.S01E01.2160p.DV.HDR.HEVC.Atmos.mkv"
                    ),
                    "size": 8_000_000_000,
                },
                {
                    "file_name": "Example.S01E01.chs-eng.ass",
                    "size": 50_000,
                },
            ]
        }

        result = extract_candidate_spec(details)

        self.assertEqual(result["resolution"], "2160p")
        self.assertEqual(result["dynamicRange"], "dolby_vision")
        self.assertEqual(result["videoCodec"], "hevc")
        self.assertEqual(result["audioFormat"], "atmos")
        self.assertEqual(result["subtitleClass"], "zh_en")
        self.assertEqual(result["subtitleForm"], "external")
        self.assertEqual(result["totalBytes"], 8_000_050_000)
        self.assertEqual(result["fileCount"], 2)
        self.assertEqual(
            result["episodeCoverage"],
            [{"season": 1, "episode": 1}],
        )

    def test_unknown_metadata_is_reported_not_invented(self):
        result = extract_candidate_spec(
            {"list": [{"file_name": "video.mkv", "size": 100}]}
        )

        self.assertEqual(result["resolution"], "unknown")
        self.assertEqual(result["dynamicRange"], "unknown")
        self.assertEqual(result["subtitleClass"], "unknown")
        self.assertEqual(result["subtitleForm"], "unknown")

    def test_share_meta_totals_and_sample_name_drive_spec(self):
        result = extract_candidate_spec(
            {
                "share": {
                    "title": "犯罪心理S01~S16",
                    "size": 202848184964,
                    "file_only_num": 327,
                },
                "list": [
                    {
                        "file_name": "犯罪心理.S01E01.1080P.mkv",
                        "dir": False,
                        "size": 1,
                    }
                ],
            }
        )

        self.assertEqual(result["resolution"], "1080p")
        self.assertEqual(result["totalBytes"], 202848184964)
        self.assertEqual(result["videoFileCount"], 327)


if __name__ == "__main__":
    unittest.main()

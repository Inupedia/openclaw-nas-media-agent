import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from planner import DownloadPlanner, PlanningError
from state_store import StateStore


MOVIE_SHARE = {
    "share": {"title": "沙丘2 2024"},
    "list": [
        {
            "file_name": "沙丘2.2024.1080P.HEVC.mkv",
            "dir": False,
            "size": 4_000_000_000,
        },
        {
            "file_name": "沙丘2.2024.zh-CN.ass",
            "dir": False,
            "size": 100_000,
        },
    ],
}


class FakeQas:
    def __init__(self, candidates=None, shares=None):
        self.candidates = candidates or []
        self.shares = shares or {}
        self.added = []
        self.ran = []

    def search(self, query, deep=True):
        return self.candidates

    def get_share(self, url, show_all=True):
        return self.shares[url]

    def add_task(self, task):
        self.added.append(task)
        return {"message": "added"}

    def run_task(self, task):
        self.ran.append(task)
        return {"submitted": True}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp_dir.name) / "state.db")
        with open(
            Path(__file__).resolve().parents[1] / "config" / "routing.json",
            encoding="utf-8",
        ) as routing_file:
            self.routing = json.load(routing_file)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def make_planner(self, qas, path_exists=lambda _: False):
        return DownloadPlanner(
            qas=qas,
            store=self.store,
            routing=self.routing,
            path_exists=path_exists,
        )

    def test_direct_movie_link_builds_volume_three_task(self):
        url = "https://pan.quark.cn/s/movie"
        planner = self.make_planner(FakeQas(shares={url: MOVIE_SHARE}))

        result = planner.plan(url, query_hint="沙丘2 2024")

        task = result["task"]
        self.assertTrue(result["planId"].startswith("plan-"))
        self.assertEqual(result["classification"]["mediaType"], "movie")
        self.assertEqual(
            result["stagingPath"].rsplit("/", 1)[0],
            "/volume3/临时影视/.incoming",
        )
        self.assertEqual(task["savepath"], "/OpenClaw/Movies/沙丘2 (2024)")
        self.assertEqual(
            task["addition"]["aria2"]["save_path"].split("/")[0],
            "临时影视",
        )
        self.assertTrue(task["addition"]["aria2"]["auto_download"])

    def test_search_selects_clear_winner(self):
        good_url = "https://pan.quark.cn/s/good"
        bad_url = "https://pan.quark.cn/s/bad"
        qas = FakeQas(
            candidates=[
                {
                    "taskname": "沙丘2 2024 1080P",
                    "content": "HEVC 中文字幕",
                    "shareurl": good_url,
                },
                {
                    "taskname": "沙丘2 2024 CAM",
                    "content": "枪版 压缩包",
                    "shareurl": bad_url,
                },
            ],
            shares={
                good_url: MOVIE_SHARE,
                bad_url: {
                    "share": {"title": "沙丘2 CAM"},
                    "list": [
                        {
                            "file_name": "沙丘2.CAM.zip",
                            "dir": False,
                            "size": 10,
                        }
                    ],
                },
            },
        )

        result = self.make_planner(qas).plan("沙丘2 2024")

        self.assertEqual(result["selected"]["shareurl"], good_url)
        self.assertGreaterEqual(result["selected"]["score"], 70)
        self.assertFalse(result["requiresConfirmation"])

    def test_search_never_selects_archive_only_candidate(self):
        archive_url = "https://pan.quark.cn/s/archive"
        video_url = "https://pan.quark.cn/s/video"
        qas = FakeQas(
            candidates=[
                {
                    "taskname": "庆余年2 全36集 1080P",
                    "content": "",
                    "shareurl": archive_url,
                },
                {
                    "taskname": "庆余年2 S02E01 1080P",
                    "content": "",
                    "shareurl": video_url,
                },
            ],
            shares={
                archive_url: {
                    "share": {"title": "庆余年2 全36集"},
                    "list": [
                        {"file_name": f"{episode:02d}.zip", "dir": False}
                        for episode in range(1, 37)
                    ],
                },
                video_url: {
                    "share": {"title": "庆余年2 S02E01"},
                    "list": [
                        {
                            "file_name": "庆余年2.S02E01.1080P.mkv",
                            "dir": False,
                        }
                    ],
                },
            },
        )

        result = self.make_planner(qas).plan("庆余年2 S02E01")

        self.assertEqual(result["selected"]["shareurl"], video_url)

    def test_search_rejects_when_every_candidate_is_archive_only(self):
        archive_url = "https://pan.quark.cn/s/archive"
        qas = FakeQas(
            candidates=[
                {
                    "taskname": "庆余年2 全36集",
                    "content": "",
                    "shareurl": archive_url,
                }
            ],
            shares={
                archive_url: {
                    "share": {"title": "庆余年2 全36集"},
                    "list": [
                        {"file_name": "01.zip", "dir": False},
                        {"file_name": "02.zip", "dir": False},
                    ],
                }
            },
        )

        with self.assertRaisesRegex(PlanningError, "no valid"):
            self.make_planner(qas).plan("庆余年2")

    def test_close_candidate_scores_require_confirmation(self):
        urls = ["https://pan.quark.cn/s/a", "https://pan.quark.cn/s/b"]
        candidates = [
            {
                "taskname": "沙丘2 2024 1080P",
                "content": "HEVC 中文字幕",
                "shareurl": url,
            }
            for url in urls
        ]
        qas = FakeQas(
            candidates=candidates,
            shares={url: MOVIE_SHARE for url in urls},
        )

        result = self.make_planner(qas).plan("沙丘2 2024")

        self.assertTrue(result["requiresConfirmation"])
        self.assertIn("candidate_scores_too_close", result["warnings"])

    def test_existing_final_path_requires_confirmation(self):
        url = "https://pan.quark.cn/s/movie"
        planner = self.make_planner(
            FakeQas(shares={url: MOVIE_SHARE}),
            path_exists=lambda _: True,
        )

        result = planner.plan(url, query_hint="沙丘2 2024")

        self.assertTrue(result["requiresConfirmation"])
        self.assertIn("final_path_exists", result["warnings"])

    def test_movie_execution_runs_once_without_subscription(self):
        url = "https://pan.quark.cn/s/movie"
        qas = FakeQas(shares={url: MOVIE_SHARE})
        planner = self.make_planner(qas)
        plan = planner.plan(url, query_hint="沙丘2 2024")

        result = planner.execute(plan["planId"], confirmed=True)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(len(qas.ran), 1)
        self.assertEqual(qas.added, [])
        with self.assertRaisesRegex(PlanningError, "already consumed"):
            planner.execute(plan["planId"], confirmed=True)

    def test_ongoing_request_adds_subscription_and_runs_immediately(self):
        url = "https://pan.quark.cn/s/show"
        share = {
            "share": {"title": "黑镜 S02E03"},
            "list": [
                {
                    "file_name": "黑镜.S02E03.1080P.mkv",
                    "dir": False,
                    "size": 1_000,
                }
            ],
        }
        qas = FakeQas(shares={url: share})
        planner = self.make_planner(qas)
        plan = planner.plan(
            url,
            query_hint="追更 黑镜 S02E03",
        )

        planner.execute(plan["planId"], confirmed=True)

        self.assertEqual(len(qas.added), 1)
        self.assertEqual(len(qas.ran), 1)


if __name__ == "__main__":
    unittest.main()

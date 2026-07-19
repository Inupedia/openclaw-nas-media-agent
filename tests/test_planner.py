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

    def get_share_preview(self, url, **kwargs):
        return self.get_share(url, show_all=True)

    def add_task(self, task):
        self.added.append(task)
        return {"message": "added"}

    def run_task(self, task):
        self.ran.append(task)
        return {"submitted": True, "events": []}


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
            "/volume2/downloads/.incoming",
        )
        self.assertRegex(task["savepath"], r"^/OpenClaw/Movies/rd-[a-f0-9]{12}$")
        self.assertEqual(
            task["addition"]["aria2"]["save_path"].split("/")[0],
            "downloads",
        )
        self.assertTrue(task["addition"]["aria2"]["auto_download"])
        self.assertIn("zip", task["pattern"])
        self.assertIn("?!", task["pattern"])

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

    def test_missing_confirmation_does_not_consume_plan(self):
        url = "https://pan.quark.cn/s/movie"
        qas = FakeQas(shares={url: MOVIE_SHARE})
        planner = self.make_planner(qas, path_exists=lambda _: True)
        plan = planner.plan(url, query_hint="沙丘2 2024")

        with self.assertRaisesRegex(PlanningError, "requires confirmation"):
            planner.execute(plan["planId"])

        result = planner.execute(plan["planId"], confirmed=True)
        self.assertEqual(result["status"], "submitted")

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

    def test_selected_plan_never_searches_and_hides_internal_task(self):
        class SearchForbiddenQas(FakeQas):
            def search(self, query, deep=True):
                raise AssertionError("planner must not search")

        url = "https://pan.quark.cn/s/show"
        qas = SearchForbiddenQas()
        candidate_id = self.store.create_candidate(
            {
                "query": "Show S01E120",
                "mediaType": "tv",
                "titleKey": "show",
                "shareurl": url,
                "candidate": {"taskname": "Show S01E120"},
                "details": {
                    "share": {"title": "Show S01E120"},
                    "list": [
                        {"file_name": "Show.S01E119.mkv", "dir": False},
                        {"file_name": "Show.S01E120.mkv", "dir": False},
                    ],
                },
                "treeIndex": {
                    "n-ep120": {
                        "nodeId": "n-ep120",
                        "name": "Show.S01E120.mkv",
                        "isDirectory": False,
                        "mediaNames": ["Show.S01E120.mkv"],
                        "path": "Show.S01E120.mkv",
                    }
                },
                "existingEpisodes": [{"season": 1, "episode": 119}],
                "newEpisodes": [{"season": 1, "episode": 120}],
            }
        )

        result = self.make_planner(qas).plan_selected(
            candidate_id,
            node_ids=["n-ep120"],
        )

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(url, serialized)
        self.assertNotIn('"task"', serialized)
        self.assertEqual(
            result["incremental"]["newEpisodes"],
            [{"season": 1, "episode": 120}],
        )
        self.assertRegex(result["cloudPath"], r"/OpenClaw/.+/rd-[a-f0-9]{12}$")
        stored = self.store.read_plan(result["planId"], "download")
        self.assertRegex("Show.S01E120.mkv", stored["task"]["pattern"])
        self.assertNotRegex("Show.S01E119.mkv", stored["task"]["pattern"])

    def test_plan_selected_requires_tree_nodes(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "Show",
                "shareurl": "https://pan.quark.cn/s/show",
                "candidate": {"taskname": "Show"},
                "details": {"share": {"title": "Show"}, "list": []},
            }
        )
        with self.assertRaisesRegex(PlanningError, "selection_required"):
            self.make_planner(FakeQas()).plan_selected(candidate_id)

    def test_plan_selected_expands_directory_node(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "幼女战记2",
                "shareurl": "https://pan.quark.cn/s/show",
                "candidate": {"taskname": "幼女战记2"},
                "details": {"share": {"title": "幼女战记2"}, "list": []},
                "treeIndex": {
                    "n-s02": {
                        "nodeId": "n-s02",
                        "name": "第二季",
                        "isDirectory": True,
                        "mediaNames": [
                            "Youjo.S02E01.mkv",
                            "Youjo.S02E02.mkv",
                        ],
                        "path": "第二季",
                        "fid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    },
                    "n-movie": {
                        "nodeId": "n-movie",
                        "name": "Gekijouban.mkv",
                        "isDirectory": False,
                        "mediaNames": ["Gekijouban.mkv"],
                        "path": "Gekijouban.mkv",
                    },
                },
            }
        )
        result = self.make_planner(FakeQas()).plan_selected(
            candidate_id,
            node_ids=["n-s02"],
        )
        self.assertEqual(
            result["incremental"]["selectedFiles"],
            ["Youjo.S02E01.mkv", "Youjo.S02E02.mkv"],
        )
        self.assertNotIn(
            "Gekijouban",
            json.dumps(result, ensure_ascii=False),
        )
        stored = self.store.read_plan(result["planId"], "download")
        self.assertEqual(
            stored["task"]["shareurl"],
            "https://pan.quark.cn/s/show#/list/share/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )

    def test_plan_selected_file_node_deeplinks_parent_dir(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "幼女战记2",
                "shareurl": "https://pan.quark.cn/s/abc123",
                "candidate": {"taskname": "幼女战记2"},
                "details": {"share": {"title": "幼女战记2"}, "list": []},
                "treeIndex": {
                    "n-dir": {
                        "nodeId": "n-dir",
                        "name": "2026 S02",
                        "isDirectory": True,
                        "mediaNames": ["ep01.mp4", "ep02.mp4"],
                        "fid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    },
                    "n-ep02": {
                        "nodeId": "n-ep02",
                        "name": "ep02.mp4",
                        "isDirectory": False,
                        "parentId": "n-dir",
                        "mediaNames": ["ep02.mp4"],
                        "fid": "cccccccccccccccccccccccccccccccc",
                    },
                },
            }
        )
        result = self.make_planner(FakeQas()).plan_selected(
            candidate_id,
            node_ids=["n-ep02"],
        )
        stored = self.store.read_plan(result["planId"], "download")
        self.assertEqual(
            stored["task"]["shareurl"],
            "https://pan.quark.cn/s/abc123#/list/share/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        self.assertIn(r"ep02\.mp4", stored["task"]["pattern"])
        self.assertNotIn(r"ep01\.mp4", stored["task"]["pattern"])

    def test_plan_selected_hiveweb_files_classify_as_anime(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "幼女战记",
                "shareurl": "https://pan.quark.cn/s/abc123",
                "candidate": {"taskname": "幼女战记"},
                "details": {"share": {"title": "幼女战记"}, "list": []},
                "treeIndex": {
                    "n-s02": {
                        "nodeId": "n-s02",
                        "name": "S02",
                        "isDirectory": True,
                        "mediaNames": [
                            "[HiveWeb] Youjo Senki S02E01.mkv",
                            "[HiveWeb] Youjo Senki S02E02.mkv",
                        ],
                        "fid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    },
                },
            }
        )
        result = self.make_planner(FakeQas()).plan_selected(
            candidate_id,
            node_ids=["n-s02"],
        )
        self.assertEqual(result["classification"]["mediaType"], "anime")
        self.assertIn("Anime", result["finalPath"])
        self.assertIn("confirm_media_type", result["warnings"])

    def test_plan_selected_media_type_override(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "幼女战记",
                "shareurl": "https://pan.quark.cn/s/abc123",
                "candidate": {"taskname": "幼女战记"},
                "details": {"share": {"title": "幼女战记"}, "list": []},
                "treeIndex": {
                    "n-s02": {
                        "nodeId": "n-s02",
                        "name": "S02",
                        "isDirectory": True,
                        "mediaNames": ["Show.S02E01.mkv"],
                        "fid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    },
                },
            }
        )
        result = self.make_planner(FakeQas()).plan_selected(
            candidate_id,
            node_ids=["n-s02"],
            preferred_media_type="anime",
        )
        self.assertEqual(result["classification"]["mediaType"], "anime")
        self.assertNotIn("confirm_media_type", result["warnings"])

    def test_selected_plan_execution_returns_no_raw_qas_events(self):
        url = "https://pan.quark.cn/s/movie"
        qas = FakeQas()
        candidate_id = self.store.create_candidate(
            {
                "query": "沙丘2 2024",
                "mediaType": "movie",
                "shareurl": url,
                "candidate": {"taskname": "沙丘2 2024"},
                "details": MOVIE_SHARE,
                "treeIndex": {
                    "n-movie": {
                        "nodeId": "n-movie",
                        "name": "沙丘2.2024.1080P.HEVC.mkv",
                        "isDirectory": False,
                        "mediaNames": [
                            "沙丘2.2024.1080P.HEVC.mkv",
                            "沙丘2.2024.zh-CN.ass",
                        ],
                        "path": "沙丘2.2024.1080P.HEVC.mkv",
                    }
                },
            }
        )
        planner = self.make_planner(qas)
        plan = planner.plan_selected(candidate_id, node_ids=["n-movie"])

        result = planner.execute(plan["planId"], confirmed=True)

        self.assertEqual(result["status"], "submitted")
        self.assertNotIn("qas", result)
        self.assertEqual(len(qas.ran), 1)

    def test_plan_selected_splits_multi_season_folders_into_transfer_jobs(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "开玩笑",
                "shareurl": "https://pan.quark.cn/s/abc123",
                "candidate": {"taskname": "开玩笑"},
                "details": {"share": {"title": "开玩笑"}, "list": []},
                "treeIndex": {
                    "n-s01": {
                        "nodeId": "n-s01",
                        "name": "第一季",
                        "isDirectory": True,
                        "mediaNames": ["开玩笑 - S01E01 - 第1集.mkv"],
                        "fid": "11111111111111111111111111111111",
                    },
                    "n-s02": {
                        "nodeId": "n-s02",
                        "name": "第二季",
                        "isDirectory": True,
                        "mediaNames": ["开玩笑 - S02E01 - 第1集.mkv"],
                        "fid": "22222222222222222222222222222222",
                    },
                },
            }
        )
        result = self.make_planner(FakeQas()).plan_selected(
            candidate_id,
            node_ids=["n-s01", "n-s02"],
        )
        self.assertEqual(result["transferJobCount"], 2)
        stored = self.store.read_plan(result["planId"], "download")
        jobs = stored["transferTasks"]
        self.assertEqual(len(jobs), 2)
        urls = {job["shareurl"] for job in jobs}
        self.assertIn(
            "https://pan.quark.cn/s/abc123#/list/share/11111111111111111111111111111111",
            urls,
        )
        self.assertIn(
            "https://pan.quark.cn/s/abc123#/list/share/22222222222222222222222222222222",
            urls,
        )
        # Must NOT fall back to bare root share for either job.
        self.assertNotIn("https://pan.quark.cn/s/abc123", urls)

    def test_execute_runs_all_transfer_jobs(self):
        qas = FakeQas()
        candidate_id = self.store.create_candidate(
            {
                "query": "开玩笑",
                "shareurl": "https://pan.quark.cn/s/abc123",
                "candidate": {"taskname": "开玩笑"},
                "details": {"share": {"title": "开玩笑"}, "list": []},
                "treeIndex": {
                    "n-s01": {
                        "nodeId": "n-s01",
                        "name": "第一季",
                        "isDirectory": True,
                        "mediaNames": ["a.mkv"],
                        "fid": "11111111111111111111111111111111",
                    },
                    "n-s02": {
                        "nodeId": "n-s02",
                        "name": "第二季",
                        "isDirectory": True,
                        "mediaNames": ["b.mkv"],
                        "fid": "22222222222222222222222222222222",
                    },
                },
            }
        )
        planner = self.make_planner(qas)
        plan = planner.plan_selected(candidate_id, node_ids=["n-s01", "n-s02"])
        result = planner.execute(plan["planId"], confirmed=True)
        self.assertEqual(result["transferJobCount"], 2)
        self.assertEqual(len(qas.ran), 2)

    def test_execute_fails_when_qas_reports_no_transfer(self):
        class NoTransferQas(FakeQas):
            def run_task(self, task):
                self.ran.append(task)
                return {
                    "submitted": True,
                    "events": ["任务结束：没有新的转存任务"],
                }

        qas = NoTransferQas()
        candidate_id = self.store.create_candidate(
            {
                "query": "开玩笑",
                "shareurl": "https://pan.quark.cn/s/abc123",
                "candidate": {"taskname": "开玩笑"},
                "details": {"share": {"title": "开玩笑"}, "list": []},
                "treeIndex": {
                    "n-s01": {
                        "nodeId": "n-s01",
                        "name": "第一季",
                        "isDirectory": True,
                        "mediaNames": ["a.mkv"],
                        "fid": "11111111111111111111111111111111",
                    },
                },
            }
        )
        planner = self.make_planner(qas)
        plan = planner.plan_selected(candidate_id, node_ids=["n-s01"])
        with self.assertRaisesRegex(PlanningError, "transferred nothing"):
            planner.execute(plan["planId"], confirmed=True)

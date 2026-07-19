import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from media_service import MediaService
from pansou_client import PanSouError
from jiaofu_client import JiaofuError
from state_store import StateStore


class FakeCatalog:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def lookup(self, query, media_type):
        self.calls.append((query, media_type))
        return dict(self.result)


class RecordingQas:
    def __init__(self, candidates=None, shares=None):
        self.candidates = candidates or []
        self.shares = shares or {}
        self.reads = []
        self.writes = []

    def search(self, query, deep=True):
        self.reads.append(("search", query, deep))
        return list(self.candidates)

    def get_share(self, url, show_all=True):
        self.reads.append(("share", url, show_all))
        result = self.shares[url]
        if isinstance(result, Exception):
            raise result
        return result

    def get_share_preview(self, url, **kwargs):
        self.reads.append(("share_preview", url))
        return self.get_share(url, show_all=True)

    def get_share_tree(self, url, **kwargs):
        self.reads.append(("share_tree", url))
        root = self.get_share(url, show_all=True)
        items = list(root.get("list") or [])
        tree = []
        videos = 0
        files = 0
        directories = 0
        for item in items:
            is_dir = bool(item.get("dir"))
            name = str(item.get("file_name") or "")
            node = {
                "name": name,
                "isDirectory": is_dir,
                "size": int(item.get("size") or 0),
                "fid": item.get("fid"),
                "path": name,
                "depth": 0,
                "children": [],
            }
            tree.append(node)
            if is_dir:
                directories += 1
            else:
                files += 1
                if name.casefold().endswith((".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts")):
                    videos += 1
        return {
            "share": root.get("share") or {},
            "tree": tree,
            "stats": {
                "directories": directories,
                "files": files,
                "videos": videos,
                "truncated": False,
                "requests": 1,
                "nodes": len(tree),
            },
        }

    def add_task(self, task):
        self.writes.append(("add", task))

    def run_task(self, task):
        self.writes.append(("run", task))


class RecordingPanSou:
    def __init__(self, candidates=None, error=None, max_candidates=50):
        self.candidates = candidates or []
        self.error = error
        self.reads = []
        self.max_candidates = max_candidates

    def search(self, query):
        self.reads.append(query)
        if self.error:
            raise self.error
        if isinstance(self.candidates, dict):
            return list(self.candidates.get(query, []))
        return list(self.candidates)


class RecordingJiaofu:
    def __init__(self, candidates=None, error=None, max_candidates=20):
        self.candidates = candidates or []
        self.error = error
        self.reads = []
        self.max_candidates = max_candidates

    def search(self, query):
        self.reads.append(query)
        if self.error:
            raise self.error
        if isinstance(self.candidates, dict):
            return list(self.candidates.get(query, []))
        return list(self.candidates)


class MediaServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "state.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_normal_search_stops_on_local_match_without_remote_call(self):
        local = {
            "found": True,
            "title": "凡人修仙传",
            "mediaType": "anime",
            "episodes": [{"season": 1, "episode": 1}],
            "fileCount": 680,
        }
        qas = RecordingQas()
        pansou = RecordingPanSou()
        service = MediaService(FakeCatalog(local), qas, self.store, pansou)

        result = service.search(
            "搜索《凡人修仙传》动画资源",
            media_type="anime",
        )

        self.assertTrue(result["terminal"])
        self.assertEqual(result["nextAction"], "stop_local_exists")
        self.assertTrue(result["data"]["local"]["found"])
        self.assertEqual(result["data"]["remoteCandidates"], [])
        self.assertEqual(qas.reads, [])
        self.assertEqual(qas.writes, [])
        self.assertEqual(pansou.reads, [])

    def test_both_sources_use_sequel_variants_and_duplicate_is_previewed_once(self):
        url = "https://pan.quark.cn/s/shared-result"
        qas = RecordingQas(
            candidates=[{"taskname": "QAS title", "shareurl": url}],
            shares={
                url: {
                    "share": {"title": "幼女战记 第二季"},
                    "list": [
                        {
                            "file_name": "幼女战记.S02E01.1080p.HEVC.mkv",
                            "size": 2_000,
                        }
                    ],
                }
            },
        )
        pansou = RecordingPanSou(
            [{"taskname": "PanSou title", "shareurl": f"{url}/"}]
        )
        service = MediaService(
            FakeCatalog({"found": False, "queryTitle": "幼女战记2", "matches": []}),
            qas,
            self.store,
            pansou,
        )

        result = service.search("幼女战记2", "anime")

        variants = ["幼女战记2", "幼女战记", "幼女战记 第2季", "幼女战记 S02"]
        self.assertEqual(
            [read[1] for read in qas.reads if read[0] == "search"],
            variants,
        )
        self.assertEqual(pansou.reads, variants)
        self.assertEqual(
            len([read for read in qas.reads if read[0] == "share"]),
            1,
        )
        candidate = result["data"]["remoteCandidates"][0]
        self.assertEqual(candidate["discoverySources"], ["qas", "pansou"])
        self.assertEqual(candidate["title"], "QAS title")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(url, serialized)
        self.assertNotIn("private-pansou", serialized)

    def test_pansou_only_candidate_is_previewed_by_qas(self):
        url = "https://pan.quark.cn/s/pansou-only"
        qas = RecordingQas(
            shares={
                url: {
                    "share": {"title": "Example"},
                    "list": [
                        {
                            "file_name": "Example.S01E01.1080p.chs-eng.mkv",
                            "size": 1_000,
                        }
                    ],
                }
            }
        )
        pansou = RecordingPanSou(
            [{"taskname": "PanSou candidate", "shareurl": url}]
        )

        result = MediaService(
            FakeCatalog({"found": False, "queryTitle": "Example", "matches": []}),
            qas,
            self.store,
            pansou,
        ).search("Example", "drama")

        self.assertEqual(result["data"]["candidateCount"], 1)
        self.assertEqual(
            result["data"]["remoteCandidates"][0]["discoverySources"],
            ["pansou"],
        )
        self.assertIn(("share", url, True), qas.reads)

    def test_pansou_failure_keeps_qas_results_and_adds_safe_warning(self):
        url = "https://pan.quark.cn/s/qas-result"
        qas = RecordingQas(
            candidates=[{"taskname": "QAS title", "shareurl": url}],
            shares={
                url: {
                    "share": {"title": "Example"},
                    "list": [{"file_name": "Example.1080p.mkv", "size": 1_000}],
                }
            },
        )
        pansou = RecordingPanSou(
            error=PanSouError("private-pansou endpoint unavailable")
        )

        result = MediaService(
            FakeCatalog({"found": False, "queryTitle": "Example", "matches": []}),
            qas,
            self.store,
            pansou,
        ).search("Example", "movie")

        self.assertEqual(result["data"]["candidateCount"], 1)
        self.assertEqual(result["data"]["warnings"], ["pansou_unavailable"])
        self.assertEqual(pansou.reads, ["Example"])
        self.assertNotIn("private-pansou", json.dumps(result))

    def test_pansou_limit_applies_across_all_query_variants(self):
        urls = [
            "https://pan.quark.cn/s/first",
            "https://pan.quark.cn/s/second",
            "https://pan.quark.cn/s/third",
        ]
        shares = {
            url: {
                "share": {"title": "幼女战记"},
                "list": [{"file_name": f"幼女战记.S02E0{index}.mkv"}],
            }
            for index, url in enumerate(urls, start=1)
        }
        pansou = RecordingPanSou(
            candidates={
                "幼女战记2": [
                    {"taskname": "first", "shareurl": urls[0]},
                ],
                "幼女战记": [
                    {"taskname": "second", "shareurl": urls[1]},
                ],
                "幼女战记 第2季": [
                    {"taskname": "third", "shareurl": urls[2]},
                ],
            },
            max_candidates=2,
        )

        result = MediaService(
            FakeCatalog({"found": False, "queryTitle": "幼女战记2", "matches": []}),
            RecordingQas(shares=shares),
            self.store,
            pansou,
        ).search("幼女战记2", "anime")

        self.assertEqual(result["data"]["candidateCount"], 2)

    def test_missing_local_title_returns_opaque_remote_candidates(self):
        url = "https://pan.quark.cn/s/secret-share"
        qas = RecordingQas(
            candidates=[
                {
                    "taskname": "沙丘2 2024 1080P",
                    "content": "HEVC 中文字幕",
                    "shareurl": url,
                }
            ],
            shares={
                url: {
                    "share": {"title": "Movie 2024"},
                    "list": [
                        {
                            "file_name": "Movie.2024.1080p.HEVC.mkv",
                            "dir": False,
                            "size": 1_000,
                        }
                    ],
                }
            },
        )
        service = MediaService(
            FakeCatalog({"found": False, "queryTitle": "沙丘2", "matches": []}),
            qas,
            self.store,
        )

        result = service.search("沙丘2", media_type="movie")

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertFalse(result["terminal"])
        self.assertEqual(result["nextAction"], "choose_candidate")
        self.assertEqual(len(result["data"]["remoteCandidates"]), 1)
        candidate = result["data"]["remoteCandidates"][0]
        self.assertTrue(candidate["candidateId"].startswith("candidate-"))
        self.assertEqual(candidate["title"], "沙丘2 2024 1080P")
        self.assertNotIn(url, serialized)
        self.assertNotIn("shareurl", serialized.lower())
        self.assertEqual(qas.writes, [])

    def test_search_lists_every_distinct_spec_without_auto_selection(self):
        urls = [f"https://pan.quark.cn/s/variant-{index}" for index in range(3)]
        qas = RecordingQas(
            candidates=[
                {"taskname": "Example 4K", "shareurl": urls[0]},
                {"taskname": "Example 1080p", "shareurl": urls[1]},
                {"taskname": "Example 1080p small", "shareurl": urls[2]},
            ],
            shares={
                urls[0]: {
                    "share": {"title": "Example"},
                    "list": [
                        {
                            "file_name": (
                                "Example.S01E01.2160p.DV.HEVC.Atmos.mkv"
                            ),
                            "size": 8_000,
                        },
                        {
                            "file_name": "Example.S01E01.chs-eng.ass",
                            "size": 10,
                        },
                    ],
                },
                urls[1]: {
                    "share": {"title": "Example"},
                    "list": [
                        {
                            "file_name": "Example.S01E01.1080p.H264.mkv",
                            "size": 4_000,
                        },
                        {
                            "file_name": "Example.S01E01.chs.ass",
                            "size": 10,
                        },
                    ],
                },
                urls[2]: {
                    "share": {"title": "Example"},
                    "list": [
                        {
                            "file_name": "Example.S01E01.1080p.HEVC.mkv",
                            "size": 2_000,
                        },
                        {
                            "file_name": "Example.S01E01.chs-eng.ass",
                            "size": 10,
                        },
                    ],
                },
            },
        )
        service = MediaService(
            FakeCatalog({"found": False, "queryTitle": "Example", "matches": []}),
            qas,
            self.store,
        )

        result = service.search("Example", "drama")

        self.assertEqual(result["data"]["candidateCount"], 3)
        self.assertEqual(len(result["data"]["specificationGroups"]), 3)
        self.assertEqual(
            result["data"]["remoteCandidates"][0]["specification"][
                "subtitleClass"
            ],
            "zh_en",
        )
        self.assertEqual(
            {
                candidate["specification"]["resolution"]
                for candidate in result["data"]["remoteCandidates"]
            },
            {"2160p", "1080p"},
        )
        self.assertNotIn("selectedCandidateId", result["data"])
        self.assertEqual(
            len([read for read in qas.reads if read[0] == "share"]),
            3,
        )

    def test_preview_reads_share_without_writing_or_returning_share_url(self):
        url = "https://pan.quark.cn/s/secret-share"
        share = {
            "share": {"title": "沙丘2 2024"},
            "list": [
                {
                    "file_name": "沙丘2.2024.1080P.mkv",
                    "dir": False,
                    "size": 1000,
                }
            ],
        }
        qas = RecordingQas(
            candidates=[{"taskname": "沙丘2", "shareurl": url}],
            shares={url: share},
        )
        service = MediaService(
            FakeCatalog({"found": False, "queryTitle": "沙丘2", "matches": []}),
            qas,
            self.store,
        )
        candidate_id = service.search("沙丘2", "movie")["data"][
            "remoteCandidates"
        ][0]["candidateId"]

        result = service.preview(candidate_id)

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["nextAction"], "plan_or_choose")
        self.assertEqual(
            result["data"]["files"],
            [{"name": "沙丘2.2024.1080P.mkv", "isDirectory": False, "size": 1000}],
        )
        self.assertNotIn(url, serialized)
        self.assertEqual(qas.writes, [])
        stored = self.store.get_candidate(candidate_id)
        self.assertEqual(stored["details"]["share"]["title"], "沙丘2 2024")

    def test_tree_assigns_opaque_node_ids_without_share_url(self):
        url = "https://pan.quark.cn/s/secret-share"
        share = {
            "share": {"title": "幼女战记合集"},
            "list": [
                {
                    "file_name": "第二季",
                    "dir": True,
                    "fid": "s02",
                    "size": 0,
                },
                {
                    "file_name": "Youjo.S02E01.mkv",
                    "dir": False,
                    "fid": "e1",
                    "size": 1000,
                },
            ],
        }
        qas = RecordingQas(
            candidates=[{"taskname": "幼女战记2", "shareurl": url}],
            shares={url: share},
        )
        service = MediaService(
            FakeCatalog({"found": False, "queryTitle": "幼女战记2", "matches": []}),
            qas,
            self.store,
        )
        candidate_id = service.search("幼女战记2", "anime")["data"][
            "remoteCandidates"
        ][0]["candidateId"]

        result = service.tree(candidate_id)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["nextAction"], "choose_tree_nodes")
        self.assertNotIn(url, serialized)
        self.assertNotIn('"fid"', serialized)
        tree = result["data"]["tree"]
        self.assertTrue(all(node["nodeId"].startswith("n-") for node in tree))
        stored = self.store.get_candidate(candidate_id)
        self.assertIn("treeIndex", stored)
        file_node = next(
            node for node in tree if not node["isDirectory"]
        )
        selected = service.resolve_tree_selection(
            candidate_id,
            [file_node["nodeId"]],
        )
        self.assertEqual(selected, ["Youjo.S02E01.mkv"])

    def test_update_returns_only_episode_missing_from_nas(self):
        url = "https://pan.quark.cn/s/show"
        qas = RecordingQas(
            candidates=[{"taskname": "凡人修仙传 118-120", "shareurl": url}],
            shares={
                url: {
                    "share": {"title": "凡人修仙传"},
                    "list": [
                        {"file_name": "凡人修仙传.S01E118.mkv", "dir": False},
                        {"file_name": "凡人修仙传.S01E119.mkv", "dir": False},
                        {"file_name": "凡人修仙传.S01E120.mkv", "dir": False},
                    ],
                }
            },
        )
        local = {
            "found": True,
            "title": "凡人修仙传",
            "mediaType": "anime",
            "episodes": [
                {"season": 1, "episode": 118},
                {"season": 1, "episode": 119},
            ],
            "fileCount": 119,
        }
        pansou = RecordingPanSou(
            [{"taskname": "duplicate update", "shareurl": url}]
        )
        service = MediaService(FakeCatalog(local), qas, self.store, pansou)

        result = service.search(
            "检查《凡人修仙传》有没有新集",
            media_type="anime",
            update=True,
        )

        self.assertFalse(result["terminal"])
        self.assertEqual(
            result["data"]["missing"],
            [{"season": 1, "episode": 120}],
        )
        candidate_id = result["data"]["remoteCandidates"][0]["candidateId"]
        stored = self.store.get_candidate(candidate_id)
        self.assertEqual(
            stored["selectedFiles"],
            ["凡人修仙传.S01E120.mkv"],
        )
        self.assertEqual(pansou.reads, ["检查《凡人修仙传》有没有新集"])
        self.assertEqual(
            result["data"]["remoteCandidates"][0]["discoverySources"],
            ["qas", "pansou"],
        )
        self.assertEqual(qas.writes, [])

    def test_update_stops_when_nas_is_already_current(self):
        url = "https://pan.quark.cn/s/show"
        qas = RecordingQas(
            candidates=[{"taskname": "Show E01", "shareurl": url}],
            shares={
                url: {
                    "share": {"title": "Show"},
                    "list": [{"file_name": "Show.S01E001.mkv", "dir": False}],
                }
            },
        )
        local = {
            "found": True,
            "title": "Show",
            "mediaType": "tv",
            "episodes": [{"season": 1, "episode": 1}],
            "fileCount": 1,
        }

        result = MediaService(
            FakeCatalog(local),
            qas,
            self.store,
        ).search("检查《Show》有没有新集", "tv", update=True)

        self.assertTrue(result["terminal"])
        self.assertEqual(result["nextAction"], "already_up_to_date")
        self.assertEqual(result["data"]["remoteCandidates"], [])
        self.assertEqual(qas.writes, [])

    def test_jiaofu_hits_skip_qas_and_pansou_discovery(self):
        url = "https://pan.quark.cn/s/jiaofu-hit"
        jiaofu = RecordingJiaofu(
            [{"taskname": "幼女战记 第二季", "shareurl": url}]
        )
        qas = RecordingQas(
            candidates=[{"taskname": "should not use", "shareurl": "https://pan.quark.cn/s/qas"}],
            shares={
                url: {
                    "share": {"title": "幼女战记 第二季", "size": 1000},
                    "list": [
                        {
                            "file_name": "幼女战记.S02E01.1080p.mkv",
                            "size": 1_000,
                        }
                    ],
                }
            },
        )
        pansou = RecordingPanSou(
            [{"taskname": "should not use", "shareurl": "https://pan.quark.cn/s/pansou"}]
        )

        result = MediaService(
            FakeCatalog({"found": False, "queryTitle": "幼女战记2", "matches": []}),
            qas,
            self.store,
            pansou,
            jiaofu,
        ).search("幼女战记2", "anime")

        self.assertEqual(result["data"]["candidateCount"], 1)
        self.assertEqual(
            result["data"]["remoteCandidates"][0]["discoverySources"],
            ["jiaofu"],
        )
        self.assertEqual(pansou.reads, [])
        self.assertEqual(
            [read[1] for read in qas.reads if read[0] == "search"],
            [],
        )
        self.assertTrue(
            any(read[0] == "share_preview" for read in qas.reads)
            or any(read[0] == "share" for read in qas.reads)
        )

    def test_jiaofu_empty_falls_back_to_qas_pansou(self):
        url = "https://pan.quark.cn/s/fallback"
        jiaofu = RecordingJiaofu([])
        qas = RecordingQas(
            candidates=[{"taskname": "QAS title", "shareurl": url}],
            shares={
                url: {
                    "share": {"title": "Example"},
                    "list": [{"file_name": "Example.1080p.mkv", "size": 1_000}],
                }
            },
        )
        pansou = RecordingPanSou([])

        result = MediaService(
            FakeCatalog({"found": False, "queryTitle": "Example", "matches": []}),
            qas,
            self.store,
            pansou,
            jiaofu,
        ).search("Example", "drama")

        self.assertEqual(
            result["data"]["remoteCandidates"][0]["discoverySources"],
            ["qas"],
        )
        self.assertTrue(jiaofu.reads)
        self.assertTrue(
            any(read[0] == "search" for read in qas.reads)
        )

    def test_jiaofu_failure_falls_back_with_warning(self):
        url = "https://pan.quark.cn/s/qas-result"
        jiaofu = RecordingJiaofu(error=JiaofuError("login required"))
        qas = RecordingQas(
            candidates=[{"taskname": "QAS title", "shareurl": url}],
            shares={
                url: {
                    "share": {"title": "Example"},
                    "list": [{"file_name": "Example.1080p.mkv", "size": 1_000}],
                }
            },
        )
        pansou = RecordingPanSou([])

        result = MediaService(
            FakeCatalog({"found": False, "queryTitle": "Example", "matches": []}),
            qas,
            self.store,
            pansou,
            jiaofu,
        ).search("Example", "drama")

        self.assertEqual(result["data"]["warnings"], ["jiaofu_unavailable"])
        self.assertEqual(
            result["data"]["remoteCandidates"][0]["discoverySources"],
            ["qas"],
        )

    def test_search_imports_quark_share_url_without_remote_keyword_search(self):
        url = "https://pan.quark.cn/s/abcShare01?pwd=xx"
        normalized = "https://pan.quark.cn/s/abcShare01"
        qas = RecordingQas(
            shares={
                normalized: {
                    "share": {
                        "title": "直链分享",
                        "file_only_num": 1,
                        "video_total": 1,
                        "size": 2_000,
                    },
                    "list": [
                        {
                            "file_name": "Show.S01E01.1080p.mkv",
                            "size": 2_000,
                            "dir": False,
                        }
                    ],
                }
            }
        )
        pansou = RecordingPanSou([{"taskname": "should-not-run", "shareurl": normalized}])
        catalog = FakeCatalog({"found": False, "queryTitle": url, "matches": []})

        result = MediaService(catalog, qas, self.store, pansou).search(url, "drama")

        self.assertEqual(result["nextAction"], "tree")
        self.assertTrue(result["data"]["shareImported"])
        self.assertEqual(result["data"]["candidateCount"], 1)
        self.assertEqual(
            result["data"]["remoteCandidates"][0]["discoverySources"],
            ["share_url"],
        )
        self.assertEqual(pansou.reads, [])
        self.assertEqual(catalog.calls, [])
        self.assertTrue(result["data"]["candidateId"])

    def test_tree_accepts_quark_share_url(self):
        url = "https://pan.quark.cn/s/treeShare99"
        qas = RecordingQas(
            shares={
                url: {
                    "share": {"title": "树分享", "video_total": 1},
                    "list": [
                        {
                            "file_name": "Season 1",
                            "dir": True,
                            "include_items": 1,
                            "fid": "d1",
                        },
                        {
                            "file_name": "Episode.01.mkv",
                            "size": 100,
                            "dir": False,
                            "fid": "f1",
                        },
                    ],
                }
            }
        )

        result = MediaService(
            FakeCatalog({"found": False}),
            qas,
            self.store,
            RecordingPanSou([]),
        ).tree(url)

        self.assertEqual(result["nextAction"], "choose_tree_nodes")
        self.assertEqual(result["data"]["title"], "树分享")
        self.assertGreaterEqual(result["data"]["stats"]["files"], 1)
        self.assertTrue(result["data"]["candidateId"].startswith("cand-") or result["data"]["candidateId"])

    def test_open_share_rejects_invalid_url(self):
        from qas_client import ClientError

        service = MediaService(
            FakeCatalog({"found": False}),
            RecordingQas(),
            self.store,
            RecordingPanSou([]),
        )
        with self.assertRaisesRegex(ClientError, "invalid quark share url"):
            service.open_share("https://example.com/not-quark")


if __name__ == "__main__":
    unittest.main()

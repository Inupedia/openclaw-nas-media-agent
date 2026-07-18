import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from media_service import MediaService
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
        return self.shares[url]

    def add_task(self, task):
        self.writes.append(("add", task))

    def run_task(self, task):
        self.writes.append(("run", task))


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
        service = MediaService(FakeCatalog(local), qas, self.store)

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

    def test_missing_local_title_returns_opaque_remote_candidates(self):
        url = "https://pan.quark.cn/s/secret-share"
        qas = RecordingQas(
            candidates=[
                {
                    "taskname": "沙丘2 2024 1080P",
                    "content": "HEVC 中文字幕",
                    "shareurl": url,
                }
            ]
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


if __name__ == "__main__":
    unittest.main()

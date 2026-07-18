import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from state_store import PlanError, StateStore


class MutableClock:
    def __init__(self, now: int = 1_700_000_000):
        self.now = now

    def __call__(self) -> int:
        return self.now


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = MutableClock()
        self.store = StateStore(
            Path(self.temp_dir.name) / "state.db",
            clock=self.clock,
        )

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_plan_is_single_use(self):
        plan_id = self.store.create_plan("download", {"title": "Test"})

        payload = self.store.consume_plan(plan_id, "download")

        self.assertEqual(payload["title"], "Test")
        with self.assertRaisesRegex(PlanError, "already consumed"):
            self.store.consume_plan(plan_id, "download")

    def test_expired_plan_is_rejected(self):
        plan_id = self.store.create_plan(
            "download",
            {"title": "Test"},
            ttl_seconds=10,
        )
        self.clock.now += 11

        with self.assertRaisesRegex(PlanError, "expired"):
            self.store.consume_plan(plan_id, "download")

    def test_action_mismatch_does_not_consume_plan(self):
        plan_id = self.store.create_plan("download", {"title": "Test"})

        with self.assertRaisesRegex(PlanError, "action mismatch"):
            self.store.consume_plan(plan_id, "delete")

        self.assertEqual(
            self.store.consume_plan(plan_id, "download")["title"],
            "Test",
        )

    def test_task_persistence_uses_an_allowlist(self):
        self.store.upsert_task(
            {
                "task_id": "rd-test",
                "title": "Test",
                "media_type": "movie",
                "qas_task_name": "Test",
                "aria2_gids": ["abc"],
                "staging_path": "/safe/.incoming/rd-test",
                "final_path": "/safe/Movie/Test",
                "status": "submitted",
                "cookie": "must-not-persist",
                "secret": "must-not-persist",
            }
        )

        serialized = json.dumps(self.store.list_tasks(), ensure_ascii=False)

        self.assertNotIn("must-not-persist", serialized)
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("secret", serialized)

    def test_candidate_is_opaque_and_expires(self):
        candidate_id = self.store.create_candidate(
            {
                "query": "Example",
                "shareurl": "https://pan.quark.cn/s/example",
            },
            ttl_seconds=10,
        )

        self.assertTrue(candidate_id.startswith("candidate-"))
        self.assertEqual(
            self.store.get_candidate(candidate_id)["query"],
            "Example",
        )
        self.clock.now += 11
        with self.assertRaisesRegex(PlanError, "candidate expired"):
            self.store.get_candidate(candidate_id)

    def test_candidate_can_be_updated_with_preview_details(self):
        candidate_id = self.store.create_candidate(
            {"query": "Example"},
        )

        self.store.update_candidate(
            candidate_id,
            {"query": "Example", "details": {"list": []}},
        )

        self.assertEqual(
            self.store.get_candidate(candidate_id)["details"],
            {"list": []},
        )


if __name__ == "__main__":
    unittest.main()

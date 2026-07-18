import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from organizer import DownloadValidator, OrganizeError, Organizer
from path_guard import PathGuard
from state_store import StateStore


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.downloads = self.base / "volume2" / "downloads"
        self.anime = self.base / "volume2" / "影视" / "Anime"
        self.movies = self.base / "volume3" / "临时影视" / "Movie"
        for path in (
            self.downloads / ".incoming",
            self.downloads / ".ready",
            self.downloads / ".quarantine",
            self.anime,
            self.movies,
        ):
            path.mkdir(parents=True)
        self.store = StateStore(self.base / "state.db")
        self.guard = PathGuard(
            [
                self.downloads,
                self.base / "volume2" / "影视",
                self.base / "volume3" / "临时影视",
            ]
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def make_task(self, *, status="complete", media_type="anime"):
        task_id = f"rd-{media_type}"
        final_root = self.movies if media_type == "movie" else self.anime
        task = {
            "task_id": task_id,
            "title": "Example",
            "title_key": "example",
            "media_type": media_type,
            "aria2_gids": ["abc"],
            "episode_keys": [],
            "aria2_dir": f"/nas/downloads/.incoming/{task_id}",
            "staging_path": str(self.downloads / ".incoming" / task_id),
            "final_path": str(final_root / "Example"),
            "status": status,
        }
        self.store.upsert_task(task)
        return task

    def make_organizer(
        self,
        *,
        same_filesystem=True,
        copy_verifier=None,
        fsync_tree=None,
    ):
        validator = DownloadValidator(
            self.store,
            self.guard,
            self.downloads,
            ffprobe_runner=None,
        )
        return Organizer(
            self.store,
            self.guard,
            self.downloads,
            validator=validator,
            same_filesystem=lambda source, target: same_filesystem,
            copy_verifier=copy_verifier,
            fsync_tree=fsync_tree,
        )

    def test_every_route_uses_unified_download_root(self):
        routing = json.loads(
            (ROOT / "config" / "routing.json").read_text(encoding="utf-8")
        )
        for name, route in routing.items():
            if name == "downloads":
                continue
            self.assertEqual(
                route["staging_root"],
                "/volume2/downloads/.incoming",
            )
            self.assertEqual(route["aria2_prefix"], "downloads/.incoming")

    def test_incomplete_task_cannot_be_organized(self):
        task = self.make_task(status="active")
        with self.assertRaisesRegex(OrganizeError, "not complete"):
            self.make_organizer().plan(task["task_id"])

    def test_temporary_or_zero_byte_media_is_rejected(self):
        task = self.make_task()
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "episode.mkv").write_bytes(b"")
        (source / "episode.mkv.aria2").write_text("", encoding="utf-8")

        report = self.make_organizer().validator.validate(task["task_id"])

        self.assertFalse(report.ok)
        self.assertEqual(report.next_action, "quarantine_download")
        self.assertIn("temporary_files", report.problems)
        self.assertIn("zero_byte_media", report.problems)

    def test_existing_final_target_is_never_overwritten(self):
        task = self.make_task()
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "episode.mkv").write_bytes(b"video")
        Path(task["final_path"]).mkdir()

        with self.assertRaisesRegex(OrganizeError, "target exists"):
            self.make_organizer().plan(task["task_id"])

    def test_cross_volume_failure_keeps_ready_source(self):
        task = self.make_task(media_type="movie")
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "movie.mkv").write_bytes(b"video")
        organizer = self.make_organizer(
            same_filesystem=False,
            copy_verifier=lambda source, target, manifest: False,
        )
        plan = organizer.plan(task["task_id"])

        with self.assertRaisesRegex(OrganizeError, "verification"):
            organizer.execute(plan["planId"], confirmed=True)

        self.assertTrue((self.downloads / ".ready" / task["task_id"]).exists())
        self.assertFalse(Path(task["final_path"]).exists())

    def test_same_volume_organize_moves_ready_directory(self):
        task = self.make_task()
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "episode.mkv").write_bytes(b"video")
        organizer = self.make_organizer(same_filesystem=True)
        plan = organizer.plan(task["task_id"])

        result = organizer.execute(plan["planId"], confirmed=True)

        self.assertEqual(result["status"], "organized")
        self.assertTrue(Path(task["final_path"]).is_dir())
        self.assertFalse(source.exists())

    def test_cross_volume_fsync_happens_before_verification(self):
        task = self.make_task(media_type="movie")
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "movie.mkv").write_bytes(b"video")
        events = []
        organizer = self.make_organizer(
            same_filesystem=False,
            fsync_tree=lambda target: events.append("fsync"),
            copy_verifier=lambda source, target, manifest: (
                events.append("verify") or True
            ),
        )
        plan = organizer.plan(task["task_id"])

        organizer.execute(plan["planId"], confirmed=True)

        self.assertEqual(events, ["fsync", "verify"])


if __name__ == "__main__":
    unittest.main()

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
        self.organizing = self.base / "volume3" / ".openclaw-organizing"
        self.organizing.mkdir(parents=True, exist_ok=True)
        self.guard = PathGuard(
            [
                self.downloads,
                self.organizing,
                self.base / "volume2" / "影视",
                self.base / "volume3" / "临时影视",
            ],
            protected_roots=[
                self.base / "volume2" / "影视",
                self.base / "volume3" / "临时影视",
            ],
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
            organizing_root=self.organizing,
            same_filesystem=lambda source, target: same_filesystem,
            copy_verifier=copy_verifier,
            fsync_tree=fsync_tree,
        )

    def test_every_route_uses_unified_download_root(self):
        routing = json.loads(
            (ROOT / "config" / "routing.json").read_text(encoding="utf-8")
        )
        for name, route in routing.items():
            if name in {"downloads", "paths"}:
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

    def test_existing_final_target_merges_new_files(self):
        task = self.make_task()
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "episode.mkv").write_bytes(b"video")
        final = Path(task["final_path"])
        final.mkdir()
        (final / "old.mkv").write_bytes(b"old")

        organizer = self.make_organizer(same_filesystem=True)
        plan = organizer.plan(task["task_id"])
        self.assertTrue(plan.get("mergeIntoExisting"))
        result = organizer.execute(plan["planId"], confirmed=True)

        self.assertEqual(result["status"], "organized")
        self.assertTrue((final / "episode.mkv").is_file())
        self.assertTrue((final / "old.mkv").is_file())
        self.assertFalse(source.exists())

    def test_merge_refuses_overwrite_conflicts(self):
        task = self.make_task()
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "episode.mkv").write_bytes(b"new")
        final = Path(task["final_path"])
        final.mkdir()
        (final / "episode.mkv").write_bytes(b"old")

        organizer = self.make_organizer(same_filesystem=True)
        plan = organizer.plan(task["task_id"])
        with self.assertRaisesRegex(OrganizeError, "merge conflicts"):
            organizer.execute(plan["planId"], confirmed=True)
        # Staging may have been relocated to .ready during validate/plan.
        ready = self.downloads / ".ready" / task["task_id"] / "episode.mkv"
        self.assertTrue(ready.is_file() or (source / "episode.mkv").is_file())
        self.assertEqual((final / "episode.mkv").read_bytes(), b"old")

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
        # Temp copy lives outside protected library and is cleaned on failure.
        self.assertFalse(
            (self.organizing / f".organizing-{task['task_id']}").exists()
        )
        self.assertFalse(
            (
                Path(task["final_path"]).parent
                / f".organizing-{task['task_id']}"
            ).exists()
        )

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

    def test_validate_rejects_incomplete_expected_manifest(self):
        task = self.make_task()
        task["expected_manifest"] = {
            "transferJobCount": 2,
            "expectedFileNames": ["a.mkv", "b.mkv"],
            "expectedFileCount": 2,
            "expectedEpisodeKeys": [],
        }
        self.store.upsert_task(task)
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "a.mkv").write_bytes(b"video")
        validator = DownloadValidator(
            self.store,
            self.guard,
            self.downloads,
            ffprobe_runner=None,
        )

        report = validator.validate(task["task_id"])

        self.assertFalse(report.ok)
        self.assertIn("expected_files_missing", report.problems)
        self.assertTrue(
            (self.downloads / ".quarantine" / task["task_id"]).exists()
        )

    def test_validate_moves_complete_download_to_ready(self):
        task = self.make_task()
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "episode.mkv").write_bytes(b"video")
        validator = DownloadValidator(
            self.store,
            self.guard,
            self.downloads,
            ffprobe_runner=None,
        )

        report = validator.validate(task["task_id"])

        self.assertTrue(report.ok)
        self.assertEqual(report.next_action, "ready_to_organize")
        self.assertTrue((self.downloads / ".ready" / task["task_id"]).exists())
        self.assertFalse(source.exists())
        refreshed = self.store.get_task(task["task_id"])
        self.assertEqual(refreshed["status"], "ready")
        self.assertEqual(refreshed["aria2_gids"], [])

    def test_validate_accepts_video_plus_sidecar_manifest(self):
        task = self.make_task()
        task["expected_manifest"] = {
            "transferJobCount": 1,
            "expectedVideoFiles": [
                {
                    "id": "fid-a/E01.mkv",
                    "name": "E01.mkv",
                    "fid": "fid-a",
                    "jobIndex": 0,
                }
            ],
            "expectedSidecarFiles": [
                {
                    "id": "fid-a/E01.ass",
                    "name": "E01.ass",
                    "fid": "fid-a",
                    "jobIndex": 0,
                }
            ],
            "expectedFileNames": ["E01.mkv"],
            "expectedFileCount": 1,
            "expectedAllFileCount": 2,
            "expectedEpisodeKeys": [],
        }
        self.store.upsert_task(task)
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "E01.mkv").write_bytes(b"video")
        (source / "E01.ass").write_text("[Script Info]")
        validator = DownloadValidator(
            self.store,
            self.guard,
            self.downloads,
            ffprobe_runner=None,
        )

        report = validator.validate(task["task_id"])

        self.assertTrue(report.ok, report.problems)
        self.assertEqual(self.store.get_task(task["task_id"])["status"], "ready")

    def test_validate_requires_duplicate_basename_coverage(self):
        task = self.make_task()
        task["expected_manifest"] = {
            "transferJobCount": 2,
            "expectedVideoFiles": [
                {"id": "s1/01.mkv", "name": "01.mkv", "fid": "s1", "jobIndex": 0},
                {"id": "s2/01.mkv", "name": "01.mkv", "fid": "s2", "jobIndex": 1},
            ],
            "expectedSidecarFiles": [],
            "expectedFileCount": 2,
            "expectedEpisodeKeys": [],
        }
        self.store.upsert_task(task)
        source = Path(task["staging_path"])
        source.mkdir()
        (source / "01.mkv").write_bytes(b"one")
        validator = DownloadValidator(
            self.store,
            self.guard,
            self.downloads,
            ffprobe_runner=None,
        )

        report = validator.validate(task["task_id"])

        self.assertFalse(report.ok)
        self.assertIn("expected_files_missing", report.problems)

    def test_validate_recovers_quarantined_to_ready(self):
        task = self.make_task()
        quarantine = self.downloads / ".quarantine" / task["task_id"]
        quarantine.mkdir(parents=True)
        (quarantine / "episode.mkv").write_bytes(b"video")
        task["status"] = "quarantined"
        task["staging_path"] = str(quarantine)
        task["aria2_gids"] = ["stale"]
        self.store.upsert_task(task)
        validator = DownloadValidator(
            self.store,
            self.guard,
            self.downloads,
            ffprobe_runner=None,
        )

        report = validator.validate(task["task_id"])

        self.assertTrue(report.ok)
        ready = self.downloads / ".ready" / task["task_id"]
        self.assertTrue(ready.exists())
        self.assertFalse(quarantine.exists())
        refreshed = self.store.get_task(task["task_id"])
        self.assertEqual(refreshed["status"], "ready")
        self.assertEqual(refreshed["aria2_gids"], [])


if __name__ == "__main__":
    unittest.main()
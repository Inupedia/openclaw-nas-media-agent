import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from path_guard import PathGuard, PathGuardError


class PathGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root_a = self.base / "volume2" / "影视"
        self.root_b = self.base / "volume3" / "临时影视"
        self.root_a.mkdir(parents=True)
        self.root_b.mkdir(parents=True)
        self.guard = PathGuard(
            [self.root_a, self.root_b],
            protected_roots=[self.root_a, self.root_b],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_resolves_existing_child(self):
        child = self.root_a / "Drama" / "Show"
        child.mkdir(parents=True)
        self.assertEqual(self.guard.resolve_existing(str(child)), child.resolve())

    def test_rejects_traversal_outside_roots(self):
        with self.assertRaises(PathGuardError):
            self.guard.resolve_target(str(self.root_a / ".." / ".." / "escape"))

    def test_resolves_nonexistent_target_from_closest_parent(self):
        target = self.root_b / ".incoming" / "rd-test" / "file.mkv"
        (self.root_b / ".incoming").mkdir()
        self.assertEqual(
            self.guard.resolve_target(str(target)),
            target.resolve(strict=False),
        )

    def test_root_itself_is_not_mutable(self):
        with self.assertRaises(PathGuardError):
            self.guard.assert_mutable(self.root_a.resolve())

    def test_symlink_escape_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.root_a / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(PathGuardError):
            self.guard.resolve_target(str(link / "file.mkv"))

    def test_protected_library_children_are_never_deletable(self):
        media = self.root_a / "Drama" / "Show.mkv"
        media.parent.mkdir()
        media.write_bytes(b"video")

        with self.assertRaisesRegex(
            PathGuardError,
            "protected media library",
        ):
            self.guard.assert_deletable(media)

    def test_existing_protected_target_is_never_replaceable(self):
        target = self.root_b / "Movie" / "Example"
        target.mkdir(parents=True)

        with self.assertRaisesRegex(
            PathGuardError,
            "protected media library",
        ):
            self.guard.assert_replace_target(target)

    def test_symlink_into_protected_library_is_not_deletable(self):
        staging = self.base / "downloads"
        staging.mkdir()
        media = self.root_a / "Drama" / "Show"
        media.mkdir(parents=True)
        link = staging / "linked-show"
        try:
            link.symlink_to(media, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        guard = PathGuard(
            [staging, self.root_a],
            protected_roots=[self.root_a],
        )

        with self.assertRaisesRegex(
            PathGuardError,
            "protected media library",
        ):
            guard.assert_deletable(link)


if __name__ == "__main__":
    unittest.main()

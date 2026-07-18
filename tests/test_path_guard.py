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
        self.guard = PathGuard([self.root_a, self.root_b])

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


if __name__ == "__main__":
    unittest.main()

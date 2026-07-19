import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from download_fs import (
    ARIA2_DIR_MODE,
    ensure_aria2_writable,
    ensure_managed_download_roots,
    is_world_writable,
)


class DownloadFsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ensure_aria2_writable_sets_world_writable(self):
        target = self.base / "downloads" / ".incoming" / "rd-demo"
        ensure_aria2_writable(target)
        self.assertTrue(target.is_dir())
        self.assertEqual(target.stat().st_mode & 0o777, ARIA2_DIR_MODE)
        self.assertTrue(is_world_writable(target))
        self.assertTrue(is_world_writable(target.parent))

    def test_ensure_managed_download_roots(self):
        downloads = self.base / "volume2" / "downloads"
        roots = ensure_managed_download_roots(downloads)
        self.assertEqual(len(roots), 4)
        for path in roots:
            self.assertTrue(path.is_dir())
            self.assertTrue(is_world_writable(path))


if __name__ == "__main__":
    unittest.main()

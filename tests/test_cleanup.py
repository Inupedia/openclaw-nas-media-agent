import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from duplicate_finder import find_duplicates
from library_scanner import scan


class DuplicateFinderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_equal_content_is_grouped_but_equal_size_difference_is_not(self):
        content = b"a" * 1024
        (self.root / "one.mkv").write_bytes(content)
        (self.root / "two.mkv").write_bytes(content)
        (self.root / "different.mkv").write_bytes(b"b" * 1024)

        groups = find_duplicates(scan(self.root))

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {path.name for path in groups[0].paths},
            {"one.mkv", "two.mkv"},
        )
        self.assertEqual(groups[0].reclaimable_bytes, 1024)


if __name__ == "__main__":
    unittest.main()

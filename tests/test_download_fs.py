import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from download_fs import (
    AGENT_DIR_MODE,
    DEFAULT_INCOMING_MODE,
    DownloadFsError,
    ensure_aria2_writable,
    ensure_managed_download_roots,
    incoming_mode,
    probe_writable,
)


class DownloadFsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_incoming_mode_is_group_writable_not_world_writable(self):
        downloads = self.base / "downloads"
        target = downloads / ".incoming" / "rd-demo"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESOURCE_AGENT_INCOMING_MODE", None)
            ensure_aria2_writable(target, downloads_root=downloads)
        self.assertTrue(target.is_dir())
        if os.name == "posix":
            self.assertEqual(DEFAULT_INCOMING_MODE, 0o770)
            self.assertEqual(target.stat().st_mode & 0o777, 0o770)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o770)
            self.assertFalse(bool(target.stat().st_mode & 0o002))

    def test_discovered_fallback_mode_can_be_explicitly_0777(self):
        downloads = self.base / "downloads"
        target = downloads / ".incoming" / "rd-demo"
        with patch.dict(os.environ, {"RESOURCE_AGENT_INCOMING_MODE": "0777"}):
            ensure_aria2_writable(target, downloads_root=downloads)
            self.assertEqual(incoming_mode(), 0o777)
        if os.name == "posix":
            self.assertEqual(target.stat().st_mode & 0o777, 0o777)

    def test_rejects_unsupported_or_unsafe_incoming_modes(self):
        for value in ("", "invalid", "0666", "4750", "0775"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"RESOURCE_AGENT_INCOMING_MODE": value}):
                    with self.assertRaises(DownloadFsError):
                        incoming_mode()

    def test_ready_and_quarantine_remain_agent_owned(self):
        downloads = self.base / "volume2" / "downloads"
        with patch.dict(os.environ, {"RESOURCE_AGENT_INCOMING_MODE": "0770"}):
            roots = ensure_managed_download_roots(downloads)
        self.assertEqual(len(roots), 4)
        incoming = downloads / ".incoming"
        ready = downloads / ".ready"
        quarantine = downloads / ".quarantine"
        if os.name == "posix":
            self.assertEqual(incoming.stat().st_mode & 0o777, 0o770)
            self.assertEqual(ready.stat().st_mode & 0o777, AGENT_DIR_MODE)
            self.assertEqual(quarantine.stat().st_mode & 0o777, AGENT_DIR_MODE)

    def test_probe_writable_creates_and_removes_unique_file(self):
        target = self.base / "incoming"
        target.mkdir()
        before = set(target.iterdir())
        self.assertTrue(probe_writable(target))
        self.assertEqual(set(target.iterdir()), before)

    def test_probe_writable_returns_false_for_non_directory(self):
        target = self.base / "file"
        target.write_text("x", encoding="utf-8")
        self.assertFalse(probe_writable(target))

    def test_refuses_chmod_outside_downloads_root(self):
        downloads = self.base / "downloads"
        downloads.mkdir()
        outside = self.base / "other"
        with self.assertRaises(DownloadFsError):
            ensure_aria2_writable(outside, downloads_root=downloads)


if __name__ == "__main__":
    unittest.main()

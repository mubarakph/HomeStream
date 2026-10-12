import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest

# Pure matching tests also run on Windows; the service itself requires Linux.
if os.name == "nt":
    sys.modules["fcntl"] = types.SimpleNamespace()
spec = importlib.util.spec_from_file_location("cleanup", Path(__file__).parents[1] / "scripts/torrent_cleanup.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)

class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        self.downloads = self.root / "downloads"
        self.media.mkdir()
        self.downloads.mkdir()
        self.source = self.downloads / "movie.mkv"
        self.source.write_bytes(b"synthetic test data")
        self.link = self.media / "movie.mkv"
        os.link(self.source, self.link)
        self.files = [{"index": 0, "name": "movie.mkv", "priority": 1, "host": self.source}]
        self.record = {}

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, when):
        return cleanup.observe(self.record, self.files, cleanup.library_index(self.media), when, 120)

    def test_existing_media_is_preserved(self):
        self.assertFalse(self.observe(100))
        self.assertFalse(self.observe(1000))

    def test_deleted_media_requires_grace_period(self):
        self.observe(100)
        self.link.unlink()
        self.assertFalse(self.observe(200))
        self.assertFalse(self.observe(319))
        self.assertTrue(self.observe(320))
        self.assertTrue(self.source.exists())  # Matcher never deletes data.

    def test_unmatched_completed_download_is_never_eligible(self):
        self.link.unlink()
        self.assertFalse(self.observe(100))
        self.assertFalse(self.observe(1000))

    def test_shared_season_is_kept_until_every_imported_episode_is_gone(self):
        second = self.downloads / "episode2.mkv"
        second.write_bytes(b"second synthetic file")
        link = self.media / "episode2.mkv"
        os.link(second, link)
        self.files.append({"index": 1, "name": "episode2.mkv", "priority": 1, "host": second})
        self.observe(100)
        self.link.unlink()
        self.assertFalse(self.observe(1000))
        link.unlink()
        self.assertFalse(self.observe(1100))
        self.assertTrue(self.observe(1220))

    def test_partially_imported_pack_is_not_deleted(self):
        self.files.append({"index": 1, "name": "episode2.mkv", "priority": 1, "host": self.downloads / "episode2.mkv"})
        self.observe(100)
        self.link.unlink()
        self.assertFalse(self.observe(1000))

    def test_library_rename_is_recognized(self):
        self.observe(100)
        self.link.rename(self.media / "renamed.mkv")
        self.assertFalse(self.observe(1000))
        self.assertEqual(len(self.record["mapped"]["0"]["paths"]), 2)

    def test_restored_media_resets_deletion_countdown(self):
        self.observe(100)
        self.link.unlink()
        self.observe(200)
        os.link(self.source, self.link)
        self.assertFalse(self.observe(1000))
        self.assertNotIn("missing_since", self.record)

    def test_download_mapping_stays_inside_root(self):
        result = cleanup.source_path("/data/downloads", "movie.mkv", self.downloads)
        self.assertEqual(result, self.source.resolve())
        for save, name in [("/data/downloads", "../movie.mkv"), ("/data/media", "movie.mkv"), ("/data/downloads", "/etc/passwd")]:
            with self.assertRaises(ValueError):
                cleanup.source_path(save, name, self.downloads)

    def test_symlink_escape_is_rejected(self):
        if os.name == "nt":
            self.skipTest("Symlink permissions vary on Windows; exercised on Ubuntu")
        outside = self.root / "outside"
        outside.mkdir()
        (self.downloads / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            cleanup.source_path("/data/downloads", "escape/movie.mkv", self.downloads)

if __name__ == "__main__":
    unittest.main()

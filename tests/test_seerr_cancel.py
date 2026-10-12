import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from seerr_cancel_cleanup import aliases, cancelled

def request(status=2, tmdb=10):
    return {"type": "movie", "status": status, "media": {"tmdbId": tmdb}}

class CancellationTests(unittest.TestCase):
    def test_removed(self):
        self.assertEqual(set(cancelled({"1": request()}, {})), {"1"})
    def test_declined(self):
        self.assertEqual(set(cancelled({"1": request()}, {"1": request(3)})), {"1"})
    def test_failed_is_not_cancelled(self):
        self.assertFalse(cancelled({"1": request()}, {"1": request(4)}))
    def test_other_request_protects_title(self):
        self.assertFalse(cancelled({"1": request()}, {"2": request()}))
    def test_pending_protects_title(self):
        self.assertFalse(cancelled({"1": request()}, {"1": request(1)}))
    def test_hybrid_hashes(self):
        self.assertEqual(aliases({"hash": "ABC", "infohash_v1": "DEF",
                                  "infohash_v2": ""}), {"abc", "def"})

if __name__ == "__main__":
    unittest.main()

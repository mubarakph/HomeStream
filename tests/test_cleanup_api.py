import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

@unittest.skipIf(os.name == "nt", "Linux service integration test")
class ApiDeletionTest(unittest.TestCase):
    def test_only_registered_deleted_media_triggers_torrent_and_data_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media, downloads = root / "media", root / "downloads"
            media.mkdir(); downloads.mkdir()
            source, imported = downloads / "fixture.mkv", media / "fixture.mkv"
            source.write_bytes(b"synthetic test fixture")
            os.link(source, imported)
            credentials, state = root / "credentials.json", root / "state.json"
            credentials.write_text(json.dumps({"username": "test", "password": "test"}))
            removed = []
            hash_id = "a" * 40
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def send(self, body):
                    self.send_response(200); self.end_headers(); self.wfile.write(body)
                def do_GET(self):
                    path = urllib.parse.urlparse(self.path).path
                    if path.endswith("/version"):
                        self.send(b"test")
                    elif path.endswith("/files"):
                        self.send(json.dumps([{"index": 0, "name": "fixture.mkv", "priority": 1}]).encode())
                    else:
                        torrents = [] if removed else [{"hash": hash_id, "category": "movies",
                                  "progress": 1, "save_path": "/data/downloads"}]
                        self.send(json.dumps(torrents).encode())
                def do_POST(self):
                    values = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                    if self.path.endswith("/delete"):
                        if values != {"hashes": [hash_id], "deleteFiles": ["true"]}:
                            self.send_error(400); return
                        removed.append(hash_id)
                        source.unlink()
                    self.send(b"Ok.")
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            command = [sys.executable, str(Path(__file__).parents[1] / "scripts/torrent_cleanup.py"),
                       "--base-url", "http://127.0.0.1:" + str(server.server_port),
                       "--credentials", str(credentials), "--media-root", str(media),
                       "--download-root", str(downloads), "--state", str(state)]
            try:
                subprocess.run(command + ["--apply"], check=True, capture_output=True)
                self.assertFalse(removed)
                self.assertTrue(imported.exists())
                imported.unlink()
                subprocess.run(command + ["--apply"], check=True, capture_output=True)
                self.assertFalse(removed)  # Grace period blocks immediate deletion.
                saved = json.loads(state.read_text())
                saved["torrents"][hash_id]["missing_since"] = time.time() - 125
                state.write_text(json.dumps(saved))
                subprocess.run(command, check=True, capture_output=True)
                self.assertFalse(removed)  # Dry-run cannot delete.
                subprocess.run(command + ["--apply"], check=True, capture_output=True)
                self.assertEqual(removed, [hash_id])
                self.assertFalse(source.exists())
                self.assertFalse(json.loads(state.read_text())["torrents"])
            finally:
                server.shutdown(); server.server_close(); thread.join()

if __name__ == "__main__":
    unittest.main()

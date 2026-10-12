import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).parents[1]
SOURCE = (REPO / "install.sh").read_text()
GIT_FUNCTIONS = SOURCE.split("# BEGIN SOURCE UPDATE FUNCTIONS\n", 1)[1].split("# END SOURCE UPDATE FUNCTIONS", 1)[0]
BACKUP_FUNCTIONS = SOURCE.split("# BEGIN CONSISTENT BACKUP FUNCTIONS\n", 1)[1].split("# END CONSISTENT BACKUP FUNCTIONS", 1)[0]
BASH = str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe") if os.name == "nt" else shutil.which("bash")

def git(folder, *args):
    return subprocess.check_output(["git", "-C", str(folder), *args], stderr=subprocess.DEVNULL).decode().strip()

class SourceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote, self.seed, self.work = [self.root / n for n in ["remote.git", "seed", "work"]]
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(self.seed)], check=True, capture_output=True)
        for name, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
            git(self.seed, "config", name, value)
        (self.seed / ".gitignore").write_text(".env\n")
        (self.seed / "version.txt").write_text("one")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "initial")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "-u", "origin", "main")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        subprocess.run(["git", "clone", str(self.remote), str(self.work)], check=True, capture_output=True)
        for name, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
            git(self.work, "config", name, value)
        (self.work / ".env").write_text("PRIVATE_LOCAL_SETTING=preserve\n")

    def tearDown(self):
        # Git on Windows marks objects read-only.
        def writable(function, path, error):
            os.chmod(path, 0o700)
            function(path)
        if os.name == "nt":
            resolved = self.root.resolve()
            allowed = Path(tempfile.gettempdir()).resolve()
            assert resolved != allowed and resolved.is_relative_to(allowed)
            shutil.rmtree(self.root, onerror=writable)
            self.tmp.cleanup()
        else:
            self.tmp.cleanup()

    def upstream_change(self):
        (self.seed / "version.txt").write_text("two")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "update")
        git(self.seed, "push")

    def update(self):
        command = 'set -Eeuo pipefail\nSTACK_DIR="$1"\n' + GIT_FUNCTIONS
        command += '\nrun_git() { git -C "$STACK_DIR" "$@"; }\nrefresh_sources\n'
        return subprocess.run([BASH, "-c", command, "test", self.work.as_posix()],
                              capture_output=True, text=True)

    def test_clean_fast_forward_preserves_private_env(self):
        self.upstream_change()
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / "version.txt").read_text(), "two")
        self.assertIn("preserve", (self.work / ".env").read_text())

    def test_dirty_source_is_not_overwritten(self):
        self.upstream_change()
        (self.work / "version.txt").write_text("my local change")
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.work / "version.txt").read_text(), "my local change")

    def test_diverged_history_is_not_merged(self):
        (self.work / "local.txt").write_text("local")
        git(self.work, "add", ".")
        git(self.work, "commit", "-m", "local")
        head = git(self.work, "rev-parse", "HEAD")
        self.upstream_change()
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)

    def test_incoming_private_file_is_rejected(self):
        (self.seed / ".env").write_text("REMOTE_SETTING=must_not_overwrite\n")
        git(self.seed, "add", "-f", ".env")
        git(self.seed, "commit", "-m", "bad private file")
        git(self.seed, "push")
        self.assertNotEqual(self.update().returncode, 0)
        self.assertIn("preserve", (self.work / ".env").read_text())

@unittest.skipIf(os.name == "nt", "Consistent backup tests run on Linux")
class BackupTests(unittest.TestCase):
    def run_backup(self, fail=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            appdata = root / "appdata"
            appdata.mkdir()
            (appdata / "settings.txt").write_text("synthetic settings")
            cache = appdata / "jellyfin-cache"; cache.mkdir()
            (cache / "cache.bin").write_text("cache not backed up")
            (root / ".env").write_text("synthetic setting")
            (root / "compose.yaml").write_text("services: {}")
            calls = root / "calls.txt"
            mock = """
docker() {
  case "$1" in
    compose) printf 'fixture-container\\n' ;;
    stop) echo stop >> "$CALL_LOG" ;;
    start) echo start >> "$CALL_LOG" ;;
    image) echo 'fixture image digest' ;;
  esac
}
tar() {
  if [[ "$FAIL_TAR" == 1 ]]; then return 42; fi
  command tar "$@"
}
"""
            command = 'set -Eeuo pipefail\ncd "$1"\n' + mock + BACKUP_FUNCTIONS + '\nbackup_application_data\n'
            environment = dict(os.environ, APPDATA_ROOT=str(appdata), CALL_LOG=str(calls),
                               FAIL_TAR="1" if fail else "0")
            environment.pop("HOMESTREAM_PREVIOUS_REV", None)
            result = subprocess.run([BASH, "-c", command, "test", str(root)],
                                    env=environment, capture_output=True, text=True)
            recorded = calls.read_text().splitlines()
            archives = list((root / "homestream-backups").glob("*/appdata.tar.gz"))
            contents = []
            if not fail:
                contents = subprocess.check_output(["tar", "-tzf", str(archives[0])]).decode()
            return result, recorded, contents

    def test_stops_backs_up_and_restarts_previous_containers(self):
        result, recorded, contents = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded, ["stop", "start"])
        self.assertIn("settings.txt", contents)
        self.assertNotIn("cache.bin", contents)

    def test_backup_failure_restarts_previous_containers(self):
        result, recorded, _ = self.run_backup(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(recorded, ["stop", "start"])

if __name__ == "__main__":
    unittest.main()

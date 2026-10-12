#!/usr/bin/env python3
"""Remove completed, previously imported torrents after all linked media is deleted."""
import argparse
import fcntl
import http.cookiejar
import json
import os
from pathlib import Path, PurePosixPath
import time
import urllib.parse
import urllib.request

VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".wmv"}

def source_path(save_path, name, download_root, container_root="/data/downloads"):
    download_root = download_root.resolve()
    base, leaf = PurePosixPath(save_path), PurePosixPath(name)
    if leaf.is_absolute() or ".." in leaf.parts or ".." in base.parts:
        raise ValueError("Unsafe torrent path")
    relative = (base / leaf).relative_to(PurePosixPath(container_root))
    host = download_root / str(relative)
    if host.is_symlink() or not host.resolve().is_relative_to(download_root):
        raise ValueError("Torrent file escapes download root")
    return host.resolve()

def library_index(root):
    root = root.resolve()
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or path.suffix.lower() not in VIDEO or not path.is_file():
            continue
        path = path.resolve()
        if not path.is_relative_to(root):
            continue
        stat = path.stat()
        result.setdefault((stat.st_dev, stat.st_ino), []).append(str(path))
    return result

def observe(record, files, indexed, now, grace):
    """Only complete associations can become eligible; preserve shared packs."""
    expected = {str(f["index"]) for f in files if f.get("priority", 1) != 0 and
                Path(f["name"]).suffix.lower() in VIDEO}
    mapped = record.setdefault("mapped", {})
    for file in files:
        key = str(file["index"])
        if key not in expected:
            continue
        path = file["host"]
        if path.is_file():
            stat = path.stat()
            matches = indexed.get((stat.st_dev, stat.st_ino), [])
            if matches:
                old = mapped.get(key, {})
                # Replaced source files must acquire a fresh association.
                if old.get("inode") != [stat.st_dev, stat.st_ino]:
                    old = {"inode": [stat.st_dev, stat.st_ino], "paths": []}
                old["paths"] = sorted(set(old["paths"] + matches))
                mapped[key] = old
    if not expected or not expected.issubset(mapped):
        record.pop("missing_since", None)
        return False
    remaining = any(Path(path).exists() for key in expected for path in mapped[key]["paths"])
    if remaining:
        record.pop("missing_since", None)
        return False
    record.setdefault("missing_since", now)
    return now - record["missing_since"] >= grace

class Client:
    def __init__(self, base, credentials):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.post("auth/login", {
            "username": credentials.get("username", credentials.get("admin_user")),
            "password": credentials.get("password", credentials.get("admin_password"))})
        # Verify authentication without printing credentials.
        self.get("app/version", text=True)

    def get(self, endpoint, text=False):
        request = urllib.request.Request(self.base + "/api/v2/" + endpoint)
        data = self.opener.open(request, timeout=20).read()
        return data.decode() if text else json.loads(data)

    def post(self, endpoint, values):
        request = urllib.request.Request(
            self.base + "/api/v2/" + endpoint,
            data=urllib.parse.urlencode(values).encode(),
            headers={"Referer": self.base + "/"})
        return self.opener.open(request, timeout=20).read()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--download-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--grace-seconds", type=int, default=120)
    parser.add_argument("--apply", action="store_true", help="Enable confirmed matched deletion")
    args = parser.parse_args()
    if args.grace_seconds < 120:
        parser.error("The deletion grace period must be at least 120 seconds.")
    media = args.media_root.resolve(strict=True)
    downloads = args.download_root.resolve(strict=True)
    if not media.is_dir() or not downloads.is_dir() or media == Path("/") or downloads == Path("/"):
        raise ValueError("Dedicated existing roots are required")
    if media.is_relative_to(downloads) or downloads.is_relative_to(media):
        raise ValueError("Media and download roots must be separate")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix(".lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    state = json.loads(args.state.read_text()) if args.state.exists() else {
        "version": 1, "media_device": media.stat().st_dev,
        "download_device": downloads.stat().st_dev, "media_root": str(media),
        "download_root": str(downloads), "torrents": {}}
    if state.get("media_root") != str(media) or state.get("download_root") != str(downloads):
        raise ValueError("Configured roots changed; cleanup stopped")
    if state["version"] != 1 or state["media_device"] != media.stat().st_dev or state["download_device"] != downloads.stat().st_dev:
        raise ValueError("Filesystem identity changed; cleanup stopped")
    client = Client(args.base_url, json.loads(args.credentials.read_text()))
    torrents = client.get("torrents/info")
    indexed = library_index(media)
    snapshots, owners = {}, {}
    for torrent in torrents:
        hash_id = torrent["hash"]
        raw = client.get("torrents/files?hash=" + urllib.parse.quote(hash_id))
        try:
            files = [{**f, "host": source_path(torrent["save_path"], f["name"], downloads)} for f in raw]
        except ValueError:
            continue
        snapshots[hash_id] = files
        for f in files:
            owners.setdefault(str(f["host"]), set()).add(hash_id)
    now = time.time()
    deleted, eligible = 0, 0
    present = {t["hash"] for t in torrents}
    for hash_id in list(state["torrents"]):
        if hash_id not in present:
            del state["torrents"][hash_id]
    for torrent in torrents:
        hash_id = torrent["hash"]
        if torrent.get("category") not in ["movies", "tv", ""] or torrent["progress"] < 1:
            continue
        if hash_id not in snapshots or not snapshots[hash_id]:
            continue
        files = snapshots[hash_id]
        record = state["torrents"].setdefault(hash_id, {})
        ready = observe(record, files, indexed, now, args.grace_seconds)
        if not ready or any(owners[str(f["host"])] != {hash_id} for f in files):
            continue
        eligible += 1
        if not args.apply:
            print("Would remove completed imported torrent:", hash_id)
            continue
        # Recheck both library links and torrent paths immediately before deletion.
        if not observe(record, files, library_index(media), time.time(), args.grace_seconds):
            continue
        current = client.get("torrents/info?hashes=" + hash_id)
        if len(current) != 1 or current[0]["progress"] < 1:
            continue
        fresh = client.get("torrents/files?hash=" + hash_id)
        current_paths = {str(source_path(current[0]["save_path"], f["name"], downloads)) for f in fresh}
        if current_paths != {str(f["host"]) for f in files}:
            continue
        client.post("torrents/delete", {"hashes": hash_id, "deleteFiles": "true"})
        del state["torrents"][hash_id]
        deleted += 1
        print("Removed torrent and download data:", hash_id)
    temp = args.state.with_suffix(".tmp")
    old_mask = os.umask(0o077)
    try:
        temp.write_text(json.dumps(state, indent=2))
        temp.chmod(0o600)
        temp.replace(args.state)
    finally:
        os.umask(old_mask)
    print("Tracked:", len(state["torrents"]), "eligible:", eligible, "removed:", deleted,
          "apply:", args.apply)

if __name__ == "__main__":
    main()

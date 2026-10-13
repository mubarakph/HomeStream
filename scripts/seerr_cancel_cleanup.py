#!/usr/bin/env python3
"""Cancel exact downloads when an observed Seerr request is removed or declined."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from torrent_cleanup import Client, source_path

def aliases(torrent):
    return {str(torrent.get(k, "")).lower() for k in ("hash", "infohash_v1", "infohash_v2")} - {""}

def identity(request):
    media = request["media"]
    return (request["type"], int(media["tmdbId"]))

def cancelled(old, current):
    wanted = {identity(r) for r in current.values() if r["status"] != 3}
    return {key: r for key, r in old.items()
            if (key not in current or current[key]["status"] == 3)
            and identity(r) not in wanted}

class API:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key
    def call(self, path, data=None, method=None):
        request = urllib.request.Request(self.url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"X-Api-Key": self.key, "Content-Type": "application/json"},
            method=method)
        raw = urllib.request.urlopen(request, timeout=30).read()
        return json.loads(raw) if raw else None

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stack-root", type=Path, required=True)
    p.add_argument("--download-root", type=Path, required=True)
    p.add_argument("--server", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--delete-library", action="store_true", help="Also delete imported files for cancelled requests")
    a = p.parse_args()
    root = a.stack_root.resolve(strict=True)
    downloads = a.download_root.resolve(strict=True)
    if downloads == Path("/") or not downloads.is_dir():
        raise ValueError("Dedicated download root required")
    statefile = root / "seerr-cancellation-state.json"
    lock = (root / "seerr-cancellation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    binding = [a.server, str(downloads), downloads.stat().st_dev]
    state = json.loads(statefile.read_text()) if statefile.exists() else {
        "binding": binding, "requests": {}, "pending": {}}
    if state["binding"] != binding:
        raise ValueError("Server or download filesystem changed")
    seerr = API(a.server + ":5055", json.loads(
        (root / "appdata/seerr/settings.json").read_text())["main"]["apiKey"])
    current = {}
    offset = 0
    while True:
        page = seerr.call("/api/v1/request?take=100&skip=" + str(offset))
        results = page["results"]
        for r in results:
            if r["status"] not in (1, 2, 3, 4, 5):
                raise ValueError("Unknown request status")
            current[str(r["id"])] = r
        offset += len(results)
        if offset >= page["pageInfo"]["results"]:
            break
        if not results:
            raise ValueError("Incomplete request pagination")
    # Confirm absence independently; transient/pagination failures never cancel.
    for key in state["requests"].keys() - current.keys():
        try:
            current[key] = seerr.call("/api/v1/request/" + key)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    lost = cancelled(state["requests"], current)
    now = time.time()
    for key, r in lost.items():
        state["pending"].setdefault(key, {"request": r, "since": now, "hashes": []})
    wanted = {identity(r) for r in current.values() if r["status"] != 3}
    state["pending"] = {k: v for k, v in state["pending"].items()
                        if identity(v["request"]) not in wanted}
    clients, entities, ownership = {}, {}, {}
    for kind, port, folder, endpoint, field in (
        ("movie", 7878, "radarr", "movie", "movieId"),
        ("tv", 8989, "sonarr", "series", "seriesId")):
        key = ET.parse(root / ("appdata/" + folder + "/config.xml")).findtext("ApiKey")
        client = API(a.server + ":" + str(port), key)
        clients[kind] = client
        entities[kind] = {e["id"]: e for e in client.call("/api/v3/" + endpoint)}
        records = client.call("/api/v3/queue?pageSize=1000")["records"]
        for page in range(1, 11):
            history = client.call("/api/v3/history?pageSize=100&page=" + str(page))
            records += history["records"]
            if page * 100 >= history["totalRecords"]:
                break
        for r in records:
            if r.get("downloadId") and r.get(field):
                ownership.setdefault(r["downloadId"].lower(), set()).add((kind, r[field]))
    qbit = Client(a.server + ":8080", json.loads((root / ".secrets.json").read_text()))
    torrents = qbit.get("torrents/info")
    paths, shared = {}, {}
    for t in torrents:
        files = qbit.get("torrents/files?hash=" + t["hash"])
        paths[t["hash"]] = {source_path(t["save_path"], f["name"], downloads) for f in files}
        # Validate save directory even before metadata arrives.
        source_path(t["save_path"], ".cancellation-check", downloads)
        for path in paths[t["hash"]]:
            shared.setdefault(path, set()).add(t["hash"])
    removed = 0
    for key, item in state["pending"].items():
        r = item["request"]
        kind = r["type"]
        entity_id = r["media"].get("externalServiceId")
        entity = entities.get(kind, {}).get(entity_id)
        if not entity or int(entity.get("tmdbId", 0)) != identity(r)[1]:
            continue
        # Only configured default service is supported; never guess another server.
        if r.get("serverId") not in (None, 0) or r.get("is4k"):
            continue
        target = (kind, entity_id)
        hashes = {h for h, owners in ownership.items() if owners == {target}}
        item["hashes"] = sorted(set(item["hashes"]) | hashes)
        if now - item["since"] < 60:
            continue
        # Confirm cancellation again before unmonitoring or deleting library files.
        try:
            live = seerr.call("/api/v1/request/" + key)
            if live["status"] != 3:
                continue
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        if a.apply:
            entity["monitored"] = False
            if kind == "tv":
                for season in entity.get("seasons", []):
                    season["monitored"] = False
            clients[kind].call("/api/v3/" + ("movie" if kind == "movie" else "series")
                              + "/" + str(entity_id), entity, "PUT")
        if a.apply and a.delete_library:
            if kind == "movie":
                file_id = entity.get("movieFileId")
                if file_id:
                    clients[kind].call("/api/v3/moviefile/" + str(file_id), method="DELETE")
            else:
                # Restrict imported TV deletion to seasons in the cancelled request.
                seasons = {season["seasonNumber"] for season in r.get("seasons", [])}
                files = clients[kind].call("/api/v3/episodefile?seriesId=" + str(entity_id))
                for file in files:
                    if file.get("seasonNumber") in seasons:
                        clients[kind].call("/api/v3/episodefile/" + str(file["id"]), method="DELETE")
        for t in torrents:
            matches = aliases(t) & set(item["hashes"])
            owners = set().union(*(ownership.get(h, set()) for h in aliases(t)))
            if not matches or (owners and owners != {target}):
                continue
            if any(shared[path] != {t["hash"]} for path in paths[t["hash"]]):
                continue
            # Recheck the live request immediately before destructive operation.
            try:
                live = seerr.call("/api/v1/request/" + key)
                if live["status"] != 3:
                    continue
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
            if a.apply:
                qbit.post("torrents/delete", {"hashes": t["hash"], "deleteFiles": "true"})
                removed += 1
    state["requests"].update(current)
    temporary = statefile.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    os.chmod(temporary, 0o600)
    temporary.replace(statefile)
    print("Observed requests:", len(current), "pending cancellations:",
          len(state["pending"]), "removed torrents:", removed,
          "mode:", "apply" if a.apply else "dry-run")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # URLs/response bodies can contain API credentials.
        print("Cancellation check stopped:", type(error).__name__)
        raise SystemExit(1)

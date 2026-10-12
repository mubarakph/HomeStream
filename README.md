# HomeStream

A Docker media platform for Ubuntu: discover and request movies and TV shows,
download and organize your library, then watch through a browser or Android TV.

**Repository name:** `HomeStream`
**GitHub description:** A Docker media platform for Ubuntu with Jellyfin, Seerr,
Radarr, Sonarr, Prowlarr, qBittorrent and FlareSolverr.

## How it works

1. Browse titles and make requests in **Seerr**.
2. **Radarr** manages movies; **Sonarr** manages TV shows.
3. **Prowlarr** searches the indexers you configure.
4. **qBittorrent** downloads the selected release.
5. Radarr/Sonarr import it into your media library.
6. **Jellyfin** plays the imported files on your computer or Android TV.

**FlareSolverr** is included as an internal helper for supported Cloudflare
challenges. It does not guarantee that every protected indexer will work.

Seerr's discovery catalog is not a list of every release available from your
indexers. Search releases directly in Prowlarr. A title appearing in Seerr does
not guarantee your indexers have it.

This stack downloads media before Jellyfin plays it. It does not provide instant
cloud playback, streaming-service subscriptions, or access to their catalogs.
Stremio is a separate application and is not configured by this installer.

## Requirements

- Ubuntu Server 22.04, 24.04 or 26.04 LTS on amd64 or arm64.
- A local account with sudo access and an internet connection.
- A fixed or DHCP-reserved private IPv4 address.
- At least 15 GiB free for deployment; substantially more space for a library.
- 4 GB RAM is a practical starting point; 8 GB is preferable for this stack.
- Available TCP ports: 5055, 8096, 7878, 8989, 9696 and 8080.
- An indexer/tracker that supports your intended movie and TV categories.

An Ubuntu VM in Proxmox is suitable. LXC installations require suitable Docker
support from the host and are not validated by this project. Hardware transcoding
requires additional GPU/device configuration and is not enabled automatically.

## Install with or without Docker

Clone the project repository:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/mubarakph/HomeStream.git
cd HomeStream
sudo bash install.sh install
```

The installer:

- Checks the Ubuntu release and architecture.
- Prompts for the LAN IP, timezone, storage locations and media owner UID/GID.
- Saves configuration to a private `.env` file.
- Creates application and media folders without recursively changing existing files.
- Installs Docker Engine and Compose if missing, using Docker's official repository.
- Reuses an existing Docker installation and installs Compose if needed.
- Checks configuration and ports, downloads images and starts the containers.
- Waits for startup and displays service status and URLs.

It deploys the containers. **Complete the first-time web setup below to connect
the applications.** It does not create application passwords, add indexer accounts,
resize disks, change router rules, or enable automatic deletion.

If incompatible Docker packages are found, the installer stops rather than
removing them. Install a compatible Docker/Compose setup and run it again.
For rootless/custom Docker daemons, use a manually configured deployment instead.

For preconfigured installation:

```bash
cp .env.example .env
nano .env
sudo bash install.sh install
```

Use unquoted values in `.env`. Storage paths must be absolute paths without
spaces or special characters. The two storage roots must be separate directories.
Existing directories must already belong to the selected UID/GID; review any
ownership error manually instead of recursively changing shared storage.

Example values:

```dotenv
SERVER_IP=192.168.1.100
TZ=Etc/UTC
PUID=1000
PGID=1000
APPDATA_ROOT=/srv/homestream/appdata
DATA_ROOT=/srv/homestream/data
```

Use `id -u` and `id -g` to find your regular user's IDs. Seerr uses UID/GID 1000
for its own configuration directory regardless of the media owner's IDs.
Set a timezone such as `Asia/Qatar` if appropriate.

Rerunning the installer reuses `.env` and persistent application data. For regular updates, use `sudo bash install.sh update`; it backs up application data before updating containers.
Changing an IP or directory in `.env` is a configuration change, not a data
migration: existing files are not automatically moved.

## Open the applications

Replace `SERVER-IP` with your configured server IP.

| Application | Address | Purpose |
|---|---|---|
| Jellyfin | http://SERVER-IP:8096 | Watch your library |
| Seerr | http://SERVER-IP:5055 | Discover and request titles |
| Radarr | http://SERVER-IP:7878 | Manage movies |
| Sonarr | http://SERVER-IP:8989 | Manage TV shows |
| Prowlarr | http://SERVER-IP:9696 | Manage and search indexers |
| qBittorrent | http://SERVER-IP:8080 | Download progress |
| FlareSolverr | Internal only | Prowlarr proxy helper |

There is no shared default HomeStream password. Create your accounts during each
application's setup. Enable authentication for Radarr, Sonarr and Prowlarr before
adding sources. Keep API keys and account credentials private.

## First-time setup

### 1. Jellyfin

Open port 8096 and complete the setup wizard:

- Create an administrator account.
- Add a Movies library with path `/media/movies`.
- Add a TV Shows library with path `/media/tv`.
- Create a separate non-admin playback account, for example `tv`.

For home-only use, configure your home subnet as a local network in
Dashboard > Networking. Also include the Docker network subnet:

```bash
sudo docker network inspect homestream_default --format '{{(index .IPAM.Config 0).Subnet}}'
```

For example, your home network could be `192.168.1.0/24`; use your actual subnet.
After local networks are configured, disable remote access and automatic port
mapping. Do not create router port forwards for this home-only deployment.

Jellyfin mounts media read-only. Delete or manage downloads through Radarr/Sonarr.
This keeps library management in the applications that imported the files.

### 2. qBittorrent

Open port 8080. Its initial username is normally `admin`; obtain the temporary
password from its startup log:

```bash
sudo docker compose logs qbittorrent
```

Change the username/password in its Web UI settings. Configure:

| Setting | Value |
|---|---|
| Default save path | `/data/downloads/complete` |
| Incomplete download path | `/data/downloads/incomplete` |
| Movies category | `movies` |
| TV category | `tv` |

Create both categories. Disable Torrent Queueing for simultaneous downloads, or set concurrency limits if preferred. Set seeding limits
according to your storage capacity and tracker requirements. The default Compose
file exposes only its Web UI; outbound peer connections can work, but incoming
peer connectivity requires a separate network/port configuration.

### 3. Radarr and Sonarr

Enable login authentication in both applications.

| Setting | Radarr | Sonarr |
|---|---|---|
| Root folder | `/data/media/movies` | `/data/media/tv` |
| Download client | qBittorrent | qBittorrent |
| Download client host | `qbittorrent` | `qbittorrent` |
| Download client port | `8080` | `8080` |
| Category | `movies` | `tv` |

Enter your qBittorrent username/password and use Test before saving.
Enable completed download handling. Enable hardlinks in Media Management:
downloads and libraries share the same `/data` mount to support efficient imports.

Set a minimum free-space reserve appropriate for the server, for example 6144 MB.
Begin with a 1080p quality profile. Large season packs and 4K releases can consume
a small disk quickly.

Copy each application's API key from Settings > General for the next steps.

### 4. Prowlarr

Enable authentication, then add Radarr and Sonarr under Settings > Apps:

| Setting | Radarr app | Sonarr app |
|---|---|---|
| Prowlarr URL | `http://prowlarr:9696` | `http://prowlarr:9696` |
| Application URL | `http://radarr:7878` | `http://sonarr:8989` |
| API key | Radarr's API key | Sonarr's API key |

Test and save each application, using Full Sync if you want Prowlarr to manage
their indexer settings.

Add your indexers and test them. Choose sources supporting Movies and TV
categories; unrelated categories will not supply movie/TV requests.
This repository includes no indexer accounts, API keys or content sources.

For supported Cloudflare challenges:

1. Open Settings > Indexers > Indexer Proxies and add FlareSolverr.
2. Set its host URL to `http://flaresolverr:8191`.
3. Add a tag named `cloudflare`.
4. Test and save the proxy.
5. Apply the same tag to each indexer that needs it, then test the indexer.

If an indexer fails, check the proxy tags and logs. Some challenges remain
unsupported; use an accessible source or the indexer's supported API.
If tagged indexers do not sync, check application tag filters and categories.

### 5. Seerr

Open port 5055, choose Jellyfin, and log in with the Jellyfin administrator.
Use the internal server address `http://jellyfin:8096`. If the form separates
hostname and port, enter `jellyfin` and `8096`.
Set the external/client address to `http://SERVER-IP:8096`.

Sync libraries and enable Movies and TV Shows. Add:

| Setting | Radarr | Sonarr |
|---|---|---|
| Hostname | `radarr` | `sonarr` |
| Port | `7878` | `8989` |
| Use SSL | No | No |
| API key | Radarr's API key | Sonarr's API key |
| Root directory | `/data/media/movies` | `/data/media/tv` |
| Quality profile | Your chosen 1080p profile | Your chosen 1080p profile |
| Default server | Yes | Yes |

Finish setup and configure request permissions/approval requirements for users.

## Watch and request media

**Computer:** open Jellyfin on port 8096, sign in with the playback account and
open Movies or TV Shows.

**Android TV:** install Jellyfin for Android TV, enter `http://SERVER-IP:8096`,
and sign in with the playback account.

**Request:** search in Seerr, open a title and click Request. Select seasons for
TV shows. An administrator may need to approve the request depending on permissions.
Watch download progress in qBittorrent. After import, the title appears in Jellyfin.

For your first test, request one title available from your configured source.
Confirm that it reaches Radarr/Sonarr, downloads, imports and plays. If Jellyfin
does not show it immediately, run a library scan and refresh the browser/TV app.

## Storage and deletion

The public installer does not enable automatic deletion. Each deployment needs
its own retention policy and explicit agreement about what may be removed.

To free space manually, remove media through Radarr/Sonarr and disable monitoring
where needed to prevent automatic redownloads. Review completed torrents and
seeding requirements before removing their download data. A hardlinked library
file and a seeded download can refer to the same data: deleting only one link
may not reclaim disk space.

An administrator can add a separately reviewed retention service later. The
custom retention service on the original project owner's server is not deployed
by this repository.

## Maintenance

Run these commands from the cloned repository:

```bash
# Status and logs
sudo docker compose ps
sudo docker compose logs --tail=100 seerr radarr sonarr jellyfin

# Restart services
sudo docker compose restart

# Stop or start services without deleting persistent data
sudo docker compose down
sudo docker compose up -d

# Update source and containers with an automatic application backup
sudo bash install.sh update
```

Images follow stable `latest` tags; updates can change behavior. Record known-good
digests and pin image versions/digests if you require repeatable releases.

Back up `.env` and APPDATA_ROOT. For a consistent backup, stop the containers,
copy the application data using your backup tool, then restart. Back up DATA_ROOT
separately if you want to preserve downloaded media. Protect backups because
application data contains credentials and API keys.

## Troubleshooting

| Symptom | Check |
|---|---|
| Jellyfin is empty | Media must download and import first; verify root paths and scan libraries |
| Download finished but no movie | Radarr/Sonarr Activity queue, import errors, categories and permissions |
| Seerr shows a title but cannot download it | Indexer availability, categories, quality profile and approval |
| qBittorrent login fails | Latest startup log; temporary passwords can change after restart |
| Cloudflare error | FlareSolverr connection, matching tags and challenge support |
| Services cannot reach each other | Use Docker service names, not `localhost` |
| Cannot play HEVC in browser | Check Jellyfin transcoding logs and available CPU/GPU capabilities |
| Cannot delete in Jellyfin | Media mount is read-only; use Radarr/Sonarr |
| Installer reports ownership mismatch | Use a new dedicated folder or review existing permissions |
| Installer reports occupied ports | Resolve the conflicting service or carefully change Compose mappings |

Docker published ports can bypass UFW rules. Binding to a LAN address is not a
complete firewall policy. Review Docker-aware firewall rules before exposing
services to other networks. Remote access is outside this installer's scope.

## Publish this repository on GitHub

The GitHub repository is `mubarakph/HomeStream`.
Keep the installation on your existing Ubuntu server separate from this source repo.

Review files, then commit only reusable source:

```bash
git add README.md install.sh compose.yaml .env.example .gitignore .gitattributes scripts/ tests/
git diff --cached
git commit -m "Add HomeStream installer and setup guide"
git branch -M main
git remote add origin https://github.com/mubarakph/HomeStream.git
git push -u origin main
```

If a remote already exists, inspect `git remote -v` before changing it. Never
commit `.env`, passwords, API keys, LOGIN.txt, databases, appdata or media.
If credentials were committed previously, ignoring them does not remove them
from Git history; rotate the credentials and clean the history before publishing.

Choose a license before distributing the installer; the included applications
retain their own licenses. No repository has been published automatically.

## Validation and references

The original Ubuntu 24.04 deployment verified service startup, Seerr discovery,
Radarr/Sonarr download-client connections, media import and Jellyfin playback.
This portable installer still requires a clean-VM installation test before it
should be advertised as production ready. Support for other listed releases and
architectures has not been integration tested.

- [Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Seerr Docker installation](https://docs.seerr.dev/getting-started/docker/)
- [Jellyfin containers](https://jellyfin.org/docs/general/installation/container/)
- [Prowlarr settings](https://wiki.servarr.com/prowlarr/settings)
- [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)

## Optional matched torrent cleanup

`scripts/torrent_cleanup.py` can remove a completed torrent and its download data
after all its tracked library files have been deleted. It is **not enabled by
install.sh**; enable deletion only after reviewing and agreeing to its scope.

The script identifies imports by hardlinks, records them before deletion, and
waits at least 120 seconds after the library files disappear. It preserves active
or unmatched downloads, renamed library files, partially imported packs and
shared season torrents while any tracked episode remains. Files copied instead
of hardlinked, or deleted before first observation, are not automatically matched.
A filesystem or configured-root change stops cleanup rather than resetting trust.

Provide a private credentials JSON file outside the repository:

```json
{"username": "YOUR-QBITTORRENT-USERNAME", "password": "YOUR-QBITTORRENT-PASSWORD"}
```

Set its permissions to 600. Example dry run (replace paths/IP for your deployment):

```bash
sudo python3 scripts/torrent_cleanup.py \\
  --base-url http://SERVER-IP:8080 \\
  --credentials /srv/homestream/credentials.json \\
  --media-root /srv/homestream/data/media \\
  --download-root /srv/homestream/data/downloads \\
  --state /srv/homestream/torrent-cleanup-state.json
```

Dry runs register associations but never delete downloads. Add `--apply` to allow
eligible torrent/data removal. Run it on a recurring schedule, typically once per
minute, using a Linux service account that can read the paths and credentials.
Do not delete the state file as routine housekeeping: it records which downloads
were proven to have been imported. This removes download data too and ends seeding.

On the original server, `media-torrent-cleanup.timer` runs every minute after the
owner explicitly approved this policy. It is separate from free-space retention:
retention retains its existing eligibility rules; this service handles matches
after media is removed. Jellyfin media remains read-only.

```bash
# Original server: inspect or disable the matching cleanup service
sudo journalctl -u media-torrent-cleanup.service
sudo systemctl disable --now media-torrent-cleanup.timer

# Matching and simulated API deletion tests (Linux)
python3 -m unittest discover -s tests -v
```

## One script for installation and updates

This repository is [mubarakph/HomeStream](https://github.com/mubarakph/HomeStream).
Use the same entry point for everyday operations:

```bash
# Fresh Ubuntu server, with or without Docker already installed
sudo bash install.sh install

# Update repository source, back up settings and update containers
sudo bash install.sh update

# Update containers using the current checked-out source
sudo bash install.sh update-images

# Inspect services without changing them
sudo bash install.sh status
```

The update command requires a Git clone with an upstream branch and no local
source changes. It fetches and accepts only a fast-forward update, refuses incoming
changes to private/runtime paths, preserves the local .env, and then runs the
updated script. Git authentication for a private repository must be configured
for the user who owns the clone. The script does not store a GitHub token.

Updates pull images first, then briefly stop the previously running containers to
make a consistent private backup of application data and .env. Jellyfin's cache
is excluded. Backups are stored in a timestamped `homestream-backups` directory
beside APPDATA_ROOT; they include an image digest list and, for source updates,
the previous source revision/Compose file. The old containers restart if the
backup fails. The final Compose operation recreates services with the new images.
A repeated install on an existing Compose deployment also makes this backup.

Keep enough free space for the backup and images. Old images and backups are not
automatically pruned. Updates can migrate application databases: rollback may
require restoring the backup together with the previous image versions and source.
Do not assume downgrading an image alone will undo a database migration. Review
startup failures with `sudo docker compose ps` and `sudo docker compose logs`.
Media files are not included in application backups. Back up separately stored
cleanup credentials/state and any media you want to preserve.

### Concurrent requested downloads

Seerr can send multiple requests. In qBittorrent, open Settings > BitTorrent and
uncheck **Torrent Queueing** if you want all accepted downloads to start together.
This prevents a stalled torrent from consuming the only available download slot.
Each torrent still needs reachable peers, and Radarr/Sonarr must first find an
acceptable release. Downloads share bandwidth and disk space. The original
server now has queueing disabled; new installations choose this in the Web UI.

### Cancel downloads from Seerr

Optional `scripts/seerr_cancel_cleanup.py` watches requests it has previously
observed. Removed or declined requests cancel exactly matched qBittorrent
downloads and unmonitor the matching Radarr movie or Sonarr series. Failed
requests are not cancellations. Another current request for the same title
protects it. Imported Jellyfin library files stay in place.

This helper currently expects the existing stack layout: appdata/seerr/settings.json,
appdata/radarr/config.xml, appdata/sonarr/config.xml and a private .secrets.json
with qBittorrent username/password (or admin_user/admin_password). It supports
the default non-4K services only. It does not retrospectively cancel requests
deleted before its first run, or torrents without a reliable download-ID match.

Run once without --apply to establish its baseline, then schedule every minute:

```bash
python3 scripts/seerr_cancel_cleanup.py --stack-root /opt/media-stack --download-root /srv/media-data/downloads --server http://192.168.18.86
# Add --apply only to enable cancellation and download-file deletion.
```

A cancellation must persist for at least 60 seconds. qBittorrent source files
must remain inside /data/downloads; shared torrent file paths are protected.
Keep the cancellation state private and persistent. The regular installer does
not enable this optional destructive policy automatically.

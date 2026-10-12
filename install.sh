#!/usr/bin/env bash
# One entry point for HomeStream installation, updates and status.
set -Eeuo pipefail
MODE="${1:-install}"
case "$MODE" in
  --help|-h|help)
    cat <<'HELP'
Usage: sudo bash install.sh [install|update|update-images|status]
  install       Install Docker if missing, configure and start HomeStream (default).
  update        Fetch Git updates safely, back up settings and update containers.
  update-images Back up settings and update containers without fetching Git.
  status        Show service status without installing or updating anything.
The installer does not enable media deletion or add indexer accounts.
HELP
    exit 0 ;;
  install|update|update-images|status) ;;
  *) echo "Unknown command: $MODE. Use --help." >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo "Only one command is accepted." >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "Run: sudo bash install.sh $MODE" >&2; exit 1; }
STACK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$STACK_DIR"
trap 'echo "HomeStream $MODE stopped at line $LINENO. Configuration is preserved; correct the error and rerun." >&2' ERR

# BEGIN SOURCE UPDATE FUNCTIONS
run_git() {
  local owner
  owner="$(stat -c %u "$STACK_DIR/.git")"
  if [[ "$owner" -eq 0 ]]; then
    git -C "$STACK_DIR" "$@"
  else
    sudo -u "#$owner" -H -- git -C "$STACK_DIR" "$@"
  fi
}
refresh_sources() {
  [[ -e "$STACK_DIR/.git" ]] || { echo "Update requires a Git clone." >&2; return 1; }
  command -v git >/dev/null || { echo "Install Git first." >&2; return 1; }
  [[ -z "$(run_git status --porcelain)" ]] || {
    echo "Commit or resolve local source changes before updating. Nothing was overwritten." >&2; return 1;
  }
  run_git rev-parse --verify HEAD >/dev/null || { echo "Repository has no initial commit." >&2; return 1; }
  run_git rev-parse --verify '@{upstream}' >/dev/null || {
    echo "Configure the branch upstream before updating." >&2; return 1;
  }
  export HOMESTREAM_PREVIOUS_REV
  HOMESTREAM_PREVIOUS_REV="$(run_git rev-parse HEAD)"
  run_git fetch --prune
  local changed
  while IFS= read -r changed; do
    case "$changed" in
      .env.example) continue ;;
      .env|.env.*|.secrets.json|LOGIN.txt|credentials*|*-state.json|*.key|*.pem|appdata/*|data/*|media/*|downloads/*)
        echo "Incoming Git changes touch private/runtime data: $changed. Update stopped." >&2; return 1 ;;
    esac
  done < <(run_git diff --name-only HEAD '@{upstream}')
  run_git merge --ff-only '@{upstream}'
}
# END SOURCE UPDATE FUNCTIONS

[[ -f compose.yaml ]] || { echo "compose.yaml is missing." >&2; exit 1; }
[[ -z "${DOCKER_HOST:-}" && -z "${DOCKER_CONTEXT:-}" ]] || {
  echo "Unset DOCKER_HOST and DOCKER_CONTEXT before local operation." >&2; exit 1;
}
if [[ "$MODE" == status ]]; then
  command -v docker >/dev/null || { echo "Docker is not installed." >&2; exit 1; }
  [[ "$(docker context show)" == default ]] || { echo "Select the local default Docker context." >&2; exit 1; }
  docker compose ps
  exit 0
fi
if [[ "$MODE" == update ]]; then
  refresh_sources
  # Re-execute the fetched script; account settings stay in the ignored .env.
  exec bash "$STACK_DIR/install.sh" update-images
fi
if [[ "$MODE" != install && ! -f .env ]]; then
  echo "No .env found. Run the install command first." >&2
  exit 1
fi
export HOMESTREAM_MODE="$MODE"
. /etc/os-release
[[ "$ID" == ubuntu ]] || { echo "This installer supports Ubuntu only." >&2; exit 1; }
case "$VERSION_ID" in
  22.04|24.04|26.04) ;;
  *) echo "Supported Ubuntu LTS releases: 22.04, 24.04, 26.04." >&2; exit 1 ;;
esac
case "$(dpkg --print-architecture)" in
  amd64|arm64) ;;
  *) echo "Supported architectures: amd64 and arm64." >&2; exit 1 ;;
esac
if [[ "$MODE" == install ]]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl python3 git tzdata
else
  for tool in docker python3 tar; do
    command -v "$tool" >/dev/null || { echo "Missing $tool; run install first." >&2; exit 1; }
  done
fi

if [[ ! -f .env ]]; then
  [[ -t 0 ]] || { echo "First installation needs a terminal or a prepared .env." >&2; exit 1; }
  suggested_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}')"
  read -r -p "Server LAN IPv4 [${suggested_ip:-192.168.1.100}]: " SERVER_IP
  SERVER_IP="${SERVER_IP:-${suggested_ip:-192.168.1.100}}"
  read -r -p "Timezone [Etc/UTC]: " TZ
  TZ="${TZ:-Etc/UTC}"
  read -r -p "Media directory [/srv/homestream/data]: " DATA_ROOT
  DATA_ROOT="${DATA_ROOT:-/srv/homestream/data}"
  read -r -p "Application data directory [/srv/homestream/appdata]: " APPDATA_ROOT
  APPDATA_ROOT="${APPDATA_ROOT:-/srv/homestream/appdata}"
  suggested_uid="${SUDO_UID:-1000}"
  suggested_gid="${SUDO_GID:-1000}"
  [[ "$suggested_uid" != 0 ]] || suggested_uid=1000
  [[ "$suggested_gid" != 0 ]] || suggested_gid=1000
  read -r -p "Media owner UID [$suggested_uid]: " PUID
  PUID="${PUID:-$suggested_uid}"
  read -r -p "Media owner GID [$suggested_gid]: " PGID
  PGID="${PGID:-$suggested_gid}"
else
  echo "Reusing existing .env. Existing application settings are preserved."
  # Read a strict subset of dotenv without executing its contents.
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    value="${value%$'\r'}"
    case "$key" in
      SERVER_IP|TZ|DATA_ROOT|APPDATA_ROOT|PUID|PGID) printf -v "$key" '%s' "$value" ;;
      ''|\#*) ;;
      *) echo "Unsupported .env entry: $key" >&2; exit 1 ;;
    esac
  done < .env
fi
export SERVER_IP TZ DATA_ROOT APPDATA_ROOT PUID PGID

python3 - <<'PY'
import ipaddress, os, pathlib, re, shutil, socket
required = ["SERVER_IP", "TZ", "DATA_ROOT", "APPDATA_ROOT", "PUID", "PGID"]
for name in required:
    if not os.environ.get(name):
        raise SystemExit("Missing configuration: " + name)
ip = ipaddress.IPv4Address(os.environ["SERVER_IP"])
if not ip.is_private or ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local:
    raise SystemExit("Choose a private LAN IPv4 address.")
probe = socket.socket()
try:
    probe.bind((str(ip), 0))
finally:
    probe.close()
tz = os.environ["TZ"]
if not re.fullmatch(r"[A-Za-z0-9_+/-]+", tz) or ".." in tz.split("/") or not pathlib.Path("/usr/share/zoneinfo", tz).is_file():
    raise SystemExit("Unknown timezone.")
for name in ["PUID", "PGID"]:
    if not os.environ[name].isdigit() or not 1 <= int(os.environ[name]) <= 2147483647:
        raise SystemExit(name + " must be a non-root numeric ID.")
roots = []
for name in ["DATA_ROOT", "APPDATA_ROOT"]:
    value = os.environ[name]
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in value.split("/"):
        raise SystemExit(name + ": use an absolute path without spaces or special characters.")
    path = pathlib.Path(value).resolve()
    if path == pathlib.Path("/") or len(path.parts) < 3:
        raise SystemExit(name + ": choose a dedicated subdirectory.")
    roots.append(path)
if roots[0].is_relative_to(roots[1]) or roots[1].is_relative_to(roots[0]):
    raise SystemExit("Media and application roots must be separate directories.")
uid, gid = int(os.environ["PUID"]), int(os.environ["PGID"])
def ensure(path, owner, group, mode=0o755):
    if path.exists():
        if not path.is_dir() or path.stat().st_uid != owner or path.stat().st_gid != group:
            raise SystemExit("Existing directory has different ownership: " + str(path) +
                             ". Choose another directory or review its permissions manually.")
    else:
        path.mkdir(parents=True, exist_ok=False)
        os.chown(path, owner, group)
        path.chmod(mode)
ensure(roots[0], uid, gid)
ensure(roots[1], uid, gid, 0o750)
for name in ["media", "media/movies", "media/tv", "downloads", "downloads/incomplete", "downloads/complete", "downloads/complete/movies", "downloads/complete/tv"]:
    ensure(roots[0] / name, uid, gid)
for name in ["jellyfin", "jellyfin-cache", "radarr", "sonarr", "prowlarr", "qbittorrent"]:
    ensure(roots[1] / name, uid, gid, 0o750)
ensure(roots[1] / "seerr", 1000, 1000, 0o750)
free = shutil.disk_usage(roots[0]).free / 1024**3
print(f"Media filesystem free: {free:.1f} GiB")
minimum = 15 if os.environ["HOMESTREAM_MODE"] == "install" else 5
if free < minimum:
    raise SystemExit(f"At least {minimum} GiB free is required for this operation.")
if not pathlib.Path(".env").exists():
    old = os.umask(0o077)
    try:
        with open(".env", "x") as f:
            for name in required:
                f.write(name + "=" + os.environ[name] + "\n")
    finally:
        os.umask(old)
PY

add_docker_repository() {
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/homestream-docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $VERSION_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
}
if ! command -v docker >/dev/null; then
  for package in docker.io podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      echo "Existing $package package detected; install a compatible Docker/Compose setup manually first." >&2
      exit 1
    fi
  done
  add_docker_repository
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif ! docker compose version >/dev/null 2>&1; then
  if dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q 'install ok installed'; then
    apt-get install -y docker-compose-v2
  else
    # The existing engine stays installed; add only the Compose plugin.
    if ! apt-cache show docker-compose-plugin >/dev/null 2>&1; then add_docker_repository; fi
    apt-get install -y docker-compose-plugin
  fi
fi
systemctl enable --now docker
[[ "$(docker context show)" == default ]] || {
  echo "Select the local default Docker context and rerun." >&2; exit 1;
}
docker info >/dev/null
docker compose config --quiet

# Check for occupied ports on a first deployment.
if [[ -z "$(docker compose ps -aq)" ]]; then
  python3 - <<'PY'
import os, socket
for port in [5055, 8096, 7878, 8989, 9696, 8080]:
    with socket.socket() as s:
        try:
            s.bind((os.environ["SERVER_IP"], port))
        except OSError as e:
            raise SystemExit(f"Port {port} is unavailable: {e}. Resolve this before starting.")
PY
fi
# BEGIN CONSISTENT BACKUP FUNCTIONS
BACKUP_IN_PROGRESS=false
PREVIOUS_CONTAINER_IDS=()
restore_previous_containers() {
  if [[ "$BACKUP_IN_PROGRESS" == true ]]; then
    if [[ ${#PREVIOUS_CONTAINER_IDS[@]} -gt 0 ]]; then
      docker start "${PREVIOUS_CONTAINER_IDS[@]}" >/dev/null || {
        echo "Some previous containers could not restart. Check docker compose ps." >&2; return 1;
      }
    fi
    BACKUP_IN_PROGRESS=false
  fi
}
trap restore_previous_containers EXIT
backup_application_data() {
  BACKUP_DIR="$(python3 - <<'PY'
import os, pathlib, shutil, time
root = pathlib.Path(os.environ["APPDATA_ROOT"]).resolve()
base = (root.parent / "homestream-backups").resolve()
if base.is_relative_to(root):
    raise SystemExit("Backup directory must be outside application data.")
base.mkdir(mode=0o700, parents=True, exist_ok=True)
size = 0
for parent, dirs, files in os.walk(root):
    if pathlib.Path(parent) == root and "jellyfin-cache" in dirs:
        dirs.remove("jellyfin-cache")
    for name in files:
        file = pathlib.Path(parent) / name
        if not file.is_symlink():
            size += file.stat().st_size
if shutil.disk_usage(base).free < size + 1024**3:
    raise SystemExit("Not enough backup space; need application data size plus 1 GiB.")
dest = base / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + str(os.getpid()))
dest.mkdir(mode=0o700)
print(dest)
PY
)"
  mapfile -t PREVIOUS_CONTAINER_IDS < <(docker compose ps --status running -q)
  BACKUP_IN_PROGRESS=true
  if [[ ${#PREVIOUS_CONTAINER_IDS[@]} -gt 0 ]]; then
    docker stop "${PREVIOUS_CONTAINER_IDS[@]}" >/dev/null
  fi
  (
    umask 077
    cp -- .env compose.yaml "$BACKUP_DIR/"
    docker image ls --digests > "$BACKUP_DIR/images.txt"
    if [[ "${HOMESTREAM_PREVIOUS_REV:-}" =~ ^[0-9a-f]{40,64}$ ]]; then
      printf '%s\n' "$HOMESTREAM_PREVIOUS_REV" > "$BACKUP_DIR/previous-revision.txt"
      run_git show "$HOMESTREAM_PREVIOUS_REV:compose.yaml" > "$BACKUP_DIR/compose.previous.yaml"
    fi
    tar -czf "$BACKUP_DIR/appdata.tar.gz" --exclude='./jellyfin-cache' -C "$APPDATA_ROOT" .
  )
  restore_previous_containers
  echo "Private application backup saved: $BACKUP_DIR"
}
# END CONSISTENT BACKUP FUNCTIONS

docker compose pull
# Updates and repeat installations protect existing databases and credentials.
if [[ "$MODE" == update-images || -n "$(docker compose ps -aq)" ]]; then
  backup_application_data
fi
docker compose up -d --wait --wait-timeout 240

docker image ls --digests > image-manifest.local.txt
docker compose ps
printf '\nHomeStream %s completed. First-time application setup is described in README.md.\n' "$MODE"
printf 'Seerr:    http://%s:5055\nJellyfin: http://%s:8096\nProwlarr: http://%s:9696\n' "$SERVER_IP" "$SERVER_IP" "$SERVER_IP"
printf 'Indexer accounts and application logins are configured through the web interfaces.\n'

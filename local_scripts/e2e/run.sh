#!/usr/bin/env bash
# End-to-end check of the JellyfinToken Seerr mode: this plugin + jellyfin-plugin-seerr-proxy
# + real Seerr, in throwaway Docker containers. Needs docker, the .NET 10 SDK and python3.
#
#   local_scripts/e2e/run.sh            build both plugins from their working trees, test, tear down
#   KEEP=1 local_scripts/e2e/run.sh     leave the containers running afterwards
#
# Overrides: PROXY_DIR, JELLYFIN_IMAGE, SEERR_IMAGE, JELLYFIN_PORT, SEERR_PORT
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PROXY_DIR="${PROXY_DIR:-$ROOT/../jellyfin-plugin-seerr-proxy}"
JELLYFIN_IMAGE="${JELLYFIN_IMAGE:-jellyfin/jellyfin:12.1}"
SEERR_IMAGE="${SEERR_IMAGE:-ghcr.io/seerr-team/seerr:develop}"
export JELLYFIN_PORT="${JELLYFIN_PORT:-18096}" SEERR_PORT="${SEERR_PORT:-15055}"
NET=wholphin-e2e
JF=wholphin-e2e-jellyfin
SEERR=wholphin-e2e-seerr
WORK="$(mktemp -d)"

remove_containers() {
  docker rm -f -v "$JF" "$SEERR" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}

cleanup() {
  if [ "${KEEP:-0}" = 1 ]; then
    echo "Left running: Jellyfin http://127.0.0.1:$JELLYFIN_PORT (admin / admin-pass), Seerr http://127.0.0.1:$SEERR_PORT"
    echo "Remove with: docker rm -f -v $JF $SEERR && docker network rm $NET"
  else
    remove_containers
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
remove_containers

echo "Wholphin plugin: $(git -C "$ROOT" log -1 --format='%h %s')"
dotnet publish "$ROOT/Jellyfin.Plugin.Wholphin" -c Release -o "$WORK/wholphin" -nologo -v q
echo "Seerr proxy:     $(git -C "$PROXY_DIR" log -1 --format='%h %s')"
dotnet publish "$PROXY_DIR/Jellyfin.Plugin.SeerrProxy.csproj" -c Release -o "$WORK/proxy" -nologo -v q

mkdir -p "$WORK/plugins/Wholphin_0.1.0.0" "$WORK/plugins/Seerr Proxy_1.0.0.0"
cp "$WORK/wholphin/"*.dll "$WORK/plugins/Wholphin_0.1.0.0/"
cp "$WORK/proxy/Jellyfin.Plugin.SeerrProxy.dll" "$WORK/plugins/Seerr Proxy_1.0.0.0/"

docker network create "$NET" >/dev/null
docker run -d --quiet --name "$SEERR" --network "$NET" --network-alias seerr \
  -p "127.0.0.1:$SEERR_PORT:5055" -e TZ=UTC "$SEERR_IMAGE" >/dev/null
docker create --quiet --name "$JF" --network "$NET" --network-alias jellyfin \
  -p "127.0.0.1:$JELLYFIN_PORT:8096" "$JELLYFIN_IMAGE" >/dev/null
docker cp "$WORK/plugins" "$JF:/config/"
docker start "$JF" >/dev/null

python3 -I "$HERE/e2e.py"

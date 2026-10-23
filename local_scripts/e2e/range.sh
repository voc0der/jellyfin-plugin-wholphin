#!/usr/bin/env bash
# End-to-end check of per-row date ranges (`range: oct15-oct31`): this plugin in a throwaway
# Jellyfin container. Needs docker, the .NET 10 SDK and python3. No Seerr needed.
#
#   local_scripts/e2e/range.sh            build from the working tree, test, tear down
#   KEEP=1 local_scripts/e2e/range.sh     leave the container running afterwards
#
# Overrides: JELLYFIN_IMAGE, JELLYFIN_PORT
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
JELLYFIN_IMAGE="${JELLYFIN_IMAGE:-jellyfin/jellyfin:12.1}"
export JELLYFIN_PORT="${JELLYFIN_PORT:-18097}" JF=wholphin-range-jellyfin
WORK="$(mktemp -d)"

cleanup() {
  if [ "${KEEP:-0}" = 1 ]; then
    echo "Left running: Jellyfin http://127.0.0.1:$JELLYFIN_PORT (admin / admin-pass)"
    echo "Remove with: docker rm -f -v $JF"
  else
    docker rm -f -v "$JF" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
docker rm -f -v "$JF" >/dev/null 2>&1 || true

echo "Wholphin plugin: $(git -C "$ROOT" log -1 --format='%h %s') + working tree ($(git -C "$ROOT" diff --stat | tail -1))"
dotnet publish "$ROOT/Jellyfin.Plugin.Wholphin" -c Release -o "$WORK/wholphin" -nologo -v q
mkdir -p "$WORK/plugins/Wholphin_0.1.0.0"
cp "$WORK/wholphin/"*.dll "$WORK/plugins/Wholphin_0.1.0.0/"

docker create --quiet --name "$JF" -p "127.0.0.1:$JELLYFIN_PORT:8096" "$JELLYFIN_IMAGE" >/dev/null
docker cp "$WORK/plugins" "$JF:/config/"
docker start "$JF" >/dev/null

python3 -I "$HERE/range.py"

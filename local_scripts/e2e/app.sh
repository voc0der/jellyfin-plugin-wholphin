#!/usr/bin/env bash
# End-to-end check of per-row date ranges in the real Wholphin app: this plugin in a throwaway Jellyfin,
# and a Wholphin APK on a headless Android TV emulator in Docker, driven over adb with screenshots.
# Needs docker (with /dev/kvm on the host), the .NET 10 SDK, python3, and an Android SDK with:
#   sdkmanager --install emulator "system-images;android-36;android-tv;x86_64"
#
#   WHOLPHIN_APK=<x86_64 or universal APK> local_scripts/e2e/app.sh
#   KEEP=1 ...    leave both containers running afterwards
#
# Build the APK in a Wholphin checkout with ./gradlew :app:assembleDefaultDebug.
# Overrides: JELLYFIN_IMAGE, JELLYFIN_PORT, ANDROID_SDK_ROOT, SHOTS_DIR, and HOST_HOME (where the Docker
# daemon sees $HOME, if this runs in a sandbox that bind-mounts another directory there; detected for bwrap).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
: "${WHOLPHIN_APK:?set WHOLPHIN_APK to a Wholphin APK (x86_64 or universal)}"
JELLYFIN_IMAGE="${JELLYFIN_IMAGE:-jellyfin/jellyfin:12.4}"
SDK="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-$HOME/Android/Sdk}}"
SYSIMG=system-images/android-36/android-tv/x86_64
EMU_IMAGE=wholphin-e2e-emulator
export JELLYFIN_PORT="${JELLYFIN_PORT:-18099}" SHOTS_DIR="${SHOTS_DIR:-$HERE/shots}"
export JF=wholphin-app-jellyfin EMU=wholphin-app-emulator
HOST_HOME="${HOST_HOME:-$(awk -v h="$HOME" '$5 == h && $4 != "/" {print $4; exit}' /proc/self/mountinfo)}"
HOST_HOME="${HOST_HOME:-$HOME}"
case "$SDK" in "$HOME"/*) HOST_SDK="$HOST_HOME${SDK#"$HOME"}" ;; *) HOST_SDK="$SDK" ;; esac
WORK="$(mktemp -d)"

[ -x "$SDK/emulator/emulator" ] || { echo "No emulator in $SDK: sdkmanager --install emulator"; exit 1; }
[ -d "$SDK/$SYSIMG" ] || { echo "No Android TV image in $SDK: sdkmanager --install \"${SYSIMG//\//;}\""; exit 1; }
[ -f "$WHOLPHIN_APK" ] || { echo "No such APK: $WHOLPHIN_APK"; exit 1; }

cleanup() {
  if [ "${KEEP:-0}" = 1 ]; then
    echo "Left running: Jellyfin http://127.0.0.1:$JELLYFIN_PORT (admin / admin-pass, alice / alice-pass), emulator in $EMU"
    echo "Remove with: docker rm -f -v $EMU $JF"
  else
    docker rm -f -v "$EMU" "$JF" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
docker rm -f -v "$EMU" "$JF" >/dev/null 2>&1 || true

echo "Wholphin plugin: $(git -C "$ROOT" log -1 --format='%h %s') + working tree ($(git -C "$ROOT" diff --stat | tail -1))"
echo "Wholphin app:    $(basename "$WHOLPHIN_APK")"
dotnet publish "$ROOT/Jellyfin.Plugin.Wholphin" -c Release -o "$WORK/wholphin" -nologo -v q -nodeReuse:false -p:UseSharedCompilation=false
mkdir -p "$WORK/plugins/Wholphin_0.1.0.0"
cp "$WORK/wholphin/"*.dll "$WORK/plugins/Wholphin_0.1.0.0/"

docker create --quiet --name "$JF" -p "127.0.0.1:$JELLYFIN_PORT:8096" "$JELLYFIN_IMAGE" >/dev/null
docker cp "$WORK/plugins" "$JF:/config/"
docker start "$JF" >/dev/null
# Three libraries of three 3-second movies, with one poster colour per library.
for lib in around:green tomorrow:red norange:blue; do
  dir=${lib%%:*} color=${lib##*:}
  for n in 1 2 3; do
    m="/media/$dir/$dir $n"
    docker exec "$JF" sh -c "mkdir -p '$m' && ff=/usr/lib/jellyfin-ffmpeg/ffmpeg \
      && \$ff -loglevel error -f lavfi -i color=c=$color:s=640x360:d=3 -c:v libx264 -pix_fmt yuv420p '$m/$dir $n.mp4' \
      && \$ff -loglevel error -f lavfi -i color=c=$color:s=400x600 -frames:v 1 '$m/poster.jpg'"
  done
done

# The emulator shares the Jellyfin container's network, so the app reaches Jellyfin at http://10.0.2.2:8096.
if ! docker image inspect "$EMU_IMAGE" >/dev/null 2>&1; then
  printf 'FROM %s\nRUN apt-get update -qq && apt-get install -y -qq --no-install-recommends %s >/dev/null && rm -rf /var/lib/apt/lists/*\n' \
    "$JELLYFIN_IMAGE" "libx11-6 libx11-xcb1 libxcb1 libxext6 libxfixes3 libxrender1 libxdamage1 libxcomposite1 libxcursor1 libxi6 \
    libxtst6 libxrandr2 libxkbfile1 libxkbcommon0 libxkbcommon-x11-0 libnss3 libdbus-1-3 libfontconfig1 libpulse0 libgl1 libegl1" \
    | docker build -q -t "$EMU_IMAGE" - >/dev/null
fi
mkdir -p "$WORK/avd/tv.avd"
printf 'path=/avd/tv.avd\ntarget=android-36\n' > "$WORK/avd/tv.ini"
cat > "$WORK/avd/tv.avd/config.ini" <<EOF
avd.ini.encoding=UTF-8
PlayStore.enabled=no
abi.type=x86_64
hw.cpu.arch=x86_64
hw.cpu.ncore=4
hw.ramSize=2048
hw.lcd.width=1920
hw.lcd.height=1080
hw.lcd.density=320
hw.dPad=yes
hw.keyboard=no
hw.mainKeys=yes
hw.gpu.enabled=no
disk.dataPartition.size=6G
image.sysdir.1=$SYSIMG/
tag.id=android-tv
tag.display=Android TV
target=android-36
EOF
docker create --quiet --name "$EMU" --network "container:$JF" --device /dev/kvm -v "$HOST_SDK:/sdk:ro" \
  -e ANDROID_SDK_ROOT=/sdk -e ANDROID_AVD_HOME=/avd --entrypoint sh "$EMU_IMAGE" -c \
  'mkdir -p ~/.android && /sdk/platform-tools/adb keygen ~/.android/adbkey >/dev/null 2>&1;
   exec /sdk/emulator/emulator -avd tv -no-window -no-audio -no-boot-anim -no-snapshot -no-metrics -gpu swiftshader_indirect' >/dev/null
docker cp "$WORK/avd" "$EMU:/avd"
docker cp "$WHOLPHIN_APK" "$EMU:/tmp/wholphin.apk"
docker start "$EMU" >/dev/null

python3 -I "$HERE/app.py"

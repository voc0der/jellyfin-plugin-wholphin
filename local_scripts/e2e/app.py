"""Drives the real Wholphin app in the emulator app.sh starts, and checks which home rows it shows today."""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

JELLYFIN = "http://127.0.0.1:" + os.environ.get("JELLYFIN_PORT", "18099")
JF = os.environ.get("JF", "wholphin-app-jellyfin")
EMU = os.environ.get("EMU", "wholphin-app-emulator")
SHOTS = os.environ.get("SHOTS_DIR", "shots")
CONFIG_XML = "/config/plugins/configurations/Jellyfin.Plugin.Wholphin.xml"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def call(method, path, token=None, body=None, device="app-admin"):
    auth = f'MediaBrowser Client="Wholphin", Device="e2e", DeviceId="{device}", Version="0.0.0"'
    if token:
        auth += f', Token="{token}"'
    h = {"Authorization": auth}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(JELLYFIN + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def wait_for_jellyfin():
    for _ in range(150):
        try:
            s, b = call("GET", "/System/Info/Public")
            if s == 200 and isinstance(b, dict) and "Version" in b:
                return b
        except OSError:
            pass
        time.sleep(2)
    sys.exit("Jellyfin did not come up")


def retry(method, path, token=None, body=None, device="app-admin"):
    for _ in range(30):
        try:
            s, b = call(method, path, token, body, device)
        except OSError as e:
            s, b = 503, str(e)
        if s != 503:
            break
        time.sleep(2)
    return s, b


def restart_jellyfin(token):
    call("POST", "/System/Restart", token)
    for _ in range(60):
        try:
            if call("GET", "/System/Info/Public")[0] != 200:
                break
        except OSError:
            break
        time.sleep(0.5)
    time.sleep(2)
    return wait_for_jellyfin()


def login(name, password, device):
    s, b = retry("POST", "/Users/AuthenticateByName", body={"Username": name, "Pw": password}, device=device)
    assert s == 200, f"login {name}: {s} {b}"
    return b["AccessToken"]


def adb(*args, check=True):
    r = subprocess.run(["docker", "exec", "-i", EMU, "/sdk/platform-tools/adb", *args], capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr.decode(errors='replace')}")
    return r.stdout


def key(*names):
    for n in names:
        adb("shell", "input", "keyevent", f"KEYCODE_{n}")
        time.sleep(0.7)


def nodes():
    """On-screen UI nodes from uiautomator: (text, resource id, left, top, focused)."""
    for _ in range(5):
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", check=False)
        xml = adb("exec-out", "cat", "/sdcard/ui.xml", check=False)
        try:
            root = ET.fromstring(xml)
            break
        except ET.ParseError:
            time.sleep(1)
    else:
        return []
    out = []
    for n in root.iter("node"):
        a = n.attrib
        x1, y1, _, _ = map(int, re.findall(r"\d+", a.get("bounds", "[0,0][0,0]")))
        out.append((a.get("text", ""), a.get("resource-id", "").split("/")[-1], x1, y1, a.get("focused") == "true"))
    return out


def wait_text(pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        found = [n for n in nodes() if n[0] and pred(n[0])]
        if found:
            return found
        time.sleep(1)
    return []


def shot(name):
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, name)
    with open(path, "wb") as f:
        f.write(adb("exec-out", "screencap", "-p"))
    print(f"     screenshot {path}", flush=True)


def row_titles(names):
    """Home row titles on screen. Nav labels start left of x=150; the focused item's title sits above y=300."""
    return [text for text, _, left, top, _ in nodes() if text in names and left >= 150 and top >= 300]


def wait_rows(names, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if row_titles(names):
            time.sleep(3)
            return True
        time.sleep(1)
    return False


def home_rows(names):
    """Titles of the home rows Wholphin shows, scrolling down through them."""
    seen = []
    key("DPAD_RIGHT")
    for _ in range(len(names) + 1):
        seen += [t for t in row_titles(names) if t not in seen]
        key("DPAD_DOWN")
    return seen


def launch(package, activity):
    for _ in range(3):
        adb("shell", "am", "start", "-W", "-n", f"{package}/{activity}", check=False)
        time.sleep(3)
        if any(rid == "tutorial_negative_action" for _, rid, _, _, _ in nodes()):
            key("DPAD_RIGHT", "DPAD_CENTER")
            continue
        return


# --- Jellyfin: users, three movie libraries, ranged rows ---------------------------------------
info = wait_for_jellyfin()
today = datetime.date.fromisoformat(
    subprocess.run(["docker", "exec", JF, "date", "+%F"], capture_output=True, text=True, check=True).stdout.strip())
print(f"Jellyfin {info['Version']} up; server date {today}", flush=True)
retry("POST", "/Startup/Configuration", body={"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"})
retry("GET", "/Startup/User")
s, b = retry("POST", "/Startup/User", body={"Name": "admin", "Password": "admin-pass"})
assert s in (200, 204), f"Startup/User {s} {b}"
retry("POST", "/Startup/RemoteAccess", body={"EnableRemoteAccess": True})
s, b = retry("POST", "/Startup/Complete")
assert s in (200, 204), f"Startup/Complete {s} {b}"
admin = login("admin", "admin-pass", "app-admin")
s, plugins = call("GET", "/Plugins", admin)
check("Wholphin plugin is Active", {p["Name"]: p["Status"] for p in plugins}.get("Wholphin") == "Active")
s, b = call("POST", "/Users/New", admin, body={"Name": "alice", "Password": "alice-pass"})
assert s == 200, f"create alice: {s} {b}"


def day(offset):
    d = today + datetime.timedelta(days=offset)
    return f"{MONTHS[d.month - 1]}{d.day}"


AROUND, TOMORROW, NORANGE = f"Around today {day(-5)}-{day(5)}", f"Starts tomorrow {day(1)}-{day(5)}", "No range"
NAMES = [AROUND, TOMORROW, NORANGE]
LIBS = [("around", AROUND, f"{day(-5)}-{day(5)}"), ("tomorrow", TOMORROW, f"{day(1)}-{day(5)}"), ("norange", NORANGE, None)]
options = {"LibraryOptions": {"EnableRealtimeMonitor": False, "MetadataSavers": [],
                              "TypeOptions": [{"Type": "Movie", "MetadataFetchers": [], "ImageFetchers": []}]}}
for folder, name, _ in LIBS:
    q = urllib.parse.urlencode({"name": name, "collectionType": "movies", "paths": f"/media/{folder}", "refreshLibrary": "false"})
    s, b = call("POST", f"/Library/VirtualFolders?{q}", admin, body=options)
    assert s in (200, 204), f"library {name}: {s} {b}"
call("POST", "/Library/Refresh", admin)
s, folders = call("GET", "/Library/VirtualFolders", admin)
ids = {f["Name"]: f["ItemId"] for f in folders}
for _ in range(90):
    counts = {n: (call("GET", f"/Items?ParentId={ids[n]}&Recursive=true&IncludeItemTypes=Movie", admin)[1] or {})
              .get("TotalRecordCount", 0) for n in NAMES}
    if all(c == 3 for c in counts.values()):
        break
    time.sleep(2)
check("Each library has its 3 movies", all(c == 3 for c in counts.values()), str(counts))

lines = ["version: 1", "rows:"]
for _, name, rng in LIBS:
    lines += ["  - type: ByParent", f"    parentId: {uuid.UUID(ids[name])}", "    recursive: true"]
    if rng:
        lines.append(f"    range: {rng}")
yaml = "\n".join(lines) + "\n"
print("--- home rows saved by the admin:\n" + yaml + "---", flush=True)
s, b = call("POST", "/wholphin/config/home", admin, body={"Value": yaml})
check("Admin saves the rows through /wholphin/config/home", s == 200, f"{s} {b}")
by_id = {v: k for k, v in ids.items()}


def served():
    token = login("alice", "alice-pass", "app-check")
    s, b = call("GET", "/wholphin/homesettings", token, device="app-check")
    return s, [by_id.get(str(r.get("parentId", "")).replace("-", "")) for r in (b or {}).get("rows", [])]


s, rows = served()
check(f"Plugin hands alice {AROUND!r} and {NORANGE!r} only", s == 200 and rows == [AROUND, NORANGE], f"{s} {rows}")

# --- The emulator and the app --------------------------------------------------------------------
adb("wait-for-device")
for _ in range(150):
    if adb("shell", "getprop", "sys.boot_completed", check=False).strip() == b"1":
        break
    time.sleep(2)
else:
    sys.exit("The emulator did not boot")
time.sleep(5)
release = adb("shell", "getprop", "ro.build.version.release").decode().strip()
print(f"Android TV {release} booted", flush=True)
out = adb("install", "-r", "/tmp/wholphin.apk", check=False).decode(errors="replace")
check("Wholphin installs on the emulator", "Success" in out, out.strip()[-300:])
package = next(p.split(":", 1)[1] for p in adb("shell", "pm", "list", "packages", "wholphin").decode().split() if p)
activity = adb("shell", "cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LEANBACK_LAUNCHER",
               package).decode().split()[-1].split("/", 1)[1]
launch(package, activity)

ok = bool(wait_text(lambda t: t == "Add Server"))
key("DPAD_CENTER")
ok = ok and bool(wait_text(lambda t: t == "Enter server address"))
key("DPAD_CENTER")
ok = ok and bool(wait_text(lambda t: t == "Enter Server IP or URL"))
adb("shell", "input", "text", "http://10.0.2.2:8096")
key("ENTER")
ok = ok and bool(wait_text(lambda t: t == "Add User"))
check("Wholphin adds the server http://10.0.2.2:8096", ok)
code = []
for _ in range(3):
    key("DPAD_CENTER")
    code = wait_text(lambda t: re.fullmatch(r"\d{6}", t) is not None, timeout=10)
    if code:
        break
alice = login("alice", "alice-pass", "app-phone")
s, b = call("POST", f"/QuickConnect/Authorize?code={code[0][0] if code else ''}", alice, device="app-phone")
check("Alice approves Wholphin's Quick Connect code", bool(code) and s == 200, f"{code} {s} {b}")
check("Wholphin signs in as alice and opens Home with rows", wait_rows(NAMES))
shot("1-home.png")
shown = home_rows(NAMES)
shot("2-home-scrolled.png")
print(f"Home rows in Wholphin: {shown}", flush=True)
check(f"Wholphin home shows {AROUND!r} then {NORANGE!r}", shown == [AROUND, NORANGE], str(shown))
check(f"Wholphin home leaves out {TOMORROW!r}", TOMORROW not in shown, str(shown))

# --- A bad range already stored (saved by an older build, or the XML edited by hand) -------------
subprocess.run(["docker", "exec", JF, "sed", "-i", f"s|<Range>{LIBS[0][2]}</Range>|<Range>oct15-octXX</Range>|", CONFIG_XML],
               check=True)
restart_jellyfin(admin)
admin = login("admin", "admin-pass", "app-admin")
s, rows = served()
check(f"After a restart with a bad range stored, the plugin hands alice {NORANGE!r} only", s == 200 and rows == [NORANGE],
      f"{s} {rows}")
logs = subprocess.run(["docker", "logs", JF], capture_output=True, text=True)
check("Jellyfin logs a warning naming the bad range",
      "Skipping home row with invalid date range: oct15-octXX" in logs.stdout + logs.stderr)
adb("shell", "am", "force-stop", package)
launch(package, activity)
check("Wholphin reopens Home with rows", wait_rows(NAMES))
shot("3-home-stored-bad-range.png")
shown = home_rows(NAMES)
print(f"Home rows in Wholphin: {shown}", flush=True)
check(f"Wholphin home shows only {NORANGE!r}, not its own default rows", shown == [NORANGE], str(shown))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)

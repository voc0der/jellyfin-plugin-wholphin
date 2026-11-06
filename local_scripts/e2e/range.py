"""Checks which rows /wholphin/homesettings hands to Wholphin today, against the container range.sh starts."""

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

JELLYFIN = "http://127.0.0.1:" + os.environ.get("JELLYFIN_PORT", "18097")
JF = os.environ.get("JF", "wholphin-range-jellyfin")
PLUGIN_ID = "00e68c90-8840-48d0-ad65-0a5d58249652"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def call(method, path, token=None, body=None, device="e2e-admin"):
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
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = raw.decode(errors="replace")
    return status, parsed


def wait_for_jellyfin():
    for _ in range(150):
        try:
            status, body = call("GET", "/System/Info/Public")
            if status == 200 and isinstance(body, dict) and "Version" in body:
                return body
        except OSError:
            pass
        time.sleep(2)
    sys.exit("Jellyfin did not come up")


def setup_call(method, path, body=None):
    for _ in range(30):
        s, b = call(method, path, body=body)
        if s != 503:
            break
        time.sleep(2)
    return s, str(b)[:200]


def login(name, password, device):
    for _ in range(30):
        s, b = call("POST", "/Users/AuthenticateByName", body={"Username": name, "Pw": password}, device=device)
        if s != 503:
            break
        time.sleep(2)
    assert s == 200, f"login {name}: {s} {b}"
    return b["AccessToken"]


def container_date():
    return subprocess.run(["docker", "exec", JF, "date", "+%F"], capture_output=True, text=True, check=True).stdout.strip()


# --- Jellyfin setup ----------------------------------------------------------------
info = wait_for_jellyfin()
TODAY = datetime.date.fromisoformat(container_date())
print(f"Jellyfin {info.get('Version')} up; server date {TODAY} ({TODAY:%a})")

setup_call("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"})
setup_call("GET", "/Startup/User")
s, b = setup_call("POST", "/Startup/User", {"Name": "admin", "Password": "admin-pass"})
assert s in (200, 204), f"Startup/User {s} {b}"
setup_call("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True})
s, b = setup_call("POST", "/Startup/Complete")
assert s in (200, 204), f"Startup/Complete {s} {b}"
admin = login("admin", "admin-pass", "e2e-admin")

s, plugins = call("GET", "/Plugins", admin)
loaded = {p["Name"]: p["Status"] for p in plugins}
check("Wholphin plugin is Active", loaded.get("Wholphin") == "Active", str(loaded))
s, b = call("POST", "/Users/New", admin, body={"Name": "alice", "Password": "alice-pass"})
assert s == 200, f"create alice: {s} {b}"
alice = login("alice", "alice-pass", "e2e-alice")


# --- Rows ------------------------------------------------------------------------
def day(offset, spaced=False):
    d = TODAY + datetime.timedelta(days=offset)
    return f"{MONTHS[d.month - 1].title()} {d.day}" if spaced else f"{MONTHS[d.month - 1]}{d.day}"


md = (TODAY.month, TODAY.day)
# (label, row type, range or None for no key, shown today?)
CASES = [
    ("no range", "ByParent", None, True),
    ("empty range", "ByParent", "''", True),
    ("README example oct15-oct31", "ByParent", "oct15-oct31", (10, 15) <= md <= (10, 31)),
    ("over new year dec20-jan5", "ByParent", "dec20-jan5", md >= (12, 20) or md <= (1, 5)),
    ("around today", "ByParent", f"{day(-5)}-{day(5)}", True),
    ("starts tomorrow", "ByParent", f"{day(1)}-{day(5)}", False),
    ("ended yesterday", "ByParent", f"{day(-5)}-{day(-1)}", False),
    ("starts today", "ByParent", f"{day(0)}-{day(5)}", True),
    ("ends today", "ByParent", f"{day(-5)}-{day(0)}", True),
    ("today only", "ByParent", day(0), True),
    ("tomorrow only", "ByParent", day(1), False),
    ("wraps the year, today is its first day", "ByParent", f"{day(0)}-{day(-1)}", True),
    ("wraps the year, today is its last day", "ByParent", f"{day(1)}-{day(0)}", True),
    ("wraps the year, leaves out only today", "ByParent", f"{day(1)}-{day(-1)}", False),
    ("whole year ending feb29 (non-leap years too)", "ByParent", "mar1-feb29", True),
    ("feb1-feb29", "ByParent", "feb1-feb29", TODAY.month == 2),
    ("upper case, spaces round the dash", "ByParent", f"{day(-5).upper()} - {day(5).upper()}", True),
    ("'Oct 18 - Oct 28' style", "ByParent", f"{day(-5, True)} - {day(5, True)}", True),
    ("NextUp row, starts tomorrow", "NextUp", f"{day(1)}-{day(5)}", False),
    ("ContinueWatchingCombined row, around today", "ContinueWatchingCombined", f"{day(-5)}-{day(5)}", True),
    ("CustomEndpoint row, ended yesterday", "CustomEndpoint", f"{day(-5)}-{day(-1)}", False),
]


def key_of(row):
    t = row.get("type")
    if t == "ByParent":
        return "p" + str(row.get("parentId", "")).replace("-", "").lower()
    if t == "CustomEndpoint":
        return "t" + row.get("title", "")
    return "y" + str(t)


def yaml_rows(cases):
    lines = ["version: 1", "rows:"]
    keys = []
    for i, (label, kind, rng, _) in enumerate(cases):
        lines.append(f"  - type: {kind}")
        if kind == "ByParent":
            guid = f"00000000-0000-0000-0000-{i + 1:012d}"
            lines += [f"    parentId: {guid}", "    recursive: false"]
            keys.append("p" + guid.replace("-", ""))
        elif kind == "CustomEndpoint":
            lines += [f"    title: {label}", "    endpoint: /HomeScreen/Section/Test"]
            keys.append("t" + label)
        else:
            keys.append("y" + kind)
        if rng is not None:
            lines.append(f"    range: {rng}")
    return "\n".join(lines) + "\n", keys


def shown(token, device):
    s, body = call("GET", "/wholphin/homesettings", token, device=device)
    rows = (body or {}).get("rows") if isinstance(body, dict) else None
    return s, body, [key_of(r) for r in rows or []], rows


def check_filtering(prefix, cases, keys, token, device):
    s, body, got, rows = shown(token, device)
    check(f"{prefix}: homesettings answers 200 with version and rows", s == 200 and body.get("version") == 1 and rows is not None,
          f"{s} {str(body)[:300]}")
    for (label, _, rng, expected), k in zip(cases, keys):
        check(f"{prefix}: {label!r} [{rng}] {'shown' if expected else 'hidden'}", (k in got) == expected,
              f"returned keys {got}")
    want = [k for (_, _, _, e), k in zip(cases, keys) if e]
    check(f"{prefix}: shown rows keep the configured order", got == want, f"{got} vs {want}")
    check(f"{prefix}: every returned row still carries its type discriminator", all("type" in r for r in rows or []))
    return rows


yaml, keys = yaml_rows(CASES)
print("--- YAML saved by the admin:\n" + yaml + "---")
s, b = call("POST", "/wholphin/config/home", admin, body={"Value": yaml})
check("Admin saves the YAML through /wholphin/config/home", s == 200, f"{s} {b}")

rows = check_filtering("alice", CASES, keys, alice, "e2e-alice")
print("alice's rows as Wholphin receives them:", json.dumps(rows)[:600])
check_filtering("admin", CASES, keys, admin, "e2e-admin")

s, b = call("GET", "/wholphin/config/home", admin)
saved = (b or {}).get("Result") or ""
missing = [rng for _, _, rng, _ in CASES if rng not in (None, "''") and f"range: {rng}" not in saved]
row_count = sum(1 for line in saved.splitlines() if line.startswith("- "))
check("Admin YAML (/wholphin/config/home) still has every row and every range", s == 200 and row_count == len(CASES) and not missing,
      f"{s} rows={row_count} missing={missing}\n{saved}")

# --- The settings page's save path: Jellyfin's standard plugin configuration endpoint (JSON) --------
s, cfg = call("GET", f"/Plugins/{PLUGIN_ID}/Configuration", admin)
cfg_rows = cfg["HomeConfig"]["HomePageSettings"]["rows"]
check("Plugin configuration JSON (used by the settings page) includes range", s == 200
      and sum(1 for r in cfg_rows if r.get("range") not in (None, "")) == sum(1 for c in CASES if c[2] not in (None, "''")),
      f"{s} {json.dumps(cfg_rows)[:400]}")
idx = next(i for i, c in enumerate(CASES) if c[0] == "starts tomorrow")
cfg_rows[idx]["range"] = f"{day(-1)}-{day(1)}"
s, b = call("POST", f"/Plugins/{PLUGIN_ID}/Configuration", admin, body=cfg)
check("Admin saves a changed range through the plugin configuration endpoint", s in (200, 204), f"{s} {b}")
CASES2 = list(CASES)
CASES2[idx] = ("starts tomorrow -> changed to yesterday..tomorrow", "ByParent", f"{day(-1)}-{day(1)}", True)
check_filtering("after JSON save", CASES2, keys, alice, "e2e-alice")

# --- Persistence: restart Jellyfin, the XML config must keep the ranges -------------------------
subprocess.run(["docker", "restart", JF], capture_output=True, check=True)
time.sleep(3)
wait_for_jellyfin()
alice = login("alice", "alice-pass", "e2e-alice2")
admin = login("admin", "admin-pass", "e2e-admin2")
check_filtering("after restart", CASES2, keys, alice, "e2e-alice2")
s, b = call("GET", "/wholphin/config/home", admin, device="e2e-admin2")
check("After restart the admin YAML still has every range",
      s == 200 and (b or {}).get("Result", "").count("range:") == sum(1 for c in CASES2 if c[2] is not None),
      f"{s} {(b or {}).get('Result')}")

# --- Invalid ranges: both save paths refuse them; Wholphin keeps getting the old rows --------------
BAD = "oct15-octXX"
SEERR_URL = "https://seerr.example.com"
_, _, before, _ = shown(alice, "e2e-alice2")
s, b = call("POST", "/wholphin/config/home", admin, body={"Value": f"version: 1\nrows:\n  - type: NextUp\n    range: {BAD}\n"},
            device="e2e-admin2")
check("Invalid range: /wholphin/config/home refuses it with a 400 naming the row",
      s == 400 and f"Home row 1 has an invalid range '{BAD}'" in str(b), f"{s} {b}")
s, cfg = call("GET", f"/Plugins/{PLUGIN_ID}/Configuration", admin, device="e2e-admin2")
cfg["HomeConfig"]["HomePageSettings"]["rows"][0]["range"] = BAD
s, b = call("POST", f"/Plugins/{PLUGIN_ID}/Configuration", admin, body=cfg, device="e2e-admin2")
print(f"The settings page's save of {BAD} answers {s} {b!r}")
check("Invalid range: the plugin configuration endpoint (settings page) refuses it with a 400", s == 400, f"{s} {b}")
s, _, after, _ = shown(alice, "e2e-alice2")
check("Invalid range: after the refused saves Wholphin gets the same rows", s == 200 and after == before,
      f"{s} {after} vs {before}")
s, b = call("POST", "/wholphin/config/seerr", admin, device="e2e-admin2",
            body={"Value": f"serverUrl: {SEERR_URL}\nlogin:\n  type: ApiKey\n  apiKey: e2e-key\n"})
check("Seerr settings save while every stored range is valid", s == 200, f"{s} {b}")
s, b = call("GET", "/wholphin/seerrsettings", alice, device="e2e-alice2")
check("Wholphin gets the saved Seerr settings", s == 200 and isinstance(b, dict) and b.get("serverUrl") == SEERR_URL, f"{s} {b}")

# --- A bad range already stored (saved by an older build, or the XML edited by hand) -------------
CONFIG_XML = "/config/plugins/configurations/Jellyfin.Plugin.Wholphin.xml"
idx = next(i for i, c in enumerate(CASES2) if c[0] == "starts today")
good = CASES2[idx][2]
subprocess.run(["docker", "exec", JF, "sed", "-i", f"s|<Range>{good}</Range>|<Range>{BAD}</Range>|", CONFIG_XML], check=True)
subprocess.run(["docker", "restart", JF], capture_output=True, check=True)
time.sleep(3)
wait_for_jellyfin()
alice = login("alice", "alice-pass", "e2e-alice3")
admin = login("admin", "admin-pass", "e2e-admin3")
stored = subprocess.run(["docker", "exec", JF, "cat", CONFIG_XML], capture_output=True, text=True).stdout
check(f"Stored invalid range: row {idx + 1} has {BAD} in the XML config Jellyfin loaded", f"<Range>{BAD}</Range>" in stored)
CASES3 = list(CASES2)
CASES3[idx] = (f"starts today -> {BAD} in the stored XML", "ByParent", BAD, False)
check_filtering("stored invalid range", CASES3, keys, alice, "e2e-alice3")
logs = subprocess.run(["docker", "logs", JF], capture_output=True, text=True)
check("Stored invalid range: Jellyfin logs a warning naming it",
      f"Skipping home row with invalid date range: {BAD}" in logs.stdout + logs.stderr)
s, b = call("POST", "/wholphin/config/seerr", admin, body={"Value": f"serverUrl: {SEERR_URL}\n"}, device="e2e-admin3")
check(f"Stored invalid range: Seerr save is refused with a 400 naming row {idx + 1}",
      s == 400 and f"Home row {idx + 1} has an invalid range '{BAD}'" in str(b), f"{s} {b}")
s, cfg = call("GET", f"/Plugins/{PLUGIN_ID}/Configuration", admin, device="e2e-admin3")
cfg["HomeConfig"]["HomePageSettings"]["rows"][idx]["range"] = good
s, b = call("POST", f"/Plugins/{PLUGIN_ID}/Configuration", admin, body=cfg, device="e2e-admin3")
check("Stored invalid range: fixing it through the settings page saves", s in (200, 204), f"{s} {b}")
s, _, after, _ = shown(alice, "e2e-alice3")
check("Stored invalid range: after the fix Wholphin gets every row again", s == 200 and after == before, f"{s} {after} vs {before}")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)

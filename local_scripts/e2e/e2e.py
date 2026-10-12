"""Plays the Wholphin client's HTTP calls against the containers run.sh starts."""

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

JELLYFIN = "http://127.0.0.1:" + os.environ.get("JELLYFIN_PORT", "18096")
SEERR = "http://127.0.0.1:" + os.environ.get("SEERR_PORT", "15055")
SERVER_URL = JELLYFIN + "/Plugins/SeerrProxy"
PROXY_ID = "1ac3cf0f-f0f9-443a-be08-be38e48ff683"

results = []
seerr_session = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def call(method, url, token=None, body=None, device="e2e-admin", headers=None, jellyfin_auth=True, opener=None):
    h = {}
    if jellyfin_auth:
        auth = f'MediaBrowser Client="Wholphin", Device="e2e", DeviceId="{device}", Version="0.0.0"'
        if token:
            auth += f', Token="{token}"'
        h["Authorization"] = auth
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url if url.startswith("http") else JELLYFIN + url, data=data, method=method, headers=h)
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=60) as r:
            raw, status, hdrs = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read(), e.code, e.headers
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = raw.decode(errors="replace")
    return status, hdrs, parsed


def wait_for(url, what, ready):
    for _ in range(150):
        try:
            status, _, body = call("GET", url, jellyfin_auth=False)
            if status == 200 and ready(body):
                return body
        except OSError:
            pass
        time.sleep(2)
    sys.exit(f"{what} did not come up")


def setup_call(method, path, body=None):
    for _ in range(30):
        s, _, b = call(method, path, body=body)
        if s != 503:
            break
        time.sleep(2)
    return s, str(b)[:200]


def login(name, password, device):
    s, _, b = call("POST", "/Users/AuthenticateByName", body={"Username": name, "Pw": password}, device=device)
    assert s == 200, f"login {name}: {s} {b}"
    return b["AccessToken"]


# --- Jellyfin setup ----------------------------------------------------------------
info = wait_for(JELLYFIN + "/System/Info/Public", "Jellyfin", lambda b: isinstance(b, dict) and "Version" in b)
status = wait_for(SEERR + "/api/v1/status", "Seerr", lambda b: isinstance(b, dict) and "version" in b)
print(f"Jellyfin {info.get('Version')} up, Seerr {status.get('version')} ({status.get('commitTag')}) up")

setup_call("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"})
setup_call("GET", "/Startup/User")
s, b = setup_call("POST", "/Startup/User", {"Name": "admin", "Password": "admin-pass"})
assert s in (200, 204), f"Startup/User {s} {b}"
setup_call("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True})
s, b = setup_call("POST", "/Startup/Complete")
assert s in (200, 204), f"Startup/Complete {s} {b}"
admin = login("admin", "admin-pass", "e2e-admin")

s, _, plugins = call("GET", "/Plugins", admin)
loaded = {p["Name"]: p["Status"] for p in plugins}
check("Wholphin plugin is Active", loaded.get("Wholphin") == "Active", str(loaded))
check("Seerr Proxy plugin is Active", loaded.get("Seerr Proxy") == "Active", str(loaded))

jellyfin_ids = {}
for name in ("alice", "bob"):
    s, _, b = call("POST", "/Users/New", admin, body={"Name": name, "Password": name + "-pass"})
    assert s == 200, f"create {name}: {s} {b}"
    jellyfin_ids[name] = b["Id"].replace("-", "").lower()

s, _, b = call("POST", "/wholphin/config/seerr", admin,
               body={"Value": f"serverUrl: {SERVER_URL}\nlogin:\n  type: JellyfinToken\n"})
check("Admin saves JellyfinToken YAML", s == 200, f"{s} {b}")

# --- Real Seerr setup: Jellyfin admin becomes Seerr admin, import alice and bob ----
s, _, b = call("POST", SEERR + "/api/v1/auth/jellyfin", jellyfin_auth=False, opener=seerr_session, body={
    "username": "admin", "password": "admin-pass", "hostname": "jellyfin", "port": 8096,
    "useSsl": False, "urlBase": "", "email": "admin@example.com", "serverType": 2})
check("Seerr linked to Jellyfin (admin signed in)", s == 200, f"{s} {b}")
s, _, main = call("GET", SEERR + "/api/v1/settings/main", jellyfin_auth=False, opener=seerr_session)
seerr_api_key = (main or {}).get("apiKey")
check("Seerr API key retrieved", s == 200 and seerr_api_key, f"{s} {main}")
s, _, imported = call("POST", SEERR + "/api/v1/user/import-from-jellyfin", jellyfin_auth=False, opener=seerr_session,
                      body={"jellyfinUserIds": list(jellyfin_ids.values())})
check("Seerr imported alice and bob from Jellyfin", s in (200, 201) and isinstance(imported, list) and len(imported) == 2,
      f"{s} {imported}")
seerr_ids = {}
for u in imported or []:
    for name, jid in jellyfin_ids.items():
        if str(u.get("jellyfinUserId", "")).replace("-", "").lower() == jid:
            seerr_ids[name] = u["id"]
print(f"Seerr user ids: {seerr_ids}")

s, _, cfg = call("GET", f"/Plugins/{PROXY_ID}/Configuration", admin)
for key in list(cfg):
    if key.lower() == "enabled":
        cfg[key] = True
    elif key.lower() == "seerrbaseurl":
        cfg[key] = "http://seerr:5055"
    elif key.lower() == "seerrapikey":
        cfg[key] = seerr_api_key
s, _, b = call("POST", f"/Plugins/{PROXY_ID}/Configuration", admin, body=cfg)
check("Proxy configured with real Seerr URL and API key", s in (200, 204), f"{s} {b}")

# --- The flow, per user, exactly as Wholphin does it --------------------------------
tokens = {}
for name in ("alice", "bob"):
    token = login(name, name + "-pass", f"e2e-{name}")
    tokens[name] = token

    s, hdrs, settings = call("GET", "/wholphin/seerrsettings", token, device=f"e2e-{name}")
    login_ = (settings or {}).get("login") or {}
    check(f"{name}: seerrsettings returns an ApiKey login with {name}'s own token",
          s == 200 and login_.get("type") == "ApiKey" and login_.get("apiKey") == token
          and settings.get("serverUrl") == SERVER_URL, f"{s} {settings}")

    api = settings["serverUrl"] + "/api/v1"
    s, _, me = call("GET", api + "/auth/me", jellyfin_auth=False, headers={"X-Api-Key": login_["apiKey"]})
    check(f"{name}: real Seerr answers auth/me as {name} (Seerr user {seerr_ids.get(name)})",
          s == 200 and isinstance(me, dict) and me.get("id") == seerr_ids.get(name)
          and str(me.get("jellyfinUserId", "")).replace("-", "").lower() == jellyfin_ids[name], f"{s} {me}")

    s, _, reqs = call("GET", api + "/request", jellyfin_auth=False, headers={"X-Api-Key": login_["apiKey"]})
    check(f"{name}: a second proxied call (GET request) works", s == 200 and isinstance(reqs, dict) and "results" in reqs,
          f"{s} {str(reqs)[:200]}")

# --- Negative paths ----------------------------------------------------------------
s, _, b = call("GET", SEERR + "/api/v1/auth/me", jellyfin_auth=False, headers={"X-Api-Key": tokens["alice"]})
check("Seerr itself rejects a Jellyfin token sent directly", s in (401, 403), f"{s} {b}")

call("POST", "/Auth/Keys?app=e2e", admin)
s, _, keys = call("GET", "/Auth/Keys", admin)
api_key = next(k["AccessToken"] for k in keys["Items"] if k.get("AppName") == "e2e")
s, _, b = call("GET", "/wholphin/seerrsettings", api_key, device="e2e-apikey")
check("Jellyfin server API key gets 403 from seerrsettings", s == 403, f"{s} {b}")
s, _, b = call("GET", SERVER_URL + "/api/v1/auth/me", jellyfin_auth=False, headers={"X-Api-Key": api_key})
check("Jellyfin server API key gets 401 from the proxy", s == 401, f"{s} {b}")

s, _, b = call("POST", "/Sessions/Logout", tokens["alice"], device="e2e-alice")
s, _, b = call("GET", SERVER_URL + "/api/v1/auth/me", jellyfin_auth=False, headers={"X-Api-Key": tokens["alice"]})
check("After alice signs out, her old token gets 401 from the proxy", s == 401, f"{s} {b}")
s, _, me = call("GET", SERVER_URL + "/api/v1/auth/me", jellyfin_auth=False, headers={"X-Api-Key": tokens["bob"]})
check("Bob is unaffected by alice signing out", s == 200 and isinstance(me, dict) and me.get("id") == seerr_ids.get("bob"),
      f"{s} {me}")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)

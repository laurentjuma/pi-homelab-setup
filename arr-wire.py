#!/usr/bin/env python3
"""
Wire the CT 107 *arr stack together — root folders, download clients, and the
Prowlarr -> Sonarr/Radarr links. Section 12e of the guide.

Idempotent: every step checks for what it would create first, so re-running
after a rebuild (or after adding one service) changes nothing else.

Run it inside CT 107, where 127.0.0.1 reaches every published port and the
API keys are readable on disk:

    QB_PASSWORD='...' python3 arr-wire.py

The API keys are read from each app's config.xml rather than passed in — they
are generated on first start and that file is the only place they are canonical.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HOST = os.environ.get("ARR_HOST", "127.0.0.1")
QB_PASSWORD = os.environ.get("QB_PASSWORD")

# app -> (published port, API version, path to its config.xml)
APPS = {
    "sonarr":   (8989, "v3", "/opt/arr/sonarr/config.xml"),
    "radarr":   (7878, "v3", "/opt/arr/radarr/config.xml"),
    "prowlarr": (9696, "v1", "/opt/arr/prowlarr/config.xml"),
}

# Service names on the `arrnet` bridge, NOT IPs: these are the addresses the
# containers use to reach each other, and they only resolve inside that network.
QB_HOST, QB_PORT = "qbittorrent", 8080


def api_key(app):
    path = APPS[app][2]
    try:
        m = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(path).read())
    except FileNotFoundError:
        sys.exit(f"{path} not found — has {app} started at least once?")
    if not m:
        sys.exit(f"no <ApiKey> in {path}")
    return m.group(1)


KEYS = {app: api_key(app) for app in APPS}


def call(app, path, method="GET", body=None):
    port, ver, _ = APPS[app]
    req = urllib.request.Request(
        f"http://{HOST}:{port}/api/{ver}/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"X-Api-Key": KEYS[app], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            # A 200 with an empty body (the /test endpoints) is a success, so it
            # must not come back as the same value an error does.
            return json.loads(raw) if raw else True
    except urllib.error.HTTPError as e:
        print(f"  !! {app} {method} {path} -> {e.code} {e.read()[:300].decode(errors='replace')}")
        return None


def fill(template, values):
    """Overlay values onto a schema's field list, leaving defaults alone."""
    template["fields"] = [
        {**f, "value": values.get(f["name"], f.get("value"))} for f in template["fields"]
    ]
    return template


def root_folder(app, path):
    if any(r["path"] == path for r in call(app, "rootfolder") or []):
        return print(f"  {app}: root folder {path} already present")
    ok = call(app, "rootfolder", "POST", {"path": path})
    print(f"  {app}: root folder {path} -> {'OK' if ok else 'FAILED'}")


def download_client(app, category):
    if any(c["name"] == "qBittorrent" for c in call(app, "downloadclient") or []):
        return print(f"  {app}: download client already present")
    if not QB_PASSWORD:
        sys.exit("QB_PASSWORD is not set — needed to create the download client")
    schema = next(
        (s for s in call(app, "downloadclient/schema") or []
         if s.get("implementation") == "QBittorrent"), None)
    if not schema:
        return print(f"  {app}: no QBittorrent schema offered")
    body = fill(schema, {"host": QB_HOST, "port": QB_PORT, "username": "admin",
                         "password": QB_PASSWORD, "category": category, "useSsl": False})
    body.update({"name": "qBittorrent", "enable": True, "priority": 1})
    print(f"  {app}: qBittorrent (category={category}) -> "
          f"{'OK' if call(app, 'downloadclient', 'POST', body) else 'FAILED'}")


def prowlarr_app(app, port):
    impl = app.capitalize()
    if any(a["name"] == impl for a in call("prowlarr", "applications") or []):
        return print(f"  prowlarr: {impl} already linked")
    schema = next(
        (s for s in call("prowlarr", "applications/schema") or []
         if s.get("implementation") == impl), None)
    if not schema:
        return print(f"  prowlarr: no {impl} schema offered")
    body = fill(schema, {"prowlarrUrl": f"http://prowlarr:{APPS['prowlarr'][0]}",
                         "baseUrl": f"http://{app}:{port}", "apiKey": KEYS[app]})
    # fullSync: indexers are managed in Prowlarr only and pushed down from there.
    body.update({"name": impl, "syncLevel": "fullSync"})
    print(f"  prowlarr: link {impl} -> "
          f"{'OK' if call('prowlarr', 'applications', 'POST', body) else 'FAILED'}")


print("== root folders ==")
root_folder("sonarr", "/data/tv")
root_folder("radarr", "/data/movies")

print("== download clients ==")
download_client("sonarr", "tv-sonarr")
download_client("radarr", "radarr")

print("== prowlarr applications ==")
prowlarr_app("sonarr", 8989)
prowlarr_app("radarr", 7878)

print("== verify ==")
for app in ("sonarr", "radarr"):
    roots = call(app, "rootfolder") or []
    clients = call(app, "downloadclient") or []
    for c in clients:
        # A saved client proves nothing; the test endpoint proves it can log in.
        ok = call(app, "downloadclient/test", "POST", c) is not None
        print(f"  {app}: '{c['name']}' reachable={'yes' if ok else 'NO'}")
    for r in roots:
        print(f"  {app}: {r['path']} accessible={r.get('accessible')}")
apps = call("prowlarr", "applications") or []
print(f"  prowlarr: {[(a['name'], a['syncLevel']) for a in apps]}")
print("\nIndexers are intentionally not configured here — add them in Prowlarr;")
print("fullSync pushes them to Sonarr and Radarr on its own.")

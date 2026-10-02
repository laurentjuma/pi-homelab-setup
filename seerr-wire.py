#!/usr/bin/env python3
"""Register Radarr and Sonarr in Seerr. Run inside CT 107. Safe to re-run."""
import json, re, sys, urllib.request, urllib.error

DRY = "--dry" in sys.argv
SEERR = "http://127.0.0.1:5055/api/v1"
SEERR_KEY = json.load(open("/opt/arr/seerr/settings.json"))["main"]["apiKey"]

def arr_key(app):
    return re.search(r"<ApiKey>([^<]+)", open(f"/opt/arr/{app}/config.xml").read()).group(1)

def call(path, body=None, method=None):
    req = urllib.request.Request(SEERR + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": SEERR_KEY, "Content-Type": "application/json"},
        method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read(); return r.status, json.loads(t) if t else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]

# Inside arrnet the apps are reachable by service name; externalUrl is what
# Seerr's UI links to, so that one is the LAN address.
APPS = {
    "radarr": dict(port=7878, profile="HD Bluray + WEB", root="/data/movies",
                   extra={"minimumAvailability": "released"}),
    "sonarr": dict(port=8989, profile="WEB-1080p", root="/data/tv",
                   extra={"seriesType": "standard", "animeSeriesType": "anime", "enableSeasonFolders": True}),
}

for app, c in APPS.items():
    st, existing = call(f"/settings/{app}")
    if st == 200 and existing:
        print(app, "already configured:", [(s["id"], s["name"]) for s in existing]); continue
    base = dict(hostname=app, port=c["port"], apiKey=arr_key(app), useSsl=False, baseUrl="")
    st, test = call(f"/settings/{app}/test", base)
    if st != 200:
        sys.exit(f"{app} test failed: {st} {test}")
    profiles = {p["name"]: p["id"] for p in test["profiles"]}
    roots = [r["path"] for r in test["rootFolders"]]
    print(app, "profiles:", list(profiles), "roots:", roots)
    if c["profile"] not in profiles or c["root"] not in roots:
        sys.exit(f"{app}: expected profile {c['profile']!r} / root {c['root']!r} not offered")
    body = dict(base, name=app.capitalize(), is4k=False, isDefault=True,
                activeProfileId=profiles[c["profile"]], activeProfileName=c["profile"],
                activeDirectory=c["root"], tags=[], syncEnabled=True, preventSearch=False,
                tagRequests=False, externalUrl=f"http://192.168.8.219:{c['port']}", **c["extra"])
    if app == "sonarr":
        body.update(activeAnimeProfileId=profiles[c["profile"]], activeAnimeProfileName=c["profile"],
                    activeAnimeDirectory=c["root"])
    if DRY:
        print(app, "would create:", {k: v for k, v in body.items() if k != "apiKey"}); continue
    st, res = call(f"/settings/{app}", body)
    print(app, "create:", st, res.get("id") if isinstance(res, dict) else res)

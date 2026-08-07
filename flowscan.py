#!/usr/bin/env python3
"""Poll a 90s90s/loverad.io "iris" flow.json feed and log every track change.

Companion to icy_meta.py for stations whose ICY StreamTitle is static (90s90s
sends only the station name). The feed always returns just the currently
playing track -- offset/count are accepted but ignored -- so there is no
history to backfill and we have to sample often enough not to miss anything.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

FEED = "https://iris-90s90s.loverad.io/flow.json"
UA = "flowscan/1.0 (+self-hosted track logger)"


def warn(msg):
    """Non-track output goes to stdout, which the unit routes to the journal."""
    print(f"Warning: {msg}", flush=True)


def fetch(station, timeout):
    url = f"{FEED}?station={station}&offset=1&count=1&ts={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse(payload):
    """-> (line, ok). Returns the formatted log line for the current track."""
    result = payload.get("result") or {}
    if result.get("found") in (None, "0", 0):
        return None, False

    entries = result.get("entry") or []
    if not entries:
        return None, False
    entry = entries[0]

    songs = (entry.get("song") or {}).get("entry") or []
    if not songs:
        return None, False
    song = songs[0]

    title = (song.get("title") or "").strip()
    if not title:
        return None, False

    artists = (song.get("artist") or {}).get("entry") or []
    artist = (artists[0].get("name") if artists else "").strip() or "Unknown Artist"

    # airtime is the broadcast time (Berlin, +02:00); render it in the Pi's
    # local zone so these lines sort alongside the icy_meta.py logs.
    airtime = entry.get("airtime")
    try:
        stamp = datetime.fromisoformat(airtime).astimezone()
    except (TypeError, ValueError):
        stamp = datetime.now().astimezone()

    return f"{stamp:%Y-%m-%d %H:%M:%S}: {artist} - {title}", True


def last_logged_line(path):
    """Seed dedup from the log so a restart doesn't re-log the current track."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        return lines[-1] if lines else None
    except FileNotFoundError:
        return None
    except OSError as exc:
        warn(f"could not read {path} to seed dedup: {exc}")
        return None


def append(path, line):
    """Open/close per write, mirroring the awk `close(LOG)` trick in the icyscan
    units: the reopen re-resolves the path, so if something over the SMB share
    deletes or replaces the file, the next track recreates it instead of
    writing into an orphaned inode."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", required=True, help="iris station id, e.g. 265")
    ap.add_argument("--log", required=True, help="path to the track log")
    ap.add_argument("--interval", type=int, default=60, help="poll seconds")
    ap.add_argument("--timeout", type=int, default=15, help="http timeout seconds")
    args = ap.parse_args()

    last = last_logged_line(args.log)
    if last:
        print(f"Resuming; last logged line: {last}", flush=True)

    while True:
        try:
            line, ok = parse(fetch(args.station, args.timeout))
            if not ok:
                warn(f"no current track for station {args.station}")
            elif line != last:
                append(args.log, line)
                last = line
        except urllib.error.HTTPError as exc:
            warn(f"HTTP {exc.code} from feed")
        except urllib.error.URLError as exc:
            warn(f"feed unreachable: {exc.reason}")
        except json.JSONDecodeError:
            warn("feed returned invalid JSON")
        except OSError as exc:
            warn(f"could not write {args.log}: {exc}")
        except Exception as exc:  # keep the loop alive whatever happens
            warn(f"unexpected {type(exc).__name__}: {exc}")

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

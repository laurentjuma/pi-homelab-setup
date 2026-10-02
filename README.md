# Raspberry Pi 5 Homelab — Rebuild Guide

Step-by-step notes for rebuilding a Raspberry Pi 5 homelab from a blank SD card, captured from the live machine rather than written from memory.

**📖 Read it here: https://laurentjuma.github.io/pi-homelab-setup/**

## What it covers

| # | Section |
|---|---|
| 1 | Base OS — Raspberry Pi OS / Debian 13 trixie, SSH keys, passwordless sudo |
| 2 | NVMe — PCIe Gen 3, EEPROM boot order, partition → LVM → ext4 → `/mnt/nvme` |
| 3 | Samba file share, plus the same tree read-only over WebDAV (`rclone serve webdav`, `:8081`) |
| 4 | PXVIRT — the Lierfang ARM64 port of Proxmox VE 9 (upstream has no arm64 build) |
| 5 | CT 100 — Music Assistant in Docker, with the library bind-mounted read-only |
| 6 | CT 101 — AzuraCast |
| 7 | CT 102 — Audiobookshelf |
| 8 | CT 103 — Plex |
| 9 | CT 104 — Emby |
| 10 | CT 105 — goPodder, a gpodder.net-compatible podcast sync server |
| 11 | CT 106 — m3ugoat, an IPTV playlist/EPG manager; the one container with no Docker |
| 12 | CT 107 — the *arr stack: qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr, Recyclarr, hardlinked off one `/data` root, plus Seerr for requests |
| 13 | CT 108 — Jellyfin |
| 14 | CT 109 — Kodi, headless under Xvfb, serving music, movies and TV over JSON-RPC to Symfonium/Yatse/Kore |
| 15 | Navidrome — plus the `nd-lyrics` plugin and why libraries must never overlap |
| 16 | Radio track logging — ICY metadata scanners, plus `flowscan` for stations that don't publish over ICY |
| 17 | Tailscale — subnet router, for a LAN behind CGNAT with no inbound path |
| 18–19 | Verification checklist and rebuild gotchas |

## `flowscan.py`

A small stdlib-only poller for stations whose ICY `StreamTitle` is just the station name. It reads the web player's own metadata API instead and appends each track change to a log. Covered in section 16c; runs as a systemd template unit where the instance name is the station ID.

## `arr-wire.py`

Wires the CT 107 stack together over the apps' own APIs — root folders, qBittorrent as the download client in Sonarr and Radarr, and both of them registered in Prowlarr. Covered in section 12e; run it inside CT 107 with `QB_PASSWORD` set. Every step checks before it creates, so it is safe to re-run after a rebuild.

Indexers are deliberately left out: add those in Prowlarr, and Full Sync pushes them down on its own.

## `seerr-wire.py`

Registers Radarr and Sonarr in Seerr with the Recyclarr profiles and the `/data` root folders, after the first-run wizard has signed in to Jellyfin. Covered in section 12h; run it inside CT 107. Seerr tests each connection before it's saved, and an app that's already set up is left alone. `--dry` prints what it would create.

## Placeholders

Values specific to my setup are replaced — substitute your own:

- `youruser` — the Linux/Samba account (uid 1000)
- `YOUR_TAILNET_IP` — Tailscale 100.x address
- `your-tailnet` — tailnet name in the MagicDNS hostname
- `your-tailscale-account` — the account the node logs in as

LAN addressing (`192.168.8.x`) is left concrete; adjust it to your own network.

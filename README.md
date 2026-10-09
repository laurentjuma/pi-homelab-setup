# Raspberry Pi 5 Homelab — Rebuild Guide

Step-by-step notes for rebuilding a Raspberry Pi 5 homelab from a blank SD card, captured from the live machine rather than written from memory.

**📖 Read it here: https://laurentjuma.github.io/pi-homelab-setup/**

## What it covers

| # | Section |
|---|---|
| 0 | What's on the box |
| 1 | Base OS — Raspberry Pi OS / Debian 13 trixie, SSH keys, passwordless sudo |
| 2 | NVMe — PCIe Gen 3, EEPROM boot order, partition → LVM → ext4 → `/mnt/nvme` |
| 3 | Samba file share, plus the same tree read-only over WebDAV (`rclone serve webdav`, `:8081`) |
| 4 | Mediagg Arr Stack in Docker on the host — Jellyfin, Navidrome, Audiobookshelf, Sonarr, Radarr, Lidarr, Bazarr, Prowlarr, qBittorrent, Seerr, Hearr |
| 5 | Navidrome on the host (disabled) — plus the `nd-lyrics` plugin and why libraries must never overlap |
| 6 | Radio track logging — ICY metadata scanners, plus `flowscan` for stations that don't publish over ICY |
| 7 | Tailscale — subnet router, for a LAN behind CGNAT with no inbound path |
| 8–9 | Verification checklist, rebuild gotchas, and the `hqdash` dashboard |

### Archived: the Proxmox era

Until 2026-10-09 the Pi ran **PXVIRT** (the ARM64 port of Proxmox VE 9) with a set of LXC containers — Music Assistant, AzuraCast, goPodder, m3ugoat, headless Kodi, and the Mediagg stack in CT 110. All of it has been removed. How it was built, and how it was taken out, is in [`proxmox-archive.md`](proxmox-archive.md) ([on the site](https://laurentjuma.github.io/pi-homelab-setup/proxmox-archive.html)).

## `flowscan.py`

A small stdlib-only poller for stations whose ICY `StreamTitle` is just the station name. It reads the web player's own metadata API instead and appends each track change to a log. Covered in section 6c; runs as a systemd template unit where the instance name is the station ID.

## `arr-wire.py` (archived)

**From the CT 107 era, before Mediagg — kept for reference.** Wires the CT 107 stack together over the apps' own APIs — root folders, qBittorrent as the download client in Sonarr, Radarr and Lidarr, and all three registered in Prowlarr. Covered in sections 12e and 12i; run it inside CT 107 with `QB_PASSWORD` set. Every step checks before it creates, so it is safe to re-run after a rebuild.

Indexers are deliberately left out: add those in Prowlarr, and Full Sync pushes them down on its own.

## `seerr-wire.py` (archived)

**From the CT 107 era, before Mediagg — kept for reference.** Registers Radarr and Sonarr in Seerr with the Recyclarr profiles and the `/data` root folders, after the first-run wizard has signed in to Jellyfin. Covered in section 12h; run it inside CT 107. Seerr tests each connection before it's saved, and an app that's already set up is left alone. `--dry` prints what it would create.

## Placeholders

Values specific to my setup are replaced — substitute your own:

- `youruser` — the Linux/Samba account (uid 1000)
- `YOUR_TAILNET_IP` — Tailscale 100.x address
- `your-tailnet` — tailnet name in the MagicDNS hostname
- `your-tailscale-account` — the account the node logs in as

LAN addressing (`192.168.8.x`) is left concrete; adjust it to your own network.

# Raspberry Pi 5 Homelab — Rebuild Guide

Step-by-step notes for rebuilding a Raspberry Pi 5 homelab from a blank SD card, captured from the live machine rather than written from memory.

**📖 Read it here: https://laurentjuma.github.io/pi-homelab-setup/**

## What it covers

| # | Section |
|---|---|
| 1 | Base OS — Raspberry Pi OS / Debian 13 trixie, SSH keys, passwordless sudo |
| 2 | NVMe — PCIe Gen 3, EEPROM boot order, partition → LVM → ext4 → `/mnt/nvme` |
| 3 | Samba file share |
| 4 | PXVIRT — the Lierfang ARM64 port of Proxmox VE 9 (upstream has no arm64 build) |
| 5 | LXC container: Music Assistant in Docker, with the library bind-mounted read-only |
| 6 | LXC container: AzuraCast |
| 7 | Navidrome |
| 8 | Radio track logging — ICY metadata scanners, plus `flowscan` for stations that don't publish over ICY |
| 9 | Tailscale — subnet router, for a LAN behind CGNAT with no inbound path |
| 10–11 | Verification checklist and rebuild gotchas |

## `flowscan.py`

A small stdlib-only poller for stations whose ICY `StreamTitle` is just the station name. It reads the web player's own metadata API instead and appends each track change to a log. Covered in section 8c; runs as a systemd template unit where the instance name is the station ID.

## Placeholders

Values specific to my setup are replaced — substitute your own:

- `youruser` — the Linux/Samba account (uid 1000)
- `YOUR_TAILNET_IP` — Tailscale 100.x address
- `your-tailnet` — tailnet name in the MagicDNS hostname
- `your-tailscale-account` — the account the node logs in as

LAN addressing (`192.168.8.x`) is left concrete; adjust it to your own network.
